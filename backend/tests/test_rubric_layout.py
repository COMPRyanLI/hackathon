"""Regression coverage for deduction scope, parser upgrades and import mixups."""
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.models import AssessmentVersion, Submission
from app.db.models_p1 import DraftFeedback, DraftGradeRevision, RubricCriterion, RubricDocument
from app.db.session import session_scope
from app.importers.service import ImportInputError, build_preview, source_assignment_warning
from app.rubrics import service
from app.rubrics.parse import parse_guide
from app.rubrics.validate import validate
from tests.test_p1_acceptance import env, draft


def test_dedented_award_ends_deduction_scope_without_arithmetic_guessing():
    text = '''SA4 (synthetic drawing) (10 points):
2 points: For good coding practice
7 points: Correctness. Deduct points as follows:
    1 point: If lines are missing
    1 point: If endpoints are incorrect
1 point: A separate presentation criterion
'''
    parsed = parse_guide([text])
    awards = [c for c in parsed.criteria if c.parent_key == "SA4" and c.rule_kind == "award"]
    assert [c.max_points for c in awards] == [Decimal(2), Decimal(7), Decimal(1)]
    assert not [i for i in validate(parsed) if i.kind == "arithmetic_conflict"]
    deductions = [c for c in parsed.criteria if c.rule_kind == "deduction"]
    assert len(deductions) == 2 and all(c.parent_key != "SA4" for c in deductions)
    # A nested item must not be promoted simply to force the total to ten.
    nested = parse_guide([text.replace("\n1 point: A separate", "\n    1 point: A separate")])
    assert any(i.kind == "arithmetic_conflict" for i in validate(nested))


def test_sa4_legacy_flat_guide_retains_separate_award_and_warning():
    from app.importers.demo_p1 import P1_DIR
    parsed = parse_guide([(P1_DIR/"guide.txt").read_text(encoding="utf-8")])
    window = next(c for c in parsed.criteria if c.assignment=="SA4" and "window_size" in c.key
                  and c.rule_kind != "warning")
    assert window.rule_kind == "award" and window.parent_key == "SA4" and window.max_points == Decimal(1)
    assert any(c.parent_key==window.key and c.rule_kind=="warning" and c.max_points==0 for c in parsed.criteria)
    assert not [i for i in validate(parsed) if i.assignment=="SA4" and i.kind in ("arithmetic_conflict","structure_ambiguous")]
    assert any(i.assignment=="SA5" and i.kind=="arithmetic_conflict" for i in validate(parsed))


def test_reparse_keeps_history_and_link_refreshes_existing_feedback(env, monkeypatch):
    draft(env, "P001")
    with session_scope() as s:
        from app.db.models_p1 import RubricVersion
        old = s.get(RubricVersion, env["rubric"])
        old_hash = old.content_hash
        old.parser_version = "guide-parse-v1"
        updated, created = service.reparse(s, old.id, "tester")
        assert created and updated.id != old.id and updated.version == old.version + 1
        assert old.content_hash == old_hash and old.parser_version == "guide-parse-v1"
        assert s.scalar(select(RubricCriterion.id).where(RubricCriterion.rubric_version_id==old.id))
        again, created = service.reparse(s, old.id, "tester")
        assert again.id == updated.id and not created
        before = [(r.id,r.total) for r in s.scalars(select(DraftGradeRevision))]
        service.link_assessment(s, env["sa3"], updated.id, "SA3", "tester")
        assert all(f.status=="stale" for f in s.scalars(select(DraftFeedback)))
        assert before == [(r.id,r.total) for r in s.scalars(select(DraftGradeRevision))]


def test_synthetic_guide_cannot_be_linked_to_real_assessment(env):
    with session_scope() as s:
        s.get(AssessmentVersion, env["sa3"]).is_synthetic = False
        with pytest.raises(service.RubricError, match="Synthetic and real"):
            service.link_assessment(s, env["sa3"], env["rubric"], "SA3", "tester")


def test_assignment_folder_mismatch_is_rejected_before_import(tmp_path):
    assert source_assignment_warning(tmp_path/"SA1", "SA3")
    assert source_assignment_warning(tmp_path/"SA3", "SA3") is None
    assert source_assignment_warning(tmp_path/"uploaded-submissions", "SA3") is None
    with pytest.raises(ImportInputError, match="source folder is SA1"):
        build_preview(tmp_path/"scores.csv",tmp_path/"SA1",r"(?P<student_id>S\d+)\.py", "test", "SA3")


def test_real_pdf_six_assignment_totals_and_sa4_deduction_scope():
    path = Path.home()/"Downloads"/"section_leader_assignment grading guide .pdf"
    if not path.exists():
        pytest.skip("private real guide unavailable")
    parsed = parse_guide(service.extract_pdf_pages(path))
    totals = {code:sum((c.max_points for c in parsed.criteria if c.parent_key==code and c.rule_kind=="award"),Decimal(0))
              for code in parsed.headings}
    assert totals == {"SA1":10,"SA2":10,"SA3":10,"SA4":10,"SA5":11,"SA6":10}
    assert [(i.assignment,i.kind) for i in validate(parsed) if i.kind=="arithmetic_conflict"] == [("SA5","arithmetic_conflict")]
