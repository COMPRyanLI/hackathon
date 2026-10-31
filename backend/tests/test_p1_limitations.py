"""Regression coverage for latency, scope correction and conservative source checks."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import select

from app.db.models import Submission
from app.db.models_p1 import ComponentMapping, DraftFeedback
from app.db.session import session_scope
from app.extraction.facts import artifact_facts, scalar_fact
from app.grading.drafts import save_draft
from app.grading.feedback import source_observation_cards
from app.importers.multi import confirm_mapping
from app.jobs.worker import Worker
from tests.test_p1_acceptance import env, draft


def test_draft_arriving_during_model_call_gets_source_feedback(env, monkeypatch):
    from app.grading import feedback
    entered, release = threading.Event(), threading.Event()
    original = feedback.run_draft_model
    def blocked(run_id, registry):
        entered.set()
        assert release.wait(15), "test did not release the simulated model call"
        original(run_id, registry)
    monkeypatch.setattr(feedback, "run_draft_model", blocked)
    draft(env, "P001", process=False)
    worker = Worker(env["registry"], poll_s=0.02)
    worker.start()
    try:
        assert entered.wait(8)
        with session_scope() as s:
            _, _, fb = save_draft(s, env["subs"]["P002"], total="9", grader_label="G", actor="tester",
                                 expected_revision_id=None)
            fid = fb.id
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with session_scope() as s:
                ready = s.get(DraftFeedback, fid).stages.get("deterministic", {}).get("status") == "completed"
            if ready:
                break
            time.sleep(0.03)
        assert ready, "CPU checks waited behind an already-running model call"
        assert not release.is_set()
    finally:
        worker.stop()
        release.set()
        worker.join(10)
        assert not worker.is_alive()


def test_mapping_change_stales_feedback_and_validates_role(env):
    draft(env, "P001")
    with session_scope() as s:
        sub = s.get(Submission, env["subs"]["P001"])
        aid = sub.artifacts[0].id
        with pytest.raises(ValueError, match="Role must"):
            confirm_mapping(s, aid, "invented_role", "tester")
        mapping = confirm_mapping(s, aid, "ambiguous", "tester")
        assert mapping.status == "ambiguous"
        assert all(f.status == "stale" for f in s.scalars(select(DraftFeedback).where(
            DraftFeedback.submission_id == sub.id)))
        assert len(list(s.scalars(select(ComponentMapping).where(ComponentMapping.artifact_id == aid,
                         ComponentMapping.is_current.is_(True))))) == 1


def _criterion(key, binding=None):
    return NS(criterion_key=key, fact_binding=binding, assignment="shared" if key.startswith("shared") else key[:3],
              is_container=False, component_role=None, title=key, page=1, source_quote="Synthetic criterion",
              max_points="1", classification="explicit", rule_kind="award")


@pytest.mark.parametrize("code,source,expected", [
    ("SA1", 'print("# is program data")\n', "Header comment not observed"),
    ("SA2", '# [NAME]\ndef draw():\n    pass\n', "1 function definition"),
    ("SA5", '# [NAME]\nstart_graphics(draw, mouse_press=click, framerate=120)\n', "Explicit framerate 120"),
])
def test_new_assignment_checks_are_observations_without_points(code, source, expected):
    artifact = NS(id=1, content_sha256="test-"+code, source_text=source)
    criteria = [_criterion("shared.practice.header_comments", "header_comment"),
                _criterion(code+".main.required_functions"), _criterion(code+".main.callback"),
                _criterion(code+".main.framerate")]
    cards = source_observation_cards({"code":code, "rubric":{"criteria":criteria},
                                     "by_role":{"whole_assignment":[artifact]}})
    assert any(expected in c["title"] for c in cards)
    assert all(c["comparison"]["proposal"] is None and "issue_spec" not in c for c in cards)
    if source.startswith("#"):
        assert not any("Header comment not observed" in c["title"] for c in cards)


def test_dynamic_framerate_is_unknown_and_parse_failure_does_not_mean_zero():
    assert scalar_fact(artifact_facts("start_graphics(draw, framerate=get_rate())"), "graphics_framerate") is None
    assert scalar_fact(artifact_facts("def [NAME]():"), "graphics_callback_keyword_count") is None


def test_real_code_pattern_absence_does_not_establish_semantic_failure(env):
    from app.calibration.rules import evaluate_spec
    from app.db.models import AssessmentVersion
    with session_scope() as s:
        sub = s.get(Submission, env["subs"]["P001"])
        s.get(AssessmentVersion, sub.assessment_id).is_synthetic = False
        # Even an approved numeric policy must not turn a syntax-pattern
        # absence into proof of a semantic omission in real student code.
        spec = NS(condition={"component":"program_1", "fact":"diagonal_check_present", "op":"==", "value":False},
                  exception_conditions=[], point_treatment={"action":"range","min":"3","max":"4"})
        result = evaluate_spec(s, spec, sub, NS(max_points="6"))
        assert result["status"] == "uncertain" and result["proposal"] is None
        assert result["evidence"][0]["observed_pattern"] is False


def test_stale_mapping_feedback_cannot_be_acted_on(env):
    from app.grading.dispositions import respond
    from app.grading.drafts import DraftConflict
    from app.db.models_p1 import FeedbackCard
    draft(env, "P001")
    with session_scope() as s:
        sub = s.get(Submission, env["subs"]["P001"])
        card = s.scalar(select(FeedbackCard).where(FeedbackCard.submission_id == sub.id))
        confirm_mapping(s, sub.artifacts[0].id, "ambiguous", "tester")
        with pytest.raises(DraftConflict, match="Feedback inputs changed"):
            respond(s, card.id, "keep_with_explanation", "tester", revision_id=card.revision_id,
                    explanation="This was based on the old role.")
