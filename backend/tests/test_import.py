"""Acceptance tests 1-4 and 7: identity, grouping, nulls, idempotence, anonymization flags."""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.config import PROJECT_ROOT
from app.db.models import Artifact, ArtifactAlias, ScoreRecord, Student, Submission
from app.db.session import session_scope
from app.extraction.static import extract
from app.importers.service import build_preview
from tests.conftest import PRINT3, REGEX, import_dataset, write_dataset


def test_suffix_maps_to_same_student_and_s001_does_not_match_s0010(tmp_path: Path, db):
    csv_path, src = write_dataset(
        tmp_path / "d",
        [("S000", "G1", 10), ("S001", "G1", 9), ("S002", "G2", 8)],
        {"S000_logo.py": PRINT3.format(a="x", b="y", c="z"),
         "S000_anything.py": PRINT3.format(a="p", b="q", c="r"),
         "S001_logo.py": PRINT3.format(a="1", b="2", c="3"),
         "S0010_logo.py": PRINT3.format(a="4", b="5", c="6"),
         "notes.txt": "not python"})
    preview = build_preview(csv_path, src, REGEX, "t", "SA1")
    assert sorted(f.relative_path for f in preview.files_by_student["S000"]) == ["S000_anything.py", "S000_logo.py"]
    assert [f.relative_path for f in preview.files_by_student["S001"]] == ["S001_logo.py"]
    kinds = {(i["kind"], i.get("path")) for i in preview.issues}
    assert ("unknown_student_id", "S0010_logo.py") in kinds          # S0010 is not S001
    assert ("filename_pattern_mismatch", "notes.txt") in kinds
    assert preview.report["matching"]["students_without_files"] == ["S002"]
    assert "not non-submission" in preview.report["matching"]["students_without_files_note"]


def test_leading_zeros_preserved_exactly(tmp_path: Path, db):
    csv_path, src = write_dataset(tmp_path / "d", [("S01", "G", 5), ("S1", "G", 6)],
                                  {"S01_a.py": "print('a')\n", "S1_b.py": "print('b')\n"})
    preview = build_preview(csv_path, src, REGEX, "t", "SA1")
    assert set(preview.files_by_student) == {"S01", "S1"}
    assert [f.relative_path for f in preview.files_by_student["S01"]] == ["S01_a.py"]


def test_multiple_files_grouped_and_identical_copies_deduplicated(tmp_path: Path, db):
    same = PRINT3.format(a="a", b="b", c="c")
    csv_path, src = write_dataset(
        tmp_path / "d", [("S003", "G", 10), ("S004", "G", 9)],
        {"S003_logo.py": same, "S003_logo_2.py": same,
         "S004_logo.py": PRINT3.format(a="m", b="n", c="o"), "S004_mystery.py": "import random\nprint(1)\n"})
    import_dataset(csv_path, src)
    with session_scope() as s:
        subs = {st.code: sub for sub, st in s.execute(
            select(Submission, Student).join(Student, Student.id == Submission.student_id)).all()}
        s003, s004 = subs["S003"], subs["S004"]
        assert len(s003.artifacts) == 1
        assert sorted(a.relative_path for a in s003.artifacts[0].aliases) == ["S003_logo.py", "S003_logo_2.py"]
        assert s003.component_scope_status == "single_artifact"
        assert len(s004.artifacts) == 2
        assert s004.component_scope_status == "component_scope_unconfirmed"
        # Artifact order follows content hash, never file name.
        hashes = [a.content_sha256 for a in s004.artifacts]
        assert hashes == sorted(hashes)


def test_null_scores_stay_null_zero_stays_zero_and_import_is_idempotent(tmp_path: Path, db):
    csv_path, src = write_dataset(tmp_path / "d", [("S001", "G", ""), ("S002", "", 0), ("S003", "G", "9.5")],
                                  {"S001_x.py": "print(1)\n", "S002_x.py": "print(2)\n", "S003_x.py": "print(3)\n"})
    batch1, a1, created1 = import_dataset(csv_path, src)
    batch2, a2, created2 = import_dataset(csv_path, src)
    assert created1 and not created2 and (batch1, a1) == (batch2, a2)
    with session_scope() as s:
        records = {st.code: r for r, st in s.execute(
            select(ScoreRecord, Student).join(Student, Student.id == ScoreRecord.student_id)).all()}
        assert records["S001"].score is None and records["S001"].status == "blank"
        assert records["S002"].score == 0.0 and records["S002"].status == "scored"
        assert records["S002"].grader_label is None            # missing grader kept separate
        assert records["S003"].score == 9.5 and records["S003"].raw_cell == "9.5"
        assert s.scalar(select(func.count(ScoreRecord.id))) == 3
        assert s.scalar(select(func.count(ArtifactAlias.id))) == 3


def test_name_placeholder_in_identifier_is_anonymization_flag_not_student_error():
    result = extract('# Author: [NAME]\ndef [NAME]_W():\n    print("hi")\n\n[NAME]_W()\nprint("[NAME] [NAME]")\n')
    assert result.parse_status == "syntax_error"
    kinds = [(i.kind, i.line) for i in result.issues]
    assert ("possible_anonymization_artifact", 2) in kinds
    assert ("possible_anonymization_artifact", 5) in kinds
    assert not any(k == "syntax_error" for k, _ in kinds)
    assert ("anonymization_placeholder", 6) in kinds          # placeholder inside a string
    assert ("anonymization_placeholder", 1) in kinds          # placeholder inside a comment


def test_source_preserved_exactly_including_crlf_and_literals(tmp_path: Path, db):
    text = 'print("  * *  ")\r\nprint("\\\\ / ")\r\n'
    csv_path, src = write_dataset(tmp_path / "d", [("S001", "G", 10)], {"S001_logo.py": text})
    import_dataset(csv_path, src)
    with session_scope() as s:
        artifact = s.scalar(select(Artifact))
        assert artifact.source_text == text
        assert "".join(c.text for c in artifact.chunks) == text


REAL_CSV = PROJECT_ROOT / "cs1_scores_anon.csv"
REAL_SA1 = PROJECT_ROOT / "SA1"


PRIVATE_EXPECTED = PROJECT_ROOT / "data/private/sa1_expected_snapshots.json"


@pytest.mark.skipif(not (REAL_CSV.is_file() and REAL_SA1.is_dir() and PRIVATE_EXPECTED.is_file()),
                    reason="private snapshot/expected inventory not present")
def test_real_snapshot_matches_a_recorded_snapshot_version():
    """Keep real class inventories in an ignored local fixture, never in the public repository."""
    import json
    private = json.loads(PRIVATE_EXPECTED.read_text(encoding="utf-8"))
    preview = build_preview(REAL_CSV, REAL_SA1, REGEX, "cs1", "SA1")
    r = preview.report
    expected = private["snapshots"].get(preview.manifest_sha256)
    assert expected is not None, "Unrecorded export: record a reviewed expected inventory locally before relying on it."
    assert r["csv"]["sha256"].upper() == private["csv_sha256"]
    assert r["csv"]["unique_student_ids"] == private["unique_students"]
    assert r["csv"]["nonempty_grader_labels"] == private["graders"]
    assert r["csv"]["rows_with_blank_grader"] == private["rows_without_grader"]
    assert r["csv"]["scores_populated_all_assessments"] == private["populated_scores"]
    assert r["csv"]["scores_blank_all_assessments"] == private["blank_scores"]
    assert r["assessment"]["scores_populated"] == private["sa1_scored"]
    assert r["assessment"]["score_value_counts"]["10.0"] == private["sa1_tens"]
    assert r["files"]["matched_files"] == expected["matched_files"]
    assert r["files"]["total_bytes"] == expected["total_bytes"]
    assert [r["files"]["size_min"], r["files"]["size_max"]] == expected["size"]
    assert r["matching"]["students_with_files"] == expected["students"]
    assert r["matching"]["files_per_student"] == expected["per_student"]
    assert r["matching"]["multi_file_students"] == private["multi_file_students"]
    assert r["matching"]["students_without_files"] == expected["without_files"]
    assert r["matching"]["matched_without_score"] == []
    assert [d["student_id"] for d in r["matching"]["within_bundle_identical_files"]] == private["identical_within"]
    assert any(g["student_ids"] == private["shared_pair"] for g in r["matching"]["cross_student_identical_files"])
    assert any(f["student_id"] == private["anonymization_case"] for f in r["quality"]["possible_anonymization_artifacts"])
    assert all(i["severity"] != "error" for i in preview.issues)
