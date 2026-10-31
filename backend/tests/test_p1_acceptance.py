"""P1 acceptance scenarios (plan section 11). Synthetic fixtures only, except the
explicitly gated real-guide check. Scenario numbers are in each test name."""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.config import PROJECT_ROOT
from app.db.models import AnalysisRun, ScoreRecord, Student, Submission
from app.db.models_p1 import (
    CalibrationIssue,
    DraftFeedback,
    DraftGradeRevision,
    ExternalFlag,
    ExternalFlagLink,
    FeedbackCard,
    GradingEvent,
    IssueCaseLink,
    RubricIssue,
)
from app.db.session import init_engine, session_scope
from app.extraction.facts import artifact_facts, infer_roles, safe_token_ngrams, scalar_fact
from app.grading import dispositions
from app.grading.drafts import DraftConflict, DraftError, current_revision, draft_from_historical, save_draft
from app.importers.demo_p1 import P1_DIR, import_synthetic_p1
from app.jobs.worker import process_queued
from app.providers.registry import ProviderRegistry
from app.rubrics.engine import PointError, compare_with_draft, information_bound, to_decimal, treatment_proposal
from app.rubrics.parse import parse_guide
from app.rubrics.validate import validate
from tests.conftest import make_settings

INCLUSIVE = {"component": "program_1", "fact": "loop_inclusive_bound", "op": "==", "value": True}
EXCEPTION = {"description": "counter starts at 1", "condition": {"component": "program_1", "fact": "loop_counter_start",
                                                                 "op": "==", "value": 1}}


@pytest.fixture
def env(tmp_path: Path):
    settings = make_settings(tmp_path)
    init_engine(settings.effective_database_url)
    with session_scope() as s:
        demo = import_synthetic_p1(s, "tester")
    registry = ProviderRegistry(settings)
    sa3 = demo["assessments"]["SA3"]["assessment_id"]
    with session_scope() as s:
        subs = dict(s.execute(select(Student.code, Submission.id).join(Submission, Submission.student_id == Student.id)
                              .where(Submission.assessment_id == sa3)).all())
    return {"settings": settings, "registry": registry, "sa3": sa3, "subs": subs,
            "sa6": demo["assessments"]["SA6"]["assessment_id"], "rubric": demo["rubric_version_id"]}


def draft(env, code: str, total: str = "9.0", grader: str = "Grader A", points: dict | None = None,
          key: str | None = None, process: bool = True):
    with session_scope() as s:
        sid = env["subs"][code]
        cur = current_revision(s, sid)
        from app.api.routes_p1 import _criteria
        from app.db.models import AssessmentVersion

        rev, created, fb = save_draft(s, sid, total=total, grader_label=grader, actor="tester",
                                      expected_revision_id=cur.id if cur else None, criterion_points=points,
                                      idempotency_key=key, criteria=_criteria(s, s.get(AssessmentVersion, env["sa3"])))
        rid = rev.id
    if process:
        process_queued(env["registry"])
    return rid


def cards(env, code: str, card_type: str | None = None) -> list[FeedbackCard]:
    with session_scope() as s:
        rev = current_revision(s, env["subs"][code])
        fb = s.scalar(select(DraftFeedback).where(DraftFeedback.revision_id == rev.id).order_by(DraftFeedback.id.desc()))
        rows = list(s.scalars(select(FeedbackCard).where(FeedbackCard.feedback_id == fb.id)))
        return [c for c in rows if card_type is None or c.card_type == card_type]


def resolve_inclusive(env, issue_id: int):
    from app.calibration import issues

    with session_scope() as s:
        issue = s.get(CalibrationIssue, issue_id)
        issues.resolve(s, issue_id, actor="head-ta", expected_version=issue.version, resolution_type="interpretation",
                       text="Inclusive bound affects only final balance (0 of 1).", note_kind="clarification",
                       condition=INCLUSIVE, exception_conditions=[EXCEPTION],
                       point_treatment={"action": "award", "points": "0"}, provider_mode="mock")
    process_queued(env["registry"])


def request(env, code: str) -> int:
    card = cards(env, code, "policy_question")[0]
    with session_scope() as s:
        out = dispositions.respond(s, card.id, "request_calibration", "tester", revision_id=card.revision_id)
    return out["issue_id"]


# ---------------------------------------------------------------- source representations (1-6, 9, 11, 14)


def test_01_literal_hash_and_triple_quoted_data_survive_token_preprocessing():
    src = 'BANNER = """### TITLE ###"""\nprint("# not a comment")  # real comment\n'
    grams = " ".join(safe_token_ngrams(src))
    assert '"# not a comment"' in grams and '"""### TITLE ###"""' in grams and "# real comment" in grams


def test_02_03_raw_comments_feed_header_evidence_and_name_placeholder_is_not_missing_header():
    facts = artifact_facts("# Author: [NAME] [NAME]\n# Date: 9/1\nprint('x')\n")
    assert facts["header"]["present"] is True and facts["header"]["placeholder"] is True
    assert scalar_fact(artifact_facts("print('x')\n"), "header_comment_present") is False


def test_04_suffix_rename_changes_neither_identity_nor_roles():
    files = sorted((P1_DIR / "SA3").glob("P001_*.py"))
    roles_a = infer_roles("SA3", [(i, artifact_facts(f.read_text(encoding="utf-8"))) for i, f in enumerate(files)])
    renamed = list(reversed(files))  # different names/order must not matter: roles follow code
    roles_b = infer_roles("SA3", [(files.index(f), artifact_facts(f.read_text(encoding="utf-8"))) for f in renamed])
    assert sorted((r["artifact_id"], r["role"]) for r in roles_a) == sorted((r["artifact_id"], r["role"]) for r in roles_b)


def test_05_two_programs_stay_separate_and_single_file_with_both_is_ambiguous(env):
    with session_scope() as s:
        from app.importers.multi import current_roles

        p001 = s.get(Submission, env["subs"]["P001"])
        roles = sorted(m.role for m in current_roles(s, p001).values())
        assert roles == ["program_1", "program_2"]
        p010 = s.get(Submission, env["subs"]["P010"])
        assert [m.status for m in current_roles(s, p010).values()] == ["ambiguous"]


def test_06_07_08_shared_file_one_file_upload_and_missing_in_export_states(env):
    from app.importers.multi import build_manifest
    from app.db.models import AssessmentVersion
    from app.rubrics.service import assessment_rubric

    with session_scope() as s:
        a = s.get(AssessmentVersion, env["sa3"])
        manifest = build_manifest(s, a, assessment_rubric(s, a)["criteria"]).manifest
        assert manifest["submissions"]["P012"]["code"] == "not_in_current_export"
        assert all(row["code"] != "confirmed_not_submitted" for row in manifest["submissions"].values())
        assert manifest["submissions"]["P001"]["screenshot"] == "not_in_current_export"
        assert manifest["criteria"]["SA3.program_1.screenshot"]["default_evidence"] == "not_in_current_export"
        record = s.execute(select(ScoreRecord).join(Student).where(Student.code == "P012",
                                                                     ScoreRecord.assessment_column == "SA3")).scalar()
        assert record.score == 7.0  # a recorded score with no code: not zero, not 'not submitted'


def test_09_multiplier_and_percentage_forms_get_the_explicit_allowance(env):
    key = "SA3.program_2.defining_constants_initial_balances_interest"
    draft(env, "P009", "9.0", points={key: "0"})
    allowance = cards(env, "P009", "explicit_allowance")
    assert allowance and "1.05/1.04" in allowance[0].body
    assert not cards(env, "P009", "evidence_not_found")


def test_10_sa4_while_policy_never_leaks_into_sa3(env):
    draft(env, "P001")
    assert not [c for c in cards(env, "P001") if "while loop found" in c.title]


def test_11_pow_is_a_review_item_never_an_automatic_verdict(env):
    draft(env, "P008", "10.0")
    pow_cards = cards(env, "P008", "exceptional_policy")
    assert pow_cards and "no verdict is proposed" in pow_cards[0].body
    with session_scope() as s:
        assert s.scalar(select(func.count(CalibrationIssue.id))) == 0   # never auto-escalated
    rev = draft(env, "P007", "9.0")
    assert not cards(env, "P007", "exceptional_policy")


def test_14_anonymization_damage_yields_unknown_facts_not_false():
    facts = artifact_facts("# Author: [NAME]\ndef [NAME]_x():\n    for i in range(3):\n        print(i)\n")
    assert facts["parse_ok"] is False and scalar_fact(facts, "loop_inclusive_bound") is None


# ---------------------------------------------------------------- rubric (12, 13, 18, 24)


def _synthetic_guide():
    text = (P1_DIR / "guide.txt").read_text(encoding="utf-8")
    pages = [p.split("---", 1)[1] for p in text.split("--- PAGE") if p.strip()]
    return parse_guide(pages)


def test_12_sa5_conflict_blocks_totals_but_not_evidence():
    issues = validate(_synthetic_guide())
    sa5 = [i for i in issues if i.assignment == "SA5" and i.kind == "arithmetic_conflict"]
    assert sa5 and sa5[0].blocks_total_proposals and "11" in sa5[0].message
    parsed = _synthetic_guide()
    assert any(c.assignment == "SA5" and c.title.lower().startswith("for using callback") for c in parsed.criteria)


def test_13_practice_exemption_unresolved_for_sa3_only():
    kinds = {(i.assignment, i.kind) for i in validate(_synthetic_guide())}
    assert ("SA3", "exemption_unresolved") in kinds
    assert ("SA6", "exemption_unresolved") not in kinds


def test_18_sa6_partial_credit_is_unresolved_not_a_blanket_zero(env):
    issues = validate(_synthetic_guide())
    assert any(i.assignment == "SA6" and i.kind == "partial_credit_undefined" for i in issues)
    assert any(i.assignment == "SA6" and i.kind == "escalation_indicated" for i in issues)
    with session_scope() as s:
        from app.db.models import AssessmentVersion
        from app.rubrics.service import assessment_rubric

        rubric = assessment_rubric(s, s.get(AssessmentVersion, env["sa6"]))
        bound = information_bound(rubric["criteria"], "SA6", {}, rubric["blocking"])
        assert bound["status"] == "ok" and (bound["lower"], bound["upper"]) == ("0", "10")


def test_24_point_arithmetic_respects_containers_caps_and_rounding():
    assert to_decimal("0.125") == Decimal("0.13")  # explicit ROUND_HALF_UP
    assert treatment_proposal({"action": "deduct", "points": "0.5"}, "1") == {"kind": "points", "points": "0.5",
                                                                              "max_points": "1"}
    with pytest.raises(PointError):
        treatment_proposal({"action": "award", "points": "2"}, "1")
    assert compare_with_draft({"kind": "points", "points": "0"}, "k", {}) ["status"] == "no_criterion_points"
    parsed = _synthetic_guide()
    from types import SimpleNamespace as N

    rows = [N(criterion_key=c.key, parent_key=c.parent_key, assignment=c.assignment, rule_kind=c.rule_kind,
              is_container=c.is_container, max_points=None if c.max_points is None else str(c.max_points))
            for c in parsed.criteria]
    bound = information_bound(rows, "SA3", {"SA3.program_1.screenshot": "1"}, [])
    assert bound["lower"] == "1" and bound["upper"] == "10"           # containers not double counted
    assert information_bound(rows, "SA5", {}, ["conflict"])["status"] == "blocked"


@pytest.mark.skipif(not (PROJECT_ROOT / "data/private/phase2-review/grading-guide.pdf").is_file(),
                    reason="private guide not present")
def test_real_guide_findings_are_preserved_not_fixed():
    from app.rubrics.service import extract_pdf_pages

    issues = validate(parse_guide(extract_pdf_pages(PROJECT_ROOT / "data/private/phase2-review/grading-guide.pdf")))
    kinds = {(i.assignment, i.kind) for i in issues}
    for expected in [("SA5", "arithmetic_conflict"), ("SA1", "exemption_unresolved"), ("SA3", "exemption_unresolved"),
                     ("SA6", "partial_credit_undefined"), ("SA3", "precedence_unresolved"),
                     ("SA4", "precedence_unresolved"), ("SA6", "scaffold_unknown")]:
        assert expected in kinds


# ---------------------------------------------------------------- flags and prompts (15, 16)


def test_16_blank_similarity_and_unresolved_identity_do_not_crash_or_force_links(env):
    with session_scope() as s:
        flags = {f.original_flag_id: f for f in s.scalars(select(ExternalFlag))}
        assert flags["2"].similarity is None and flags["2"].similarity_raw == ""
        links = {(l.flag_id, l.side): l for l in s.scalars(select(ExternalFlagLink))}
        assert links[(flags["2"].id, "a")].status == "unresolved"          # altered snippet, same score/grader no help
        assert links[(flags["1"].id, "a")].status == "exact_file_in_larger_bundle"
        assert any(c["kind"] == "note_contradicted_by_source" for c in flags["1"].checks)
        assert any(c["kind"] == "speculation_not_evidence" for c in flags["2"].checks)
        assert flags["1"].trust == "unverified_imported_lead"


def test_15_39_grades_graders_and_imported_notes_never_reach_model_prompts(env):
    draft(env, "P001", "9.0", grader="Grader Secretlabel")
    calls = env["registry"].for_mode("mock").generation("analyst").calls
    text = "\n".join(m["content"] for c in calls for m in c["messages"])
    for leaked in ("9.0", "Secretlabel", "Synthetic Grader", "neither program has an author header", "P001"):
        assert leaked not in text


# ---------------------------------------------------------------- guidance propagation (19-21, 25)


def test_19_20_anchor_scan_covers_unflagged_same_score_same_grader_blank_and_ignores_similarity_chains(env):
    draft(env, "P001", "9.0", grader="Grader A")
    issue_id = request(env, "P001")
    resolve_inclusive(env, issue_id)
    with session_scope() as s:
        from app.db.models import NoteApplicability

        issue = s.get(CalibrationIssue, issue_id)
        rows = {s.get(Student, s.get(Submission, r.submission_id).student_id).code: r for r in s.scalars(
            select(NoteApplicability).where(NoteApplicability.note_version_id == issue.resolution_note_version_id))}
    assert len(rows) == 11
    assert rows["P006"].status == "applies" and not rows["P006"].previously_flagged        # unflagged + blank score
    assert rows["P007"].status == "does_not_apply"                                         # same score as P001
    assert rows["P004"].status == "applies"                                                # same grader as P001
    # P009 is lexically close to P007 but its outcome comes only from its own typed facts.
    assert rows["P009"].status == "does_not_apply" and rows["P002"].method == "typed_rule"


def test_21_25_superseded_guidance_invalidates_current_proposals(env):
    from app.guidance import notes

    draft(env, "P001")
    issue_id = request(env, "P001")
    resolve_inclusive(env, issue_id)
    with session_scope() as s:
        old = s.get(CalibrationIssue, issue_id).resolution_note_version_id
        successor = notes.supersede(s, old, {"interpretation": "Revised wording."}, "head-ta")
        notes.approve(s, successor.id, {}, "head-ta")
        from app.db.models import NoteApplicability, InstructorNoteVersion

        assert s.get(InstructorNoteVersion, old).status == "superseded"
        assert all(not r.is_current for r in s.scalars(select(NoteApplicability).where(
            NoteApplicability.note_version_id == old)))
        from app.grading.prerelease import create_snapshot
        from app.db.models import AssessmentVersion

        snap = create_snapshot(s, s.get(AssessmentVersion, env["sa3"]), "tester", env["settings"])
        assert all(g["note_version_id"] != old for g in snap.guidance_versions)


# ---------------------------------------------------------------- drafts (17, 27-31, 38)


def test_27_total_only_draft_persists_when_inference_fails(tmp_path: Path):
    settings = make_settings(tmp_path, analyst_provider="ollama", ollama_base_url="http://127.0.0.1:9")
    init_engine(settings.effective_database_url)
    from tests.conftest import import_dataset, write_dataset

    csv_path, src = write_dataset(tmp_path / "d", [("S001", "G", 9)], {"S001_a.py": "print('x')\n"})
    _, aid, _ = import_dataset(csv_path, src)
    with session_scope() as s:
        sid = s.scalar(select(Submission.id))
        rev, _, _ = save_draft(s, sid, total="9", grader_label="G", actor="t", expected_revision_id=None)
        rid = rev.id
        assert rev.criterion_points == {}
    process_queued(ProviderRegistry(settings))
    with session_scope() as s:
        fb = s.scalar(select(DraftFeedback).where(DraftFeedback.revision_id == rid))
        assert fb.status == "partial" and fb.stages["model_source_analysis"]["status"] == "failed"
        assert s.get(DraftGradeRevision, rid).total == "9"


def test_28_idempotent_saves_and_explicit_concurrency_conflicts(env):
    first = draft(env, "P007", "9.0", key="k1", process=False)
    with session_scope() as s:
        sid = env["subs"]["P007"]
        rev, created, _ = save_draft(s, sid, total="8.0", grader_label="Grader A", actor="t",
                                     expected_revision_id=first, idempotency_key="k2")
        same, created_again, _ = save_draft(s, sid, total="8.0", grader_label="Grader A", actor="t",
                                            expected_revision_id=first, idempotency_key="k2")
        assert created and not created_again and same.id == rev.id
        with pytest.raises(DraftConflict):
            save_draft(s, sid, total="7.0", grader_label="Grader B", actor="t", expected_revision_id=first)
        with pytest.raises(DraftConflict):
            save_draft(s, sid, total="6.0", grader_label="Grader A", actor="t", expected_revision_id=rev.id,
                       idempotency_key="k2")
        assert s.scalar(select(func.count(DraftGradeRevision.id)).where(DraftGradeRevision.submission_id == sid)) == 2


def test_29_late_completion_of_an_older_check_cannot_become_current(env):
    from app.grading.feedback import run_checks

    first = draft(env, "P007", "9.0", process=False)
    with session_scope() as s:
        old_fb = s.scalar(select(DraftFeedback).where(DraftFeedback.revision_id == first)).id
    second = draft(env, "P007", "8.5", process=False)
    run_checks(old_fb, env["registry"].for_mode("mock"))        # the old job finishes late
    with session_scope() as s:
        assert s.get(DraftFeedback, old_fb).status in ("stale", "superseded")
    process_queued(env["registry"])
    with session_scope() as s:
        from app.grading.feedback import current_feedback

        assert current_feedback(s, env["subs"]["P007"]).revision_id == second


def test_30_31_peer_matching_scope_and_cold_start_disclosure(env):
    draft(env, "P001", "9.0")
    with session_scope() as s:
        from app.grading.feedback import current_feedback

        cov = current_feedback(s, env["subs"]["P001"]).coverage
        assert "Insufficient peer evidence" in cov["peers"]["note"]
    draft(env, "P002", "10.0", grader="Grader B")
    draft(env, "P001", "8.0")  # self-revision: must not compare against own earlier revision
    with session_scope() as s:
        cov = current_feedback(s, env["subs"]["P001"]).coverage
        compared = cov["peers"]["compared"]
        assert compared and all(c["submission_id"] != env["subs"]["P001"] for c in compared)
        assert {c["role"] for c in compared} <= {"program_1", "program_2"}
        sa6_ids = set(s.scalars(select(Submission.id).where(Submission.assessment_id == env["sa6"])))
        assert not sa6_ids & {c["submission_id"] for c in compared}  # different assignment never matched


def test_17_38_no_fabricated_deductions_or_historical_timestamps(env):
    with session_scope() as s:
        assert s.scalar(select(func.count(GradingEvent.id))) == 0      # import creates no grading events
        rev, created, _ = draft_from_historical(s, env["subs"]["P001"], "tester", None)
        assert rev.origin == "from_historical_record" and rev.total == "9" and rev.criterion_points == {}
        events = list(s.scalars(select(GradingEvent)))
        assert {e.event_type for e in events} == {"draft_saved", "draft_from_historical"}
        assert all(not e.simulated for e in events)
        with pytest.raises(DraftError):
            draft_from_historical(s, env["subs"]["P006"], "tester", None)   # blank recorded score


# ---------------------------------------------------------------- issues (32-37)


def test_33_materially_different_scope_does_not_auto_merge(env):
    from app.calibration.issues import match_key

    a = match_key(1, "SA3.program_1.correct_final_balance", "program_1", 1, "h", "loop_boundary_inclusive", "q")
    assert a != match_key(1, "SA3.program_2.correctly_setting_up_loop", "program_2", 1, "h", "loop_boundary_inclusive", "q")
    assert a != match_key(2, "SA3.program_1.correct_final_balance", "program_1", 1, "h", "loop_boundary_inclusive", "q")
    assert a != match_key(1, "SA3.program_1.correct_final_balance", "program_1", None, "h", "loop_boundary_inclusive", "q")
    assert a != match_key(1, "SA3.program_1.correct_final_balance", "program_1", 1, "h2", "loop_boundary_inclusive", "q")


def test_35_keep_with_explanation_is_local_only(env):
    draft(env, "P001")
    issue_id = request(env, "P001")
    draft(env, "P002", "10.0", grader="Grader B")
    card = cards(env, "P002", "policy_question")[0]
    with session_scope() as s:
        dispositions.respond(s, card.id, "keep_with_explanation", "tester", revision_id=card.revision_id,
                             explanation="Different interval in my section's version.")
    with session_scope() as s:
        assert s.get(CalibrationIssue, issue_id).state == "awaiting_head_ta"
        link = s.scalar(select(IssueCaseLink).where(IssueCaseLink.issue_id == issue_id,
                                                    IssueCaseLink.submission_id == env["subs"]["P002"]))
        assert link.local_disposition == "kept_with_explanation"
    assert cards(env, "P001", "policy_question")[0].status == "linked"


def test_36_unchanged_saves_do_not_repeat_and_changed_rules_reopen(env):
    draft(env, "P001")
    card = cards(env, "P001", "policy_question")[0]
    with session_scope() as s:
        dispositions.respond(s, card.id, "defer", "tester", revision_id=card.revision_id)
    draft(env, "P001", "9.0")                                     # unchanged evidence and rules
    assert cards(env, "P001", "policy_question")[0].status == "suppressed"
    issue_id = None
    with session_scope() as s:
        from app.calibration import issues

        spec = card.comparison["issue_spec"]
        issue, _ = issues.find_or_create(s, assessment_id=env["sa3"], assignment_code="SA3",
                                         criterion_key=spec["criterion_key"], component_role=spec["component_role"],
                                         rubric_version_id=spec["rubric_version_id"], rule_hash=spec["rule_hash"],
                                         issue_type=spec["issue_type"], question_key=spec["question_key"],
                                         question=spec["question"], context_note="", actor="tester")
        issue_id = issue.id
    resolve_inclusive(env, issue_id)                              # a changed rule produces a new card
    covered = cards(env, "P001", "covered_by_guidance")
    assert covered and covered[0].status == "open"


def test_37_merge_split_and_history_are_preserved(env):
    from app.calibration import issues

    draft(env, "P001")
    draft(env, "P002", "10.0", grader="Grader B")
    with session_scope() as s:
        mk = dict(assessment_id=env["sa3"], assignment_code="SA3", criterion_key="SA3.program_1.x",
                  component_role="program_1", rubric_version_id=None, rule_hash="", context_note="", actor="ta")
        a, _ = issues.find_or_create(s, issue_type="t1", question_key="q1", question="Q1?", **mk)
        b, _ = issues.find_or_create(s, issue_type="t2", question_key="q2", question="Q2?", **mk)
        issues.link_case(s, a, env["subs"]["P001"], "ta", "grader_request")
        issues.link_case(s, b, env["subs"]["P002"], "ta", "grader_request")
        target = issues.merge(s, a.id, b.id, "head-ta", a.version, b.version)
        assert issues.affected_count(s, target.id) == 2 and s.get(CalibrationIssue, a.id).state == "superseded"
        new = issues.split(s, target.id, [env["subs"]["P001"]], "Separate question?", "head-ta", target.version)
        assert issues.affected_count(s, new.id) == 1 and issues.affected_count(s, target.id) == 1
        history = list(s.scalars(select(IssueCaseLink).where(IssueCaseLink.submission_id == env["subs"]["P001"])))
        assert [h.active for h in history].count(True) == 1 and len(history) == 3
        with pytest.raises(issues.IssueConflict):
            issues.defer(s, target.id, "ta", expected_version=1, reason="stale version")


# ---------------------------------------------------------------- jobs, pre-release (22, 23, 40)


def test_22_prompt_overflow_is_visible_partial_coverage(tmp_path: Path):
    settings = make_settings(tmp_path, ollama_num_ctx=600, analyst_max_output_tokens=200)
    init_engine(settings.effective_database_url)
    from tests.conftest import import_dataset, write_dataset

    big = "".join(f'print("{"#" * 60} line {i}")\n' for i in range(40))
    csv_path, src = write_dataset(tmp_path / "d", [("S001", "G", 9)], {"S001_a.py": big})
    import_dataset(csv_path, src)
    with session_scope() as s:
        rev, _, _ = save_draft(s, s.scalar(select(Submission.id)), total="9", grader_label="G", actor="t",
                               expected_revision_id=None)
        rid = rev.id
    process_queued(ProviderRegistry(settings))
    with session_scope() as s:
        fb = s.scalar(select(DraftFeedback).where(DraftFeedback.revision_id == rid))
        assert fb.status == "partial"
        assert "prompt_overflow" in json.dumps(fb.stages["model_source_analysis"])


def test_23_feedback_signature_changes_with_rule_inputs(env):
    draft(env, "P001")
    with session_scope() as s:
        from app.grading.feedback import current_feedback

        before = current_feedback(s, env["subs"]["P001"]).signature
    issue_id = request(env, "P001")
    resolve_inclusive(env, issue_id)
    with session_scope() as s:
        after = current_feedback(s, env["subs"]["P001"])
        assert after.signature != before and after.inputs["guidance_versions"]


def test_40_prerelease_reports_unchecked_pairs_failures_and_unresolved_and_pins_revisions(env):
    from app.grading.prerelease import create_snapshot, prerelease_summary, start_prerelease_review
    from app.db.models import AssessmentVersion

    rev = draft(env, "P001")
    request(env, "P001")
    with session_scope() as s:
        a = s.get(AssessmentVersion, env["sa3"])
        start_prerelease_review(s, a, 4, "tester", "mock")
    process_queued(env["registry"])
    with session_scope() as s:
        a = s.get(AssessmentVersion, env["sa3"])
        summary = prerelease_summary(s, a)
        assert summary["coverage"]["all_pairs"] == 55 and summary["coverage"]["unchecked_pairs"] >= 51
        assert summary["unresolved_policy"] and summary["unresolved_policy"][0]["affected"] == 1
        assert summary["coverage"]["model_reviewed_pairs"] <= 4
        snap = create_snapshot(s, a, "tester", env["settings"])
        assert snap.revision_ids == [rev]
        assert "open shared issue" in json.dumps(snap.unresolved)


def test_25_original_gradebook_untouched_through_the_whole_flow(env):
    before = hashlib.sha256((P1_DIR / "scores.csv").read_bytes()).hexdigest()
    with session_scope() as s:
        scores_before = sorted((r.id, r.raw_cell) for r in s.scalars(select(ScoreRecord)))
    draft(env, "P001")
    resolve_inclusive(env, request(env, "P001"))
    with session_scope() as s:
        assert sorted((r.id, r.raw_cell) for r in s.scalars(select(ScoreRecord))) == scores_before
    assert hashlib.sha256((P1_DIR / "scores.csv").read_bytes()).hexdigest() == before
