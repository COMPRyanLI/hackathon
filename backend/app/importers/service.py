"""Import preview and commit.

Identity rule: the configurable full-match regex extracts a student ID from the
file name; that ID must equal a CSV ``student_id`` string exactly. Everything
after the ID (``_logo``, ``_mystery``, ...) is ignored for identity, ordering,
retrieval and prompts, but the original relative path is kept as private
provenance. Unknown or ambiguous IDs fail closed (reported, not imported).
"""
from __future__ import annotations

import csv
import io
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.db.models import (
    Artifact,
    ArtifactAlias,
    AssessmentVersion,
    Chunk,
    ExtractionIssue,
    ImportBatch,
    ScoreRecord,
    Student,
    Submission,
)
from app.extraction.static import PARSER_VERSION, decode_source, extract, line_count

ASSUMPTIONS = [
    "Each CSV row's grader label is assumed to identify the grader of every assessment in that row "
    "(working assumption approved by the user; recorded as provenance).",
    "Filename text after the student ID is ignored for identity, ordering, retrieval and prompts. "
    "No task, quality, chronology or grading rule is derived from it.",
    "Students without files are reported as unavailable in this upload, not as non-submission or zero credit.",
    "The observed maximum score is not proof of the maximum possible points.",
    "Blank score cells stay null; they are never converted to zero.",
    "Row order, student IDs and file modification times are not used as grading chronology.",
]


class ImportInputError(ValueError):
    """Raised for unusable import inputs (bad paths, missing columns)."""


def source_assignment_warning(source_root: str | Path, assessment_name: str) -> str | None:
    folder = Path(source_root).name.upper()
    if re.fullmatch(r"SA\d+", folder) and re.fullmatch(r"SA\d+", assessment_name.upper()) and folder != assessment_name.upper():
        return (f"Import mismatch: source folder is {folder}, but the selected score column is {assessment_name}. "
                "Use the matching folder and assessment before reviewing grades. Existing records are preserved.")
    return None


@dataclass
class CsvRowData:
    row_number: int
    student_code: str
    grader_raw: str
    cells: dict[str, str]


@dataclass
class FileData:
    relative_path: str
    student_code: str
    data: bytes
    sha256: str


@dataclass
class PreviewData:
    dataset_key: str
    assessment_name: str
    csv_path: Path
    source_root: Path
    filename_regex: str
    csv_sha256: str
    manifest_sha256: str
    header: list[str]
    rows: list[CsvRowData]
    files_by_student: dict[str, list[FileData]]
    report: dict[str, Any]
    issues: list[dict[str, Any]] = field(default_factory=list)


def _parse_score(raw: str) -> tuple[float | None, str]:
    if raw.strip() == "":
        return None, "blank"
    try:
        return float(raw), "scored"
    except ValueError:
        return None, "unparseable"


def read_scores_csv(csv_path: Path) -> tuple[bytes, list[str], list[CsvRowData]]:
    if not csv_path.is_file():
        raise ImportInputError(f"CSV not found: {csv_path}")
    data = csv_path.read_bytes()
    text = data.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(reader)
    except StopIteration as exc:
        raise ImportInputError("CSV is empty") from exc
    if "student_id" not in header or "grader" not in header:
        raise ImportInputError("CSV must contain 'student_id' and 'grader' columns")
    sid_index = header.index("student_id")
    grader_index = header.index("grader")
    rows = []
    for row_number, row in enumerate(reader, start=2):
        if not any(cell.strip() for cell in row):
            continue
        row = row + [""] * (len(header) - len(row))
        cells = {name: row[i] for i, name in enumerate(header) if i not in (sid_index, grader_index)}
        rows.append(CsvRowData(row_number, row[sid_index], row[grader_index], cells))
    return data, header, rows


def scan_source_files(source_root: Path, pattern: re.Pattern[str]) -> tuple[list[FileData], list[dict[str, Any]]]:
    """Walk the import root without following links outside it."""
    if not source_root.is_dir():
        raise ImportInputError(f"Submission folder not found: {source_root}")
    root = source_root.resolve()
    files: list[FileData] = []
    rejected: list[dict[str, Any]] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in sorted(filenames):
            path = Path(dirpath) / name
            resolved = path.resolve()
            if root not in resolved.parents and resolved != root:
                rejected.append({"path": name, "reason": "outside_import_root"})
                continue
            relative = resolved.relative_to(root).as_posix()
            match = pattern.fullmatch(name)
            if not match:
                rejected.append({"path": relative, "reason": "filename_pattern_mismatch"})
                continue
            data = resolved.read_bytes()
            files.append(FileData(relative, match.group("student_id"), data, audit.sha256_bytes(data)))
    return files, rejected


def build_preview(csv_path: Path, source_root: Path, filename_regex: str, dataset_key: str,
                  assessment_name: str) -> PreviewData:
    mismatch = source_assignment_warning(source_root, assessment_name)
    if mismatch:
        raise ImportInputError(mismatch)
    try:
        pattern = re.compile(filename_regex)
    except re.error as exc:
        raise ImportInputError(f"Invalid filename regex: {exc}") from exc
    if "student_id" not in pattern.groupindex:
        raise ImportInputError("Filename regex needs a named group 'student_id'")

    csv_bytes, header, rows = read_scores_csv(csv_path)
    if assessment_name not in header:
        raise ImportInputError(f"CSV has no column {assessment_name!r}")
    files, rejected = scan_source_files(source_root, pattern)

    issues: list[dict[str, Any]] = []
    id_counts = Counter(row.student_code for row in rows)
    duplicate_ids = sorted(code for code, n in id_counts.items() if n > 1)
    for code in duplicate_ids:
        issues.append({"kind": "duplicate_csv_id", "severity": "error", "student_id": code,
                       "message": "Student ID appears in more than one CSV row; files for it are not imported."})
    for row in rows:
        if row.student_code != row.student_code.strip() or row.student_code == "":
            issues.append({"kind": "csv_id_whitespace_or_empty", "severity": "error", "row": row.row_number,
                           "message": "Student ID cell is empty or has surrounding whitespace; exact matching fails closed."})
    csv_ids = {row.student_code for row in rows}

    files_by_student: dict[str, list[FileData]] = defaultdict(list)
    unknown_files = []
    ambiguous_files = []
    for f in files:
        if f.student_code in duplicate_ids:
            ambiguous_files.append(f.relative_path)
        elif f.student_code in csv_ids:
            files_by_student[f.student_code].append(f)
        else:
            unknown_files.append({"path": f.relative_path, "extracted_id": f.student_code})
    for item in unknown_files:
        issues.append({"kind": "unknown_student_id", "severity": "error", **item,
                       "message": "Extracted ID has no exact CSV match; file not imported."})
    for path in ambiguous_files:
        issues.append({"kind": "ambiguous_student_id", "severity": "error", "path": path,
                       "message": "Extracted ID is ambiguous; file not imported."})
    for item in rejected:
        issues.append({"kind": item["reason"], "severity": "warning", "path": item["path"],
                       "message": "File not imported."})

    # Score/grader statistics.
    assessment_columns = [c for c in header if c not in ("student_id", "grader")]
    populated = blank = unparseable = 0
    for row in rows:
        for column in assessment_columns:
            _, status = _parse_score(row.cells.get(column, ""))
            populated += status == "scored"
            blank += status == "blank"
            unparseable += status == "unparseable"
    target_scores = {row.student_code: _parse_score(row.cells.get(assessment_name, "")) for row in rows}
    target_values = Counter(v for v, s in target_scores.values() if s == "scored")
    graders = Counter(row.grader_raw for row in rows if row.grader_raw.strip())

    matched = sorted(files_by_student)
    sizes = [len(f.data) for fs in files_by_student.values() for f in fs]
    content_students: dict[str, set[str]] = defaultdict(set)
    within_bundle_dupes = []
    for code, fs in files_by_student.items():
        counts = Counter(f.sha256 for f in fs)
        for sha, n in counts.items():
            content_students[sha].add(code)
            if n > 1:
                within_bundle_dupes.append({"student_id": code, "copies": n,
                                            "paths": sorted(f.relative_path for f in fs if f.sha256 == sha)})
    cross_student = [
        {"content_sha256": sha[:12], "student_ids": sorted(codes)}
        for sha, codes in content_students.items() if len(codes) > 1
    ]

    # Static extraction for the data-quality report (fast; no models).
    parse_failures = []
    anonymization_flags = []
    identifying_flags = []
    for code, fs in sorted(files_by_student.items()):
        for f in fs:
            text, _, _ = decode_source(f.data)
            result = extract(text)
            if result.parse_status != "ok":
                parse_failures.append({"student_id": code, "path": f.relative_path,
                                       "line": (result.parse_error or {}).get("line")})
            for issue in result.issues:
                if issue.kind == "possible_anonymization_artifact":
                    anonymization_flags.append({"student_id": code, "path": f.relative_path, "line": issue.line})
                elif issue.kind == "possible_identifying_text":
                    identifying_flags.append({"student_id": code, "path": f.relative_path, "line": issue.line})

    files_per_student = Counter(len(fs) for fs in files_by_student.values())
    report = {
        "csv": {
            "path": str(csv_path),
            "sha256": audit.sha256_bytes(csv_bytes),
            "columns": header,
            "rows": len(rows),
            "unique_student_ids": len(csv_ids),
            "duplicate_ids": duplicate_ids,
            "nonempty_grader_labels": len(graders),
            "rows_with_blank_grader": sum(1 for row in rows if not row.grader_raw.strip()),
            "scores_populated_all_assessments": populated,
            "scores_blank_all_assessments": blank,
            "scores_unparseable_all_assessments": unparseable,
        },
        "assessment": {
            "name": assessment_name,
            "scores_populated": sum(1 for _, s in target_scores.values() if s == "scored"),
            "scores_blank": sum(1 for _, s in target_scores.values() if s == "blank"),
            "score_value_counts": {str(k): v for k, v in sorted(target_values.items())},
            "observed_max": max(target_values) if target_values else None,
            "observed_max_note": "Observed maximum is not proof of maximum possible points.",
        },
        "files": {
            "source_root": str(source_root),
            "matched_files": sum(len(fs) for fs in files_by_student.values()),
            "total_bytes": sum(sizes),
            "size_min": min(sizes) if sizes else None,
            "size_max": max(sizes) if sizes else None,
            "rejected_files": len(rejected),
            "unknown_id_files": len(unknown_files),
            "ambiguous_id_files": len(ambiguous_files),
            "filename_regex": filename_regex,
        },
        "matching": {
            "students_with_files": len(matched),
            "files_per_student": {str(k): v for k, v in sorted(files_per_student.items())},
            "multi_file_students": sorted(c for c, fs in files_by_student.items() if len(fs) > 1),
            "students_without_files": sorted(csv_ids - set(matched) - set(duplicate_ids)),
            "students_without_files_note": "Unavailable in this upload; not non-submission or zero credit.",
            "matched_without_score": sorted(c for c in matched if target_scores[c][1] != "scored"),
            "within_bundle_identical_files": within_bundle_dupes,
            "cross_student_identical_files": cross_student,
            "cross_student_note": "Artifact observation only; not evidence of misconduct or equivalent submissions.",
        },
        "quality": {
            "parse_failures": parse_failures,
            "possible_anonymization_artifacts": anonymization_flags,
            "possible_identifying_text": identifying_flags,
        },
        "assumptions": ASSUMPTIONS,
    }
    manifest = audit.stable_hash(sorted((f.relative_path, f.sha256) for f in files))
    return PreviewData(dataset_key, assessment_name, csv_path, source_root, filename_regex,
                       audit.sha256_bytes(csv_bytes), manifest, header, rows, dict(files_by_student),
                       report, issues)


def preview_payload(preview: PreviewData) -> dict[str, Any]:
    students = []
    for row in preview.rows:
        fs = preview.files_by_student.get(row.student_code, [])
        score, status = _parse_score(row.cells.get(preview.assessment_name, ""))
        students.append({
            "student_id": row.student_code,
            "grader": row.grader_raw or None,
            "score_raw": row.cells.get(preview.assessment_name, ""),
            "score": score,
            "score_status": status,
            "files": [f.relative_path for f in fs],
            "distinct_contents": len({f.sha256 for f in fs}),
        })
    return {"report": preview.report, "issues": preview.issues, "students": students}


def commit_import(session: Session, preview: PreviewData, actor: str, is_synthetic: bool = False,
                  context: dict[str, Any] | None = None) -> tuple[ImportBatch, AssessmentVersion, bool]:
    """Write an import. Re-committing identical inputs returns the existing batch."""
    existing = session.scalar(select(ImportBatch).where(
        ImportBatch.dataset_key == preview.dataset_key,
        ImportBatch.assessment_name == preview.assessment_name,
        ImportBatch.csv_sha256 == preview.csv_sha256,
        ImportBatch.source_manifest_sha256 == preview.manifest_sha256,
        ImportBatch.filename_regex == preview.filename_regex,
    ))
    if existing is not None:
        assessment = session.scalar(select(AssessmentVersion).where(AssessmentVersion.batch_id == existing.id))
        return existing, assessment, False

    batch = ImportBatch(
        dataset_key=preview.dataset_key, assessment_name=preview.assessment_name,
        csv_path=str(preview.csv_path), csv_sha256=preview.csv_sha256,
        source_root=str(preview.source_root), source_manifest_sha256=preview.manifest_sha256,
        filename_regex=preview.filename_regex, is_synthetic=is_synthetic,
        summary=preview.report, assumptions=ASSUMPTIONS, actor=actor,
    )
    session.add(batch)
    session.flush()

    students: dict[str, Student] = {}
    for row in preview.rows:
        student = session.scalar(select(Student).where(
            Student.dataset_key == preview.dataset_key, Student.code == row.student_code))
        if student is None:
            student = Student(dataset_key=preview.dataset_key, code=row.student_code, first_batch_id=batch.id)
            session.add(student)
            session.flush()
        students[row.student_code] = student
        grader = row.grader_raw if row.grader_raw.strip() else None
        for column, raw in row.cells.items():
            score, status = _parse_score(raw)
            session.add(ScoreRecord(batch_id=batch.id, student_id=student.id, assessment_column=column,
                                    raw_cell=raw, score=score, status=status, grader_label=grader,
                                    grader_raw_cell=row.grader_raw, csv_row_number=row.row_number))

    version = (session.scalar(select(func.max(AssessmentVersion.version)).where(
        AssessmentVersion.dataset_key == preview.dataset_key,
        AssessmentVersion.name == preview.assessment_name)) or 0) + 1
    assessment = AssessmentVersion(dataset_key=preview.dataset_key, name=preview.assessment_name,
                                   version=version, batch_id=batch.id, is_synthetic=is_synthetic)
    if context:
        for key, value in context.items():
            setattr(assessment, key, value)
    from app.analysis.context import refresh_context_hash
    refresh_context_hash(assessment)
    session.add(assessment)
    session.flush()

    content_students: dict[str, set[str]] = defaultdict(set)
    for code, fs in preview.files_by_student.items():
        for f in fs:
            content_students[f.sha256].add(code)

    for code, fs in sorted(preview.files_by_student.items()):
        by_hash: dict[str, list[FileData]] = defaultdict(list)
        for f in fs:
            by_hash[f.sha256].append(f)
        ordered_hashes = sorted(by_hash)  # content-hash order; never filename/suffix order
        submission = Submission(
            assessment_id=assessment.id, student_id=students[code].id, batch_id=batch.id,
            bundle_hash=audit.stable_hash(ordered_hashes),
            component_scope_status="single_artifact" if len(ordered_hashes) == 1 else "component_scope_unconfirmed",
        )
        session.add(submission)
        session.flush()
        for position, sha in enumerate(ordered_hashes):
            first = by_hash[sha][0]
            text, encoding, decode_issues = decode_source(first.data)
            result = extract(text)
            artifact = Artifact(
                submission_id=submission.id, position=position, content_sha256=sha,
                size_bytes=len(first.data), line_count=line_count(text), encoding=encoding,
                source_text=text, parse_status=result.parse_status, parse_error=result.parse_error,
                parser_version=PARSER_VERSION, features=result.features,
                cross_student_copies=len(content_students[sha]) - 1,
            )
            session.add(artifact)
            session.flush()
            for f in by_hash[sha]:
                session.add(ArtifactAlias(artifact_id=artifact.id, relative_path=f.relative_path,
                                          file_sha256=f.sha256, size_bytes=len(f.data)))
            for chunk in result.chunks:
                session.add(Chunk(artifact_id=artifact.id, chunk_key=chunk.key, start_line=chunk.start_line,
                                  end_line=chunk.end_line, text=chunk.text, sha256=audit.sha256_text(chunk.text)))
            for issue in decode_issues + result.issues:
                session.add(ExtractionIssue(artifact_id=artifact.id, kind=issue.kind, severity=issue.severity,
                                            line=issue.line, message=issue.message, detail=issue.detail))
            if artifact.cross_student_copies:
                session.add(ExtractionIssue(
                    artifact_id=artifact.id, kind="shared_content_candidate", severity="info", line=None,
                    message=f"Identical content appears in {artifact.cross_student_copies} other student bundle(s). "
                            "Could be shared/reference material; classification needs confirmation. "
                            "Not evidence of misconduct.",
                    detail={"other_bundles": artifact.cross_student_copies}))

    audit.record(session, actor, "import_committed", "import_batch", batch.id,
                 assessment_id=assessment.id, csv_sha256=preview.csv_sha256,
                 manifest_sha256=preview.manifest_sha256, synthetic=is_synthetic)
    return batch, assessment, True
