"""Optional deterministic applicability conditions for instructor notes.

A small, explicit language over static features and source regexes, e.g.
    {"all": [{"feature": "loop_count", "op": "==", "value": 0},
             {"source_regex": "print\\(", "present": true}]}
An unavailable feature (for example AST counts after a parse failure) gives
``uncertain``, never a silent false.
"""
from __future__ import annotations

import operator
import re
import math
from typing import Any

from app.db.models import Artifact, Submission
from app.extraction.static import AST_FEATURES, TEXT_FEATURES

SUPPORTED_FEATURES = TEXT_FEATURES + AST_FEATURES
OPS = {"==": operator.eq, "!=": operator.ne, "<": operator.lt, "<=": operator.le, ">": operator.gt,
       ">=": operator.ge}
MAX_REGEX_LENGTH = 300


def validate_condition(cond: Any, path: str = "condition") -> list[str]:
    if not isinstance(cond, dict) or len(cond) == 0:
        return [f"{path}: must be a non-empty object"]
    selectors = {"all", "any", "not", "feature", "source_regex"} & cond.keys()
    if len(selectors) != 1:
        return [f"{path}: exactly one condition operator is required"]
    if "all" in cond or "any" in cond:
        key = "all" if "all" in cond else "any"
        items = cond[key]
        if not isinstance(items, list) or not items:
            return [f"{path}.{key}: must be a non-empty list"]
        return [e for i, item in enumerate(items) for e in validate_condition(item, f"{path}.{key}[{i}]")]
    if "not" in cond:
        return validate_condition(cond["not"], f"{path}.not")
    if "feature" in cond:
        errors = []
        if cond["feature"] not in SUPPORTED_FEATURES:
            errors.append(f"{path}: unsupported feature {cond['feature']!r}; supported: {', '.join(SUPPORTED_FEATURES)}")
        if cond.get("op") not in OPS:
            errors.append(f"{path}: op must be one of {', '.join(OPS)}")
        if "value" not in cond:
            errors.append(f"{path}: value is required")
        elif not isinstance(cond["value"], (int, float, bool)) or not math.isfinite(cond["value"]):
            errors.append(f"{path}: feature value must be a finite number or boolean")
        return errors
    if "source_regex" in cond:
        if "present" in cond and not isinstance(cond["present"], bool):
            return [f"{path}: present must be a boolean"]
        pattern = cond["source_regex"]
        if not isinstance(pattern, str) or not pattern or len(pattern) > MAX_REGEX_LENGTH:
            return [f"{path}: source_regex must be a non-empty string up to {MAX_REGEX_LENGTH} characters"]
        try:
            re.compile(pattern)
        except re.error as exc:
            return [f"{path}: invalid regex: {exc}"]
        return []
    return [f"{path}: expected one of all/any/not/feature/source_regex"]


def _evaluate(cond: dict[str, Any], artifact: Artifact) -> tuple[bool | None, list[dict[str, Any]]]:
    if "all" in cond or "any" in cond:
        key = "all" if "all" in cond else "any"
        results = [_evaluate(item, artifact) for item in cond[key]]
        evidence = [e for _, ev in results for e in ev]
        values = [r for r, _ in results]
        if key == "all":
            value = False if False in values else (None if None in values else True)
        else:
            value = True if True in values else (None if None in values else False)
        return value, evidence
    if "not" in cond:
        value, evidence = _evaluate(cond["not"], artifact)
        return (None if value is None else not value), evidence
    if "feature" in cond:
        name = cond["feature"]
        features = artifact.features or {}
        if name not in features:
            return None, [{"artifact_id": artifact.id, "feature": name, "status": "unavailable",
                           "reason": "AST unavailable (parse failed)" if not features.get("parse_ok") else "missing"}]
        actual = features[name]
        value = bool(OPS[cond["op"]](actual, cond["value"]))
        return value, [{"artifact_id": artifact.id, "feature": name, "actual": actual, "op": cond["op"],
                        "expected": cond["value"], "result": value}]
    pattern = re.compile(cond["source_regex"], re.MULTILINE)
    matches = []
    for number, line in enumerate(artifact.source_text.splitlines(), start=1):
        if pattern.search(line):
            matches.append({"line": number, "quote": line[:200]})
            if len(matches) >= 5:
                break
    present = bool(matches)
    expected = bool(cond.get("present", True))
    return present == expected, [{"artifact_id": artifact.id, "source_regex": cond["source_regex"],
                                  "present": present, "expected_present": expected, "matches": matches}]


def evaluate_submission(cond: dict[str, Any], submission: Submission) -> tuple[str, str, list[dict[str, Any]]]:
    per_artifact = [(artifact, *_evaluate(cond, artifact)) for artifact in submission.artifacts]
    values = [value for _, value, _ in per_artifact]
    evidence = [e for _, _, ev in per_artifact for e in ev]
    if values and all(v is True for v in values):
        return "applies", "Deterministic condition holds for every file in the submission.", evidence
    if values and all(v is False for v in values):
        return "does_not_apply", "Deterministic condition does not hold for any file in the submission.", evidence
    if None in values:
        return "uncertain", "A feature needed by the condition is unavailable for at least one file.", evidence
    return ("uncertain", "Files in this multi-file submission disagree and component scope is unconfirmed.",
            evidence)
