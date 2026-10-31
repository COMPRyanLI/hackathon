"""Asynchronous draft feedback.

Order of checks for one saved revision:
  1. approved typed guidance (reuse answers; compare proposals with the draft in code),
  2. typed issue detectors scoped to the assignment (policy questions, exceptional rules,
     explicit allowances, evidence absent for full credit),
  3. incremental peer comparison within the same assignment and component,
  4. an optional bounded model pass that reuses cached source analysis.

Scores and grader labels never enter model prompts. A total-only draft never
yields a claimed criterion deduction. Feedback for a revision that is no longer
current is stored as stale and never served as current.
"""
from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.db.models import AnalysisRun, AssessmentVersion, InstructorNoteVersion, Submission, Student, utcnow
from app.db.models_p1 import (
    CalibrationIssue,
    DraftFeedback,
    DraftGradeRevision,
    FeedbackCard,
    FeedbackDisposition,
    GradingEvent,
    GuidanceRuleSpec,
    IssueCaseLink,
)
from app.db.session import session_scope
from app.extraction.facts import fact_lines, safe_token_ngrams, scalar_fact
from app.grading.drafts import current_revision, enqueue_check
from app.importers.multi import current_roles, ensure_component_mappings, facts_for
from app.rubrics.engine import compare_with_draft, to_decimal
from app.rubrics.service import assessment_rubric, criterion_hash
from app.calibration import issues as issue_service
from app.calibration.rules import evaluate_spec
from app.jobs.errors import Cancelled

CHECK_VERSION = "draft-check-v2"
MAX_INITIAL_CARDS = 3
PEER_FACTS = ("loop_inclusive_bound", "uses_pow", "while_loop_present", "header_comment_present",
              "rate_uses_multiplier_form")
RESPONDED = ("keep_with_explanation", "defer", "link_issue", "request_calibration")


# ---------------------------------------------------------------- context


def _quote(artifact: Any, lines: list[int]) -> list[dict[str, Any]]:
    src = artifact.source_text.splitlines()
    return [{"artifact_id": artifact.id, "line": n, "quote": src[n - 1][:200]} for n in lines[:4] if 0 < n <= len(src)]


def _criterion(rubric: dict | None, role: str | None, *needles: str):
    if not rubric:
        return None
    for c in rubric["criteria"]:
        if c.is_container or c.assignment == "shared":
            continue
        if role and c.component_role not in (role, None):
            continue
        if all(n in c.criterion_key for n in needles):
            return c
    return None


def _rubric_ref(c: Any) -> list[dict[str, Any]]:
    if c is None:
        return []
    return [{"criterion_key": c.criterion_key, "title": c.title, "page": c.page, "quote": c.source_quote[:300],
             "max_points": c.max_points, "classification": c.classification, "rule_kind": c.rule_kind}]


def _artifacts_by_role(session: Session, sub: Submission) -> dict[str, list[Any]]:
    roles = current_roles(session, sub)
    out: dict[str, list[Any]] = {}
    for a in sub.artifacts:
        m = roles.get(a.id)
        role = m.role if m and m.status in ("proposed", "confirmed") else "ambiguous"
        out.setdefault(role, []).append(a)
    return out


def build_context(session: Session, revision: DraftGradeRevision) -> dict[str, Any]:
    sub = session.get(Submission, revision.submission_id)
    assessment = session.get(AssessmentVersion, revision.assessment_id)
    ensure_component_mappings(session, assessment, "system")
    rubric = assessment_rubric(session, assessment)
    specs = list(session.scalars(select(GuidanceRuleSpec).join(
        InstructorNoteVersion, InstructorNoteVersion.id == GuidanceRuleSpec.note_version_id).where(
        GuidanceRuleSpec.assessment_id == assessment.id, InstructorNoteVersion.status == "approved")))
    return {"session": session, "revision": revision, "submission": sub, "assessment": assessment,
            "code": assessment.name.upper(), "rubric": rubric, "specs": specs,
            "by_role": _artifacts_by_role(session, sub),
            "points": revision.criterion_points or {}}


# ---------------------------------------------------------------- checks


def guidance_cards(ctx: dict[str, Any]) -> tuple[list[dict], set[str]]:
    session, sub, rubric = ctx["session"], ctx["submission"], ctx["rubric"]
    cards, covered_types = [], set()
    for spec in ctx["specs"]:
        note = session.get(InstructorNoteVersion, spec.note_version_id)
        criterion = rubric["by_key"].get(spec.criterion_key) if rubric else None
        result = evaluate_spec(session, spec, sub, criterion)
        issue = session.get(CalibrationIssue, spec.issue_id) if spec.issue_id else None
        if issue is not None:
            covered_types.add(issue.match_key)
        base = {"criterion_key": spec.criterion_key, "component_role": spec.component_role,
                "rubric_refs": _rubric_ref(criterion), "guidance_version_id": note.id,
                "evidence": [{"fact": e.get("fact"), "actual": e.get("actual"), "artifact_id": e.get("artifact_id"),
                              "lines": e.get("lines", [])} for e in result["evidence"]],
                "sig": [spec.id, result["status"], [e.get("actual") for e in result["evidence"]]]}
        label = f"guidance v{note.version} (note {note.note_id}, approved by {note.approved_by})"
        if result["status"] == "applies":
            comparison = compare_with_draft(result["proposal"] or {"kind": "no_numeric_rule"}, spec.criterion_key,
                                            ctx["points"])
            if comparison["status"] == "differs":
                cards.append({**base, "type": "guidance_differs", "priority": 90,
                              "title": f"Your draft differs from {label}",
                              "body": f"Approved guidance: {note.interpretation} It proposes "
                                      f"{result['proposal'].get('points', result['proposal'])} for {spec.criterion_key}; "
                                      f"your draft records {comparison['draft']}. Review the difference or explain an "
                                      "exception. Nothing changes automatically.",
                              "comparison": {"proposal": result["proposal"], "draft_comparison": comparison},
                              "sig": base["sig"] + [comparison.get("draft")]})
            else:
                note_text = ("Your draft records the same criterion value." if comparison["status"] == "consistent"
                             else "Your draft is total-only, so it cannot be compared with this criterion proposal; "
                                  "check that your total reflects it." if comparison["status"] == "no_criterion_points"
                             else "No numeric rule is attached.")
                cards.append({**base, "type": "covered_by_guidance", "priority": 60,
                              "title": f"Covered by {label}",
                              "body": f"{note.interpretation} {note_text} No new head-TA question is needed.",
                              "comparison": {"proposal": result["proposal"], "draft_comparison": comparison}})
        elif result["status"] == "uncertain":
            cards.append({**base, "type": "guidance_exception", "priority": 72,
                          "title": f"{label} may not apply here: individual review",
                          "body": f"{result['rationale']} The approved answer is not applied automatically.",
                          "comparison": {"exception": result.get("exception")}})
    return cards, covered_types


def _issue_spec(ctx: dict, issue_type: str, question_key: str, criterion: Any, role: str | None, question: str,
                note: str = "") -> dict[str, Any]:
    rubric = ctx["rubric"]
    return {"issue_type": issue_type, "question_key": question_key, "question": question, "context_note": note,
            "criterion_key": criterion.criterion_key if criterion else f"{ctx['code']}.{role or 'main'}",
            "component_role": role, "rubric_version_id": rubric["version"].id if rubric else None,
            "rule_hash": criterion_hash(criterion) if criterion else ""}


def detector_cards(ctx: dict[str, Any]) -> list[dict]:
    code, rubric, points = ctx["code"], ctx["rubric"], ctx["points"]
    cards: list[dict] = source_observation_cards(ctx)
    by_role = ctx["by_role"]

    if code == "SA3":
        for artifact in by_role.get("program_1", []):
            facts = facts_for(artifact)
            if scalar_fact(facts, "loop_inclusive_bound"):
                crit = _criterion(rubric, "program_1", "final_balance")
                start = scalar_fact(facts, "loop_counter_start")
                lines = [l["line"] for l in facts["loops"] if l["inclusive_bound"]]
                note = ("The loop counter starts at 1, so the iteration count may equal the exclusive form; this may "
                        "be an exception." if start == 1 else "")
                cards.append({
                    "type": "policy_question", "priority": 70, "criterion_key": crit.criterion_key if crit else None,
                    "component_role": "program_1", "rubric_refs": _rubric_ref(crit), "evidence": _quote(artifact, lines),
                    "title": "Inclusive loop end bound in Program 1",
                    "body": "The Program 1 loop uses an inclusive end bound (`<=` or `range(..., LIMIT + 1)`), one "
                            "more iteration than an exclusive bound. The intended number of compounding periods is "
                            "unresolved (assignment instructions unavailable), so no correctness claim is made. "
                            + note,
                    "question": "If the intended interval excludes the end value, should an inclusive loop bound in "
                                "Program 1 affect only the 'correct final balance' and 'correct interest earned' "
                                "criteria, or also count as a loop-setup problem?",
                    "issue_spec": _issue_spec(ctx, "loop_boundary_inclusive", "sa3_p1_inclusive_bound_effect", crit,
                                              "program_1",
                                              "If the intended interval excludes the end value, should an inclusive "
                                              "loop bound in Program 1 affect only the 'correct final balance' and "
                                              "'correct interest earned' criteria, or also count as a loop-setup "
                                              "problem?", note),
                    "sig": ["incl", artifact.content_sha256, start]})
        for role, artifacts in by_role.items():
            for artifact in artifacts:
                facts = facts_for(artifact)
                if scalar_fact(facts, "uses_pow"):
                    crit = _criterion(rubric, None, "note", "simple_interest")
                    cards.append({
                        "type": "exceptional_policy", "priority": 85,
                        "criterion_key": crit.criterion_key if crit else None, "component_role": role,
                        "rubric_refs": _rubric_ref(crit), "evidence": _quote(artifact, fact_lines(facts, "uses_pow")),
                        "title": "Exponentiation found: exceptional resubmission rule needs human confirmation",
                        "body": "The guide's SA3 note mentions a zero and resubmission for simple interest or "
                                "exponentiation. Its scope and precedence are unresolved, and an operator alone is "
                                "not proof of a prohibited calculation. Confirm with the head TA; no verdict is "
                                "proposed.",
                        "question": "Does the SA3 exponentiation rule apply to this use of `**`, and how does it "
                                    "interact with the criterion points?",
                        "issue_spec": _issue_spec(ctx, "exceptional_policy_pow", "sa3_pow_rule_scope", crit, None,
                                                  "Does the SA3 exponentiation rule apply when `**` computes compound "
                                                  "growth, and how does it interact with criterion points?"),
                        "manual_issue": True,
                        "sig": ["pow", artifact.content_sha256]})
        for role, needle in (("program_1", "upper_case"), ("program_2", "defining_constants")):
            crit = _criterion(rubric, role, needle)
            if crit is None or crit.criterion_key not in points:
                continue
            given = to_decimal(points[crit.criterion_key])
            maximum = to_decimal(crit.max_points)
            for artifact in by_role.get(role, []):
                facts = facts_for(artifact)
                count = scalar_fact(facts, "uppercase_constant_count")
                if given < maximum and facts.get("rate_literals") and count:
                    allowance = _criterion(rubric, None, "note", "define")
                    cards.append({
                        "type": "explicit_allowance", "priority": 65, "criterion_key": crit.criterion_key,
                        "component_role": role, "rubric_refs": _rubric_ref(crit) + _rubric_ref(allowance),
                        "evidence": _quote(artifact, fact_lines(facts, "uppercase_constant_count")),
                        "title": "The guide explicitly allows either rate representation",
                        "body": f"Your draft gives {given} of {maximum} here. Upper-case rate constants are present "
                                f"({', '.join(str(r) for r in facts['rate_literals'])}). The guide accepts 1.05/1.04 "
                                "as well as 0.05/0.04 when used appropriately; check whether the deduction is for "
                                "something else.", "sig": ["allow", artifact.content_sha256, str(given)]})
                if given == maximum and count == 0:
                    cards.append({
                        "type": "evidence_not_found", "priority": 75, "criterion_key": crit.criterion_key,
                        "component_role": role, "rubric_refs": _rubric_ref(crit), "evidence": [],
                        "title": "Full credit, but no upper-case constants found in this component",
                        "body": "Static facts show no module-level upper-case numeric constants in the file mapped "
                                "to this program. The mapping may be wrong, or the credit may rest on other "
                                "evidence; please confirm.", "sig": ["noconst", artifact.content_sha256, str(given)]})
    if code == "SA6":
        crit = _criterion(rubric, None, "correctness")
        for role, artifacts in by_role.items():
            for artifact in artifacts:
                facts = facts_for(artifact)
                reasons = []
                if scalar_fact(facts, "diagonal_check_present") is False:
                    reasons.append(("missing_diagonal", "no diagonal conflict check was found",
                                    "a reviewer confirms that diagonal conflict checks are missing", "diagonal_check_present"))
                elif scalar_fact(facts, "diagonal_check_complete") is False:
                    reasons.append(("incomplete_diagonal", "only one diagonal direction appears to be checked",
                                    "a reviewer confirms that only one diagonal direction is checked", "diagonal_check_complete"))
                if scalar_fact(facts, "backtracking_present") is False:
                    reasons.append(("missing_backtracking", "a recursive search without an undo step was found",
                                    "a reviewer confirms that recursive search never undoes a placement", "backtracking_present"))
                for reason, observed, situation, fact in reasons:
                    question = (f"For SA6 correctness ({crit.max_points if crit else '?'} points, partial credit by "
                                f"judgment), what points or range should a solution receive when {situation}, "
                                "with the rest of the program appearing to work?")
                    cards.append({
                        "type": "calibration_question", "priority": 68,
                        "criterion_key": crit.criterion_key if crit else None, "component_role": role,
                        "rubric_refs": _rubric_ref(crit), "evidence": _quote(artifact, fact_lines(facts, fact)),
                        "title": f"SA6 partial credit: {observed}",
                        "body": "SA6 correctness is awarded by judgment and the guide defines no point scale. "
                                f"Candidate reason (descriptive taxonomy, not an official weight): {observed}. "
                                + ("Several candidate reasons interact in this submission; review individually. "
                                   if len(reasons) > 1 else "")
                                + "These are syntax-pattern candidates, not proof of a defect; alternate implementations "
                                  "may not match. Real submissions require individual applicability review even after an anchor. "
                                + "Draft mode: no numeric proposal until the head TA approves an anchor.",
                        "question": question,
                        "issue_spec": _issue_spec(ctx, f"sa6_partial_credit_{reason}", f"sa6_{reason}", crit, role,
                                                  question),
                        "sig": [reason, artifact.content_sha256]})
    if code == "SA4":
        for role, artifacts in by_role.items():
            for artifact in artifacts:
                facts = facts_for(artifact)
                if scalar_fact(facts, "while_loop_present"):
                    crit = _criterion(rubric, None, "while_loop")
                    cards.append({
                        "type": "exceptional_policy", "priority": 85, "criterion_key": crit.criterion_key if crit else None,
                        "component_role": role, "rubric_refs": _rubric_ref(crit),
                        "evidence": _quote(artifact, [l["line"] for l in facts["loops"] if l["kind"] == "while"]),
                        "title": "while loop found: SA4 resubmission rule needs human confirmation",
                        "body": "The SA4 note specifies zero and resubmission for while-loop use. Whether this loop "
                                "is the student's relevant code or supplied scaffolding is not established; confirm "
                                "before any consequence.", "manual_issue": True,
                        "issue_spec": _issue_spec(ctx, "exceptional_policy_while", "sa4_while_scope", crit, None,
                                                  "Does the SA4 while-loop rule apply to this loop?"),
                        "sig": ["while", artifact.content_sha256]})
                crit = _criterion(rubric, None, "window_size")
                if crit and scalar_fact(facts, "start_graphics_without_size"):
                    cards.append({
                        "type": "explicit_allowance", "priority": 65, "criterion_key": crit.criterion_key,
                        "component_role": role, "rubric_refs": _rubric_ref(crit),
                        "evidence": _quote(artifact, fact_lines(facts, "start_graphics_without_size")),
                        "title": "Warning only: no deduction for missing width/height in start_graphics",
                        "body": "The guide says to leave a warning rather than deduct when width and height are not "
                                "set in start_graphics. The separate point for using window-size constants is an "
                                "award criterion; missing width/height arguments alone do not justify removing it.",
                        "sig": ["warn", artifact.content_sha256]})
    return cards


def source_observation_cards(ctx: dict[str, Any]) -> list[dict]:
    """Useful code-only checks; never turn unavailable behavior into a deduction."""
    code, rubric = ctx["code"], ctx["rubric"]
    if code not in ("SA1", "SA2", "SA5") or not rubric:
        return []
    header = next((c for c in rubric["criteria"] if c.fact_binding == "header_comment"), None)
    cards = []
    for role, artifacts in ctx["by_role"].items():
        for artifact in artifacts:
            facts = facts_for(artifact)
            def add(title, body, crit, lines, key, priority=45):
                cards.append({"type": "source_observation", "priority": priority,
                              "criterion_key": crit.criterion_key if crit else None, "component_role": role,
                              "rubric_refs": _rubric_ref(crit), "evidence": _quote(artifact, lines),
                              "title": title, "body": body,
                              "comparison": {"proposal": None, "method": "static_observation"},
                              "sig": [key, artifact.content_sha256, FACT_CHECK_VERSION]})
            if header and scalar_fact(facts, "header_comment_present") is False:
                add("Header comment not observed in this uploaded file",
                    "The shared guide includes header comments. No leading comment or module docstring was found "
                    "in this uploaded file. Check component scope and whether export/anonymization removed it. "
                    "This is not proof of a missing original header and proposes no deduction.",
                    header, [1] if artifact.source_text else [], "header_absent", 55)
            if not facts.get("parse_ok"):
                continue
            if code == "SA2":
                crit = _criterion(rubric, None, "required_functions")
                if crit:
                    count = len(facts["function_defs"])
                    add(f"{count} function definition(s) observed; required list unavailable",
                        "Definitions can be located in source, but the required function list is in the missing "
                        "assignment instructions. Definition counts do not establish correctness or missing functions. "
                        "No points are proposed.", crit,
                        fact_lines(facts, "function_def_count") or [1], "function_inventory")
            if code == "SA5" and facts["start_graphics"]:
                crit = _criterion(rubric, None, "callback")
                count = scalar_fact(facts, "graphics_callback_keyword_count")
                if crit:
                    add(f"{int(count)} mouse/key callback keyword(s) observed",
                        "This records callback keyword arguments to start_graphics only. Positional callbacks, "
                        "aliases and actual event behavior are not established by this check. No points are proposed.",
                        crit, fact_lines(facts, "graphics_callback_keyword_count"), "callbacks")
                rate = scalar_fact(facts, "graphics_framerate")
                crit = _criterion(rubric, None, "framerate")
                if crit and rate is not None and rate != 40:
                    add(f"Explicit framerate {rate:g}; guide requests a check at 40",
                        "The code supplies a different framerate. That does not establish why it was chosen or "
                        "whether drawing breaks at 40. Student code was not executed; the behavioral criterion "
                        "remains unresolved and no deduction is proposed.", crit,
                        fact_lines(facts, "graphics_framerate"), "framerate", 55)
    return cards


FACT_CHECK_VERSION = "source-observations-v1"


def peer_cards(ctx: dict[str, Any], limit: int = 3) -> tuple[list[dict], dict[str, Any]]:
    """Compare compatible components with the latest drafts of other students."""
    from sklearn.feature_extraction.text import TfidfVectorizer

    session, sub, revision = ctx["session"], ctx["submission"], ctx["revision"]
    peers = []
    for other in session.scalars(select(Submission).where(Submission.assessment_id == sub.assessment_id,
                                                          Submission.id != sub.id)):
        rev = current_revision(session, other.id)
        if rev is not None:
            peers.append((other, rev))
    info: dict[str, Any] = {"drafted_peers": len(peers), "compared": []}
    if not peers:
        info["note"] = "Insufficient peer evidence: no other drafts in this assignment yet. Explicit rules only."
        return [], info
    cards = []
    my_total = to_decimal(revision.total)
    for role, artifacts in ctx["by_role"].items():
        if role == "ambiguous":
            continue
        mine = artifacts[0]
        my_facts = facts_for(mine)
        candidates = []
        for other, rev in peers:
            theirs = _artifacts_by_role(session, other).get(role)
            if theirs:
                candidates.append((other, rev, theirs[0]))
        if not candidates:
            continue
        docs = [" || ".join(safe_token_ngrams(mine.source_text))] + [
            " || ".join(safe_token_ngrams(c[2].source_text)) for c in candidates]
        matrix = TfidfVectorizer(token_pattern=r"[^|]+", lowercase=False).fit_transform(docs)
        sims = (matrix[0] @ matrix[1:].T).toarray()[0]
        ranked = sorted(zip(candidates, sims), key=lambda x: -x[1])[:limit]
        for (other, rev, art), sim in ranked:
            facts = facts_for(art)
            same = all(scalar_fact(my_facts, f) == scalar_fact(facts, f) for f in PEER_FACTS)
            code = session.get(Student, other.student_id).code
            info["compared"].append({"role": role, "submission_id": other.id, "student_id": code,
                                     "revision_id": rev.id, "similarity": round(float(sim), 3), "facts_match": same})
            gap = abs(to_decimal(rev.total) - my_total)
            if not same or sim < 0.5 or gap < to_decimal("0.5"):
                continue
            shared_crit = sorted(set(revision.criterion_points or {}) & set(rev.criterion_points or {}))
            differing = [k for k in shared_crit if revision.criterion_points[k] != rev.criterion_points[k]]
            if differing:
                body = (f"For comparable {role.replace('_', ' ')} evidence, your draft records "
                        + ", ".join(f"{k} = {revision.criterion_points[k]}" for k in differing)
                        + f"; {code}'s draft records " + ", ".join(f"{k} = {rev.criterion_points[k]}" for k in differing)
                        + ". Review the highlighted evidence or explain the difference.")
                card_type, priority = "peer_criterion_gap", 80
            else:
                body = (f"Comparable {role.replace('_', ' ')} evidence appears in {code}'s submission (same key "
                        f"facts; similarity {float(sim):.2f}, retrieval diagnostic only), but the draft totals differ "
                        f"({revision.total} vs {rev.total}). Other criteria or missing evidence may explain the gap; "
                        "no criterion deduction is recorded for either draft.")
                card_type, priority = "peer_total_gap", 50 + float(gap) * 5
            cards.append({"type": card_type, "priority": priority, "criterion_key": None, "component_role": role,
                          "rubric_refs": [], "evidence": [], "title": f"Comparable {role.replace('_', ' ')} drafted "
                                                                       f"differently ({code})",
                          "body": body, "comparison": {"peer_submission_id": other.id, "peer_student": code,
                                                       "peer_revision_id": rev.id, "peer_total": rev.total,
                                                       "peer_grader": rev.grader_label,
                                                       "similarity": round(float(sim), 3), "same_grader":
                                                           rev.grader_label == revision.grader_label},
                          "sig": [card_type, role, rev.id, revision.total, differing]})
    return cards, info


def coverage_info(ctx: dict[str, Any]) -> dict[str, Any]:
    rubric = ctx["rubric"]
    out: dict[str, Any] = {"mode": "rubric_guided" if rubric else "content_only",
                           "component_roles": {r: len(a) for r, a in ctx["by_role"].items()},
                           "draft_has_criterion_points": bool(ctx["points"])}
    if rubric:
        out["rubric_version_id"] = rubric["version"].id
        out["unresolved_criteria"] = [
            {"criterion_key": c.criterion_key, "title": c.title[:80], "classification": c.classification,
             "max_points": c.max_points} for c in rubric["criteria"]
            if not c.is_container and c.assignment == ctx["code"] and c.classification != "explicit"]
        out["blocking_rubric_issues"] = rubric["blocking"]
        out["total_bound"] = "Total proposals blocked by rubric issues." if rubric["blocking"] else None
    if "ambiguous" in ctx["by_role"]:
        out["ambiguous_components"] = [a.id for a in ctx["by_role"]["ambiguous"]]
    out["statement"] = ("No card means no issue was found within checked coverage. It does not mean the grade was "
                        "verified correct.")
    return out


# ---------------------------------------------------------------- persistence


def _signature(card: dict[str, Any], revision: DraftGradeRevision, rubric: dict | None) -> str:
    return audit.stable_hash([card["type"], card.get("criterion_key"), card.get("component_role"),
                              card.get("guidance_version_id"), card.get("sig"),
                              rubric["version"].id if rubric else None])


def persist_cards(session: Session, feedback: DraftFeedback, revision: DraftGradeRevision, cards: list[dict],
                  rubric: dict | None, actor: str) -> dict[str, int]:
    prior = list(session.scalars(select(FeedbackCard).where(FeedbackCard.submission_id == revision.submission_id,
                                                            FeedbackCard.feedback_id != feedback.id)))
    responded = {c.signature for c in prior if session.scalar(select(FeedbackDisposition.id).where(
        FeedbackDisposition.card_id == c.id, FeedbackDisposition.action.in_(RESPONDED)).limit(1))}
    seen = {c.signature for c in prior}
    stats = {"cards": 0, "new_notifications": 0, "suppressed": 0, "linked_to_issue": 0}
    for card in sorted(cards, key=lambda c: -c["priority"]):
        sig = _signature(card, revision, rubric)
        row = FeedbackCard(
            feedback_id=feedback.id, submission_id=revision.submission_id, revision_id=revision.id,
            card_type=card["type"], criterion_key=card.get("criterion_key"), component_role=card.get("component_role"),
            title=card["title"], body=card["body"], evidence=card.get("evidence", []),
            rubric_refs=card.get("rubric_refs", []),
            comparison={**card.get("comparison", {}), **({"issue_spec": card["issue_spec"],
                                                          "manual_issue": bool(card.get("manual_issue"))}
                                                         if card.get("issue_spec") else {})},
            question=card.get("question", ""), guidance_version_id=card.get("guidance_version_id"),
            priority=card["priority"], signature=sig, is_mock=bool(card.get("is_mock")))
        if sig in responded:
            row.status, row.status_reason = "suppressed", "Unchanged evidence and rules since your earlier response."
            stats["suppressed"] += 1
        elif sig not in seen:
            stats["new_notifications"] += 1
        session.add(row)
        session.flush()
        spec = card.get("issue_spec")
        if spec and not card.get("manual_issue"):
            key = issue_service.match_key(revision.assessment_id, spec["criterion_key"], spec["component_role"],
                                          spec["rubric_version_id"], spec["rule_hash"], spec["issue_type"],
                                          spec["question_key"])
            issue = session.scalar(select(CalibrationIssue).where(CalibrationIssue.match_key == key,
                                                                  CalibrationIssue.state.in_(issue_service.OPEN_STATES)))
            if issue is not None:
                issue_service.link_case(session, issue, revision.submission_id, actor, "auto_signature", revision.id,
                                        row.id, sig)
                row.issue_id = issue.id
                count = issue_service.affected_count(session, issue.id)
                row.body += (f" This question is already {'awaiting head-TA review' if issue.state == 'awaiting_head_ta' else 'open as a shared issue'}"
                             f" (#{issue.id}, {count} affected submissions); no need to ask again.")
                stats["linked_to_issue"] += 1
        stats["cards"] += 1
    return stats


def _peer_refresh_targets(session: Session, revision: DraftGradeRevision) -> list[int]:
    """Earlier drafted submissions that compared against an older revision of this one."""
    targets = []
    for fb in session.scalars(select(DraftFeedback).where(DraftFeedback.assessment_id == revision.assessment_id,
                                                          DraftFeedback.submission_id != revision.submission_id,
                                                          DraftFeedback.status.in_(("completed", "partial")))):
        compared = (fb.coverage or {}).get("peers", {}).get("compared", [])
        if any(c["submission_id"] == revision.submission_id and c["revision_id"] != revision.id for c in compared):
            current = current_revision(session, fb.submission_id)
            if current is not None and current.id == fb.revision_id:
                targets.append(fb.submission_id)
    return sorted(set(targets))


def run_checks(feedback_id: int, registry: Any, actor: str = "system") -> None:
    started = time.monotonic()
    with session_scope() as s:
        feedback = s.get(DraftFeedback, feedback_id)
        revision = s.get(DraftGradeRevision, feedback.revision_id)
        current = current_revision(s, revision.submission_id)
        if feedback.status in ("superseded", "stale") or current is None or current.id != revision.id:
            feedback.status = "stale" if feedback.status != "superseded" else feedback.status
            feedback.finished_at = utcnow()
            return
        feedback.status, feedback.started_at = "running", utcnow()
    with session_scope() as s:
        feedback = s.get(DraftFeedback, feedback_id)
        revision = s.get(DraftGradeRevision, feedback.revision_id)
        ctx = build_context(s, revision)
        g_cards, covered = guidance_cards(ctx)
        d_cards = [c for c in detector_cards(ctx) if not (c.get("issue_spec") and issue_service.match_key(
            revision.assessment_id, c["issue_spec"]["criterion_key"], c["issue_spec"]["component_role"],
            c["issue_spec"]["rubric_version_id"], c["issue_spec"]["rule_hash"], c["issue_spec"]["issue_type"],
            c["issue_spec"]["question_key"]) in covered)]
        p_cards, peer_info = peer_cards(ctx)
        coverage = {**coverage_info(ctx), "peers": peer_info}
        signature = audit.stable_hash([CHECK_VERSION, revision.id, [a.content_sha256 for a in ctx["submission"].artifacts],
                                       [s_.note_version_id for s_ in ctx["specs"]],
                                       [c["peer_revision_id"] for c in [x.get("comparison", {}) for x in p_cards]
                                        if c.get("peer_revision_id")],
                                       ctx["rubric"]["version"].id if ctx["rubric"] else None])
        cards = g_cards + d_cards + p_cards
        current = current_revision(s, revision.submission_id)
        if current.id != revision.id:
            feedback.status, feedback.finished_at = "stale", utcnow()
            feedback.error = "A newer revision was saved while this check ran; results kept as history only."
            return
        stats = persist_cards(s, feedback, revision, cards, ctx["rubric"], actor)
        feedback.signature, feedback.coverage = signature, coverage
        feedback.inputs = {"check_version": CHECK_VERSION, "guidance_versions": [x.note_version_id for x in ctx["specs"]],
                           "peer_revisions": [c["revision_id"] for c in peer_info.get("compared", [])],
                           "rubric_version_id": coverage.get("rubric_version_id"),
                           "source_hashes": [a.content_sha256 for a in ctx["submission"].artifacts]}
        feedback.stages = {"deterministic": {"status": "completed", **stats,
                                             "ms": int((time.monotonic() - started) * 1000)}}
        mode = "mock" if ctx["assessment"].is_synthetic else None
        artifact_ids = [a.id for a in ctx["submission"].artifacts]
        assessment_id = ctx["assessment"].id
    # The model job uses the serialized inference lane; CPU feedback has its own lane.
    with session_scope() as s:
        model_run = AnalysisRun(assessment_id=assessment_id, kind="draft_model", status="queued", actor=actor,
                                params={"feedback_id": feedback_id, "artifact_ids": artifact_ids,
                                        "provider_mode": mode})
        s.add(model_run)
        s.flush()
        feedback = s.get(DraftFeedback, feedback_id)
        stages = dict(feedback.stages or {})
        stages["deterministic_ready_ms"] = _elapsed_ms(feedback.created_at)
        stages["model_source_analysis"] = {"status": "queued", "run_id": model_run.id}
        feedback.stages = stages


def finalize_feedback(feedback_id: int, actor: str) -> str:
    with session_scope() as s:
        feedback = s.get(DraftFeedback, feedback_id)
        revision = s.get(DraftGradeRevision, feedback.revision_id)
        current = current_revision(s, revision.submission_id)
        if current.id != revision.id or feedback.status in ("stale", "superseded"):
            feedback.status = "stale"
            for card in s.scalars(select(FeedbackCard).where(FeedbackCard.feedback_id == feedback.id)):
                if card.status == "open":
                    card.status, card.status_reason = "superseded", "A newer revision exists."
        else:
            failed = any(isinstance(v, dict) and v.get("status") in ("failed", "partial")
                         for v in (feedback.stages or {}).values())
            feedback.status = "partial" if failed else "completed"
        feedback.finished_at = utcnow()
        stages = dict(feedback.stages or {})
        stages["total_ms"] = _elapsed_ms(feedback.started_at)
        feedback.stages = stages
        s.add(GradingEvent(actor=actor, event_type="feedback_completed", assessment_id=revision.assessment_id,
                           submission_id=revision.submission_id, revision_id=revision.id,
                           detail={"feedback_id": feedback.id, "status": feedback.status,
                                   "latency_ms": _elapsed_ms(feedback.created_at),
                                   "deterministic_ready_ms": stages.get("deterministic_ready_ms")}))
        targets = _peer_refresh_targets(s, revision) if feedback.status in ("completed", "partial")             and feedback.trigger == "draft_save" else []
        if targets:
            enqueue_refresh(s, revision.assessment_id, targets, actor, reason=f"new peer draft revision {revision.id}")
        return feedback.status


def run_draft_model(run_id: int, registry: Any) -> None:
    with session_scope() as s:
        run = s.get(AnalysisRun, run_id)
        feedback_id, artifact_ids, actor = int(run.params["feedback_id"]), run.params["artifact_ids"], run.actor
        feedback = s.get(DraftFeedback, feedback_id)
        revision = s.get(DraftGradeRevision, feedback.revision_id)
        stale = (current_revision(s, revision.submission_id).id != revision.id
                 or feedback.status in ("stale", "superseded"))
        assessment_id = feedback.assessment_id
        if stale:  # a newer revision exists: do not spend the GPU on outdated work
            stages = dict(feedback.stages or {})
            stages["model_source_analysis"] = {"status": "skipped", "reason": "revision superseded before it ran"}
            feedback.stages = stages
    if not stale:
        _model_stage(feedback_id, artifact_ids, assessment_id, registry, None)
    status = finalize_feedback(feedback_id, actor)
    with session_scope() as s:
        run = s.get(AnalysisRun, run_id)
        run.status = "succeeded" if status in ("completed", "stale") else "partial"
        run.coverage = {"feedback_status": status}


def _elapsed_ms(start) -> int | None:
    """Queue-to-completion time; SQLite returns naive UTC datetimes."""
    from datetime import timezone

    if start is None:
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    return int((utcnow() - start).total_seconds() * 1000)


def _model_stage(feedback_id: int, artifact_ids: list[int], assessment_id: int, registry: Any, mode: str | None) -> None:
    """Bounded source analysis reused from the analyst cache; failures never lose the draft."""
    from app.analysis.analyst import run_analyst
    from app.db.models import Artifact

    run_registry = registry  # already resolved per run (mock only for synthetic data)
    result: dict[str, Any] = {"status": "completed", "artifacts": 0, "cached_or_new": [], "errors": []}
    t0 = time.monotonic()
    for artifact_id in artifact_ids:
        with session_scope() as s:
            artifact = s.get(Artifact, artifact_id)
            assessment = s.get(AssessmentVersion, assessment_id)
            try:
                evidence = run_analyst(s, run_registry, artifact, assessment)
            except Exception as exc:  # provider construction errors are reported, not raised
                result["status"] = "failed"
                result["errors"].append(f"{type(exc).__name__}: {exc}")
                continue
            result["artifacts"] += 1
            result["cached_or_new"].append({"artifact_id": artifact_id, "status": evidence.status,
                                            "is_mock": evidence.is_mock, "error": evidence.error_kind,
                                            "coverage_status": (evidence.evidence or {}).get("coverage_status"),
                                            "partition": (evidence.usage or {}).get("partition")})
            if evidence.status == "partially_valid" and result["status"] != "failed":
                result["status"] = "partial"
            if evidence.status == "failed":
                result["status"] = "failed"
                result["errors"].append(evidence.error_kind or "failed")
                if evidence.error_kind in ("model_missing", "unreachable", "cloud_disabled", "missing_credentials"):
                    break
    result["ms"] = int((time.monotonic() - t0) * 1000)
    with session_scope() as s:
        feedback = s.get(DraftFeedback, feedback_id)
        stages = dict(feedback.stages or {})
        stages["model_source_analysis"] = result
        feedback.stages = stages


# ---------------------------------------------------------------- refresh batching


def enqueue_refresh(session: Session, assessment_id: int, submission_ids: list[int] | None, actor: str, reason: str,
                    provider_mode: str | None = None, all_drafted: bool = False,
                    priority_ids: list[int] | None = None) -> AnalysisRun | None:
    if all_drafted:
        drafted = sorted(set(session.scalars(select(DraftGradeRevision.submission_id).where(
            DraftGradeRevision.assessment_id == assessment_id))))
        ids = sorted(set(priority_ids or []) | set(drafted))
    else:
        ids = sorted(set(submission_ids or []))
    ids = [i for i in ids if current_revision(session, i) is not None]
    if not ids:
        return None
    if provider_mode is None:
        provider_mode = "mock" if session.get(AssessmentVersion, assessment_id).is_synthetic else None
    queued = session.scalar(select(AnalysisRun).where(AnalysisRun.assessment_id == assessment_id,
                                                      AnalysisRun.kind == "feedback_refresh",
                                                      AnalysisRun.status == "queued"))
    if queued is not None:  # batch rather than notify per neighbour
        params = dict(queued.params)
        params["submission_ids"] = sorted(set(params.get("submission_ids", [])) | set(ids))
        params["reasons"] = params.get("reasons", []) + [reason]
        queued.params = params
        return queued
    run = AnalysisRun(assessment_id=assessment_id, kind="feedback_refresh", status="queued", actor=actor,
                      params={"submission_ids": ids, "reasons": [reason], "provider_mode": provider_mode})
    session.add(run)
    session.flush()
    return run


def run_draft_check(run_id: int, registry: Any) -> None:
    with session_scope() as s:
        run = s.get(AnalysisRun, run_id)
        feedback_id = int(run.params["feedback_id"])
        actor = run.actor
    run_checks(feedback_id, registry, actor)
    with session_scope() as s:
        run = s.get(AnalysisRun, run_id)
        feedback = s.get(DraftFeedback, feedback_id)
        run.status = "succeeded" if feedback.status in ("running", "completed", "stale", "superseded") else "partial"
        run.coverage = {"feedback_status": feedback.status, "stages": feedback.stages,
                        "note": "Deterministic feedback is ready; the model pass runs as a separate job."}


def run_feedback_refresh(run_id: int, registry: Any) -> None:
    with session_scope() as s:
        run = s.get(AnalysisRun, run_id)
        ids = list(run.params.get("submission_ids", []))
        reasons = run.params.get("reasons", [])
        actor = run.actor
        feedback_ids = []
        for sub_id in ids:
            revision = current_revision(s, sub_id)
            if revision is None:
                continue
            fb = DraftFeedback(revision_id=revision.id, submission_id=sub_id, assessment_id=revision.assessment_id,
                               trigger="refresh", status="queued", run_id=run_id,
                               inputs={"reasons": reasons})
            s.add(fb)
            s.flush()
            feedback_ids.append(fb.id)
    statuses = {}
    for fid in feedback_ids:
        with session_scope() as s:
            if s.get(AnalysisRun, run_id).cancel_requested:
                raise Cancelled()
        run_checks(fid, registry, actor)
        with session_scope() as s:
            statuses[fid] = s.get(DraftFeedback, fid).status
    with session_scope() as s:
        run = s.get(AnalysisRun, run_id)
        run.coverage = {"refreshed": len(feedback_ids), "statuses": statuses, "reasons": reasons}
        run.status = "succeeded" if all(v in ("running", "completed", "stale") for v in statuses.values()) else "partial"


def current_feedback(session: Session, submission_id: int) -> DraftFeedback | None:
    revision = current_revision(session, submission_id)
    if revision is None:
        return None
    return session.scalar(select(DraftFeedback).where(DraftFeedback.revision_id == revision.id)
                          .order_by(DraftFeedback.id.desc()).limit(1))


def card_view(session: Session, card: FeedbackCard) -> dict[str, Any]:
    issue = session.get(CalibrationIssue, card.issue_id) if card.issue_id else None
    dispositions = list(session.scalars(select(FeedbackDisposition).where(FeedbackDisposition.card_id == card.id)
                                        .order_by(FeedbackDisposition.id)))
    return {"id": card.id, "feedback_id": card.feedback_id, "revision_id": card.revision_id, "type": card.card_type,
            "criterion_key": card.criterion_key, "component_role": card.component_role, "title": card.title,
            "body": card.body, "evidence": card.evidence, "rubric_refs": card.rubric_refs,
            "comparison": {k: v for k, v in card.comparison.items() if k != "issue_spec"},
            "question": card.question, "can_request_calibration": bool(card.comparison.get("issue_spec")),
            "issue": issue_service.issue_view(session, issue) if issue else None,
            "guidance_version_id": card.guidance_version_id, "priority": round(card.priority, 2),
            "status": card.status, "status_reason": card.status_reason, "is_mock": card.is_mock,
            "dispositions": [{"action": d.action, "explanation": d.explanation, "actor": d.actor,
                              "new_revision_id": d.new_revision_id,
                              "at": d.created_at.isoformat()} for d in dispositions]}


def feedback_view(session: Session, feedback: DraftFeedback | None, include_all: bool = True) -> dict[str, Any] | None:
    if feedback is None:
        return None
    revision = session.get(DraftGradeRevision, feedback.revision_id)
    current = current_revision(session, revision.submission_id)
    cards = [card_view(session, c) for c in session.scalars(select(FeedbackCard).where(
        FeedbackCard.feedback_id == feedback.id).order_by(FeedbackCard.priority.desc(), FeedbackCard.id))]
    actionable = [c for c in cards if c["status"] in ("open",)]
    return {"id": feedback.id, "revision_id": feedback.revision_id, "revision_number": revision.revision_number,
            "status": feedback.status,
            "is_current": current is not None and current.id == feedback.revision_id and feedback.status != "stale",
            "trigger": feedback.trigger, "coverage": feedback.coverage, "stages": feedback.stages,
            "inputs": feedback.inputs, "error": feedback.error,
            "initial_cards": actionable[:MAX_INITIAL_CARDS], "all_cards": cards if include_all else [],
            "counts": {"actionable": len(actionable), "total": len(cards),
                       "suppressed": sum(1 for c in cards if c["status"] == "suppressed")},
            "created_at": feedback.created_at.isoformat() if feedback.created_at else None,
            "finished_at": feedback.finished_at.isoformat() if feedback.finished_at else None}


__all__ = ["enqueue_check", "enqueue_refresh", "run_draft_check", "run_feedback_refresh", "current_feedback",
           "feedback_view", "card_view", "IssueCaseLink"]
