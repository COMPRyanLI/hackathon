"""External flags importer: untrusted leads, never guidance or ground truth.

Rows are stored verbatim with their hashes. Sides are linked to submissions
only by exact source text (outer whitespace trimmed); score/grader agreement
is reported afterwards as a metadata check, never used to establish identity.
Assignment hints parsed from notes stay unconfirmed. Imported prose is never
placed in prompts or guidance.
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.db.models import AssessmentVersion, ScoreRecord, Student, Submission
from app.db.models_p1 import ExternalFlag, ExternalFlagLink
from app.extraction.facts import artifact_facts

TEXT_COLUMNS = ("text_a", "text_b")


def _similarity(raw: str) -> float | None:
    try:
        return float(raw) if raw.strip() else None
    except ValueError:
        return None


def _norm(text: str) -> str:
    """Exact text after normalising line endings and trimming outer whitespace only."""
    return chr(10).join(text.splitlines()).strip()


def _index(session: Session, dataset_key: str) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    """Exact-text indexes over current artifacts and concatenated bundles."""
    files: dict[str, list[dict]] = {}
    bundles: dict[str, list[dict]] = {}
    rows = session.execute(select(Submission, AssessmentVersion, Student)
                           .join(AssessmentVersion, AssessmentVersion.id == Submission.assessment_id)
                           .join(Student, Student.id == Submission.student_id)
                           .where(AssessmentVersion.dataset_key == dataset_key)).all()
    for sub, assessment, student in rows:
        ref = {"assessment_id": assessment.id, "assignment": assessment.name, "submission_id": sub.id,
               "student_id": student.code, "bundle_files": len(sub.artifacts)}
        for artifact in sub.artifacts:
            files.setdefault(_norm(artifact.source_text), []).append({**ref, "artifact_id": artifact.id})
        for joiner in ("\n", ""):
            joined = _norm(joiner.join(_norm(a.source_text) for a in sub.artifacts))
            bundles.setdefault(joined, []).append(ref)
    return files, bundles


def _link(text: str, files: dict[str, list[dict]], bundles: dict[str, list[dict]]) -> tuple[str, list[dict]]:
    key = _norm(text)
    if not key:
        return "unresolved", []
    bundle_hits = {(b["assessment_id"], b["submission_id"]): b for b in bundles.get(key, [])}
    if len(bundle_hits) == 1:
        return "exact_bundle", list(bundle_hits.values())
    file_hits = {(f["assessment_id"], f["submission_id"]): f for f in files.get(key, [])}
    if len(bundle_hits) > 1 or len(file_hits) > 1:
        return "ambiguous", list((bundle_hits or file_hits).values())
    if len(file_hits) == 1:
        hit = next(iter(file_hits.values()))
        return ("exact_bundle" if hit["bundle_files"] == 1 else "exact_file_in_larger_bundle"), [hit]
    return "unresolved", []


def _metadata_check(session: Session, hit: dict, score_raw: str, grader: str) -> dict[str, Any]:
    assessment = session.get(AssessmentVersion, hit["assessment_id"])
    record = session.execute(select(ScoreRecord).join(Student, Student.id == ScoreRecord.student_id).where(
        ScoreRecord.batch_id == assessment.batch_id, ScoreRecord.assessment_column == assessment.name,
        Student.code == hit["student_id"])).scalar()
    if record is None:
        return {"status": "no_gradebook_row"}
    try:
        score_agrees = record.score is not None and float(score_raw) == record.score
    except ValueError:
        score_agrees = False
    return {"status": "agrees" if score_agrees and (record.grader_label or "") == grader else "disagrees",
            "note": "Validates record linkage only; not the flag's explanation."}


def _note_checks(flag: ExternalFlag, side_hits: dict[str, list[dict]], session: Session) -> list[dict[str, Any]]:
    checks = []
    note = flag.rubric_note or ""
    if re.search(r"neither program has (an )?(author )?header", note, re.I):
        for side, hits in side_hits.items():
            if len(hits) != 1:
                continue
            sub = session.get(Submission, hits[0]["submission_id"])
            for artifact in sub.artifacts:
                header = artifact_facts(artifact.source_text)["header"]
                if header["present"]:
                    checks.append({"kind": "note_contradicted_by_source", "side": side,
                                   "detail": f"Side {side} has a comment header on lines {header['lines']}"
                                             + (" (anonymized with [NAME], which still shows a header exists)"
                                                if header["placeholder"] else "")})
                    break
    if re.search(r"\blate\b|lateness|dates? in comments", note, re.I):
        checks.append({"kind": "speculation_not_evidence",
                       "detail": "Dates in comments are not submission timestamps; lateness is not established."})
    if re.search(r"screenshot", note, re.I):
        checks.append({"kind": "external_evidence_claim",
                       "detail": "Screenshots are not part of this export; the claim cannot be checked here."})
    return checks


def import_flags(session: Session, path: Path, dataset_key: str, actor: str) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"Flags file not found: {path}")
    data = path.read_bytes()
    sha = audit.sha256_bytes(data)
    existing = session.scalar(select(ExternalFlag.id).where(ExternalFlag.import_sha256 == sha).limit(1))
    if existing is None:
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""))
        for number, row in enumerate(reader, start=2):
            fields = {k: v for k, v in row.items() if k not in TEXT_COLUMNS}
            hint = re.search(r"\b(SA\d+)\b", row.get("rubric_note", "") or "")
            session.add(ExternalFlag(
                import_sha256=sha, source_path=str(path), row_number=number,
                row_sha256=audit.stable_hash(row), original_flag_id=row.get("flag_id", "") or "",
                fields=fields, text_a=row.get("text_a", "") or "", text_b=row.get("text_b", "") or "",
                similarity=_similarity(row.get("similarity", "") or ""), similarity_raw=row.get("similarity", "") or "",
                rubric_note=row.get("rubric_note", "") or "", explanation=row.get("explanation", "") or "",
                assignment_hint=hint.group(1) if hint else None, actor=actor))
        session.flush()
    flags = list(session.scalars(select(ExternalFlag).where(ExternalFlag.import_sha256 == sha)
                                 .order_by(ExternalFlag.row_number)))
    files, bundles = _index(session, dataset_key)
    summary: dict[str, int] = {}
    for flag in flags:
        session.query(ExternalFlagLink).filter(ExternalFlagLink.flag_id == flag.id).delete()
        side_hits = {}
        for side, text, score_key, grader_key in (("a", flag.text_a, "score_a", "grader_a"),
                                                  ("b", flag.text_b, "score_b", "grader_b")):
            status, hits = _link(text, files, bundles)
            side_hits[side] = hits if status in ("exact_bundle", "exact_file_in_larger_bundle") else []
            check = (_metadata_check(session, hits[0], flag.fields.get(score_key, ""), flag.fields.get(grader_key, ""))
                     if len(hits) == 1 else {})
            session.add(ExternalFlagLink(flag_id=flag.id, side=side, status=status, candidates=hits,
                                         metadata_check=check, actor=actor))
            summary[status] = summary.get(status, 0) + 1
        flag.checks = _note_checks(flag, side_hits, session)
    session.flush()
    audit.record(session, actor, "external_flags_imported", "external_flags", None, sha256=sha, rows=len(flags),
                 link_summary=summary)
    return {"import_sha256": sha, "rows": len(flags), "link_summary": summary,
            "note": "Unverified imported leads. Not training labels, approved guidance or ground truth."}
