"""Typed, component-scoped conditions for criterion guidance.

    {"component": "program_1", "fact": "loop_inclusive_bound", "op": "==", "value": true}
    {"all": [...]}, {"any": [...]}, {"not": {...}}

Facts come from ``extraction.facts`` (application code). Component scope is
enforced through current component mappings; an unmapped or ambiguous
component, or an undeterminable fact, yields unknown (never false). A model
may *suggest* a condition, but only a validated, approved one is evaluated.
"""
from __future__ import annotations

import math
import operator
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import AssessmentVersion, Submission
from app.extraction.facts import SCALAR_FACTS, fact_lines, scalar_fact
from app.importers.multi import current_roles, facts_for
from app.rubrics.engine import PointError, treatment_proposal

OPS = {"==": operator.eq, "!=": operator.ne, "<": operator.lt, "<=": operator.le, ">": operator.gt,
       ">=": operator.ge}
TREATMENT_ACTIONS = ("award", "deduct", "range", "no_numeric_rule")
SEMANTIC_HEURISTICS = {"diagonal_check_present", "diagonal_check_complete", "backtracking_present"}


def validate_scoped(cond: Any, path: str = "condition") -> list[str]:
    if not isinstance(cond, dict) or not cond:
        return [f"{path}: must be a non-empty object"]
    combinators = {"all", "any", "not"} & cond.keys()
    if combinators:
        if len(cond) != 1:
            return [f"{path}: a combinator must be the only key"]
        key = next(iter(combinators))
        if key == "not":
            return validate_scoped(cond["not"], f"{path}.not")
        items = cond[key]
        if not isinstance(items, list) or not items:
            return [f"{path}.{key}: must be a non-empty list"]
        return [e for i, item in enumerate(items) for e in validate_scoped(item, f"{path}.{key}[{i}]")]
    errors = []
    if set(cond) - {"component", "fact", "op", "value"}:
        errors.append(f"{path}: unexpected keys {sorted(set(cond) - {'component', 'fact', 'op', 'value'})}")
    if not isinstance(cond.get("component"), str) or not cond["component"]:
        errors.append(f"{path}: component is required (a role such as program_1, or 'any')")
    fact = cond.get("fact")
    if fact not in SCALAR_FACTS:
        errors.append(f"{path}: unsupported fact {fact!r}")
    if cond.get("op") not in OPS:
        errors.append(f"{path}: op must be one of {', '.join(OPS)}")
    value = cond.get("value")
    if isinstance(value, bool):
        if fact in SCALAR_FACTS and SCALAR_FACTS[fact] is not bool:
            errors.append(f"{path}: {fact} is numeric")
    elif isinstance(value, (int, float)) and math.isfinite(value):
        if fact in SCALAR_FACTS and SCALAR_FACTS[fact] is bool:
            errors.append(f"{path}: {fact} is boolean")
    else:
        errors.append(f"{path}: value must be a boolean or finite number")
    return errors


def validate_treatment(treatment: Any) -> list[str]:
    if not isinstance(treatment, dict) or treatment.get("action") not in TREATMENT_ACTIONS:
        return [f"point_treatment.action must be one of {', '.join(TREATMENT_ACTIONS)}"]
    return []


def evaluate_scoped(session: Session, cond: dict[str, Any], submission: Submission) -> tuple[bool | None, list[dict]]:
    if "all" in cond or "any" in cond:
        key = "all" if "all" in cond else "any"
        results = [evaluate_scoped(session, c, submission) for c in cond[key]]
        values = [r for r, _ in results]
        evidence = [e for _, ev in results for e in ev]
        if key == "all":
            return (False if False in values else None if None in values else True), evidence
        return (True if True in values else None if None in values else False), evidence
    if "not" in cond:
        value, evidence = evaluate_scoped(session, cond["not"], submission)
        return (None if value is None else not value), evidence
    roles = current_roles(session, submission)
    component = cond["component"]
    if component == "any":
        artifacts = list(submission.artifacts)
    else:
        artifacts = [a for a in submission.artifacts if a.id in roles and roles[a.id].role == component
                     and roles[a.id].status in ("proposed", "confirmed")]
    if not artifacts:
        ambiguous = any(m.status == "ambiguous" for m in roles.values())
        return None, [{"component": component, "fact": cond["fact"], "status": "unknown",
                       "reason": "component mapping is ambiguous" if ambiguous else "no file mapped to this component"}]
    values, evidence = [], []
    assessment = session.get(AssessmentVersion, submission.assessment_id)
    for artifact in artifacts:
        facts = facts_for(artifact)
        actual = scalar_fact(facts, cond["fact"])
        if cond["fact"] in SEMANTIC_HEURISTICS and not assessment.is_synthetic:
            # These names predate P1's real-data review. Their implementation
            # recognizes syntax patterns, not the named semantic property.
            # Known synthetic examples exercise the demo; real work needs an
            # independent applicability judgment before a numeric proposal.
            values.append(None)
            evidence.append({"artifact_id": artifact.id, "component": component, "fact": cond["fact"],
                             "actual": None, "observed_pattern": actual, "result": None,
                             "status": "unknown", "lines": fact_lines(facts, cond["fact"]),
                             "reason": "Pattern detection cannot establish this semantic property on real code; "
                                       "review applicability individually. An approved point scale alone is insufficient."})
            continue
        result = None if actual is None else bool(OPS[cond["op"]](actual, cond["value"]))
        values.append(result)
        evidence.append({"artifact_id": artifact.id, "component": component, "fact": cond["fact"], "actual": actual,
                         "op": cond["op"], "expected": cond["value"], "result": result,
                         "lines": fact_lines(facts, cond["fact"])})
    if None in values:
        return None, evidence
    return any(values), evidence


def evaluate_spec(session: Session, spec: Any, submission: Submission, criterion: Any | None) -> dict[str, Any]:
    """Applicability of one approved typed rule to one submission (+ criterion proposal)."""
    value, evidence = evaluate_scoped(session, spec.condition, submission)
    if value is None:
        return {"status": "uncertain", "rationale": "A fact or component needed by the rule is unknown; this is "
                                                    "not 'does not apply'.", "evidence": evidence, "proposal": None}
    if value is False:
        return {"status": "does_not_apply", "rationale": "The rule's typed condition does not hold.",
                "evidence": evidence, "proposal": None}
    for exception in spec.exception_conditions or []:
        ex_value, ex_evidence = evaluate_scoped(session, exception["condition"], submission)
        if ex_value is None:
            return {"status": "uncertain", "rationale": f"Exception '{exception.get('description', '')}' could not be "
                                                        "evaluated; individual review needed.",
                    "evidence": evidence + ex_evidence, "proposal": None, "exception": exception}
        if ex_value:
            return {"status": "uncertain", "rationale": f"Matches the listed exception '{exception.get('description', '')}'"
                                                        "; remains reviewable individually.",
                    "evidence": evidence + ex_evidence, "proposal": None, "exception": exception}
    try:
        proposal = treatment_proposal(spec.point_treatment, criterion.max_points if criterion else None)
    except PointError as exc:
        proposal = {"kind": "invalid", "error": str(exc)}
    return {"status": "applies", "rationale": "The rule's typed condition holds and no listed exception matches.",
            "evidence": evidence, "proposal": proposal}
