"""Deterministic point arithmetic. Models never choose totals.

* Decimal arithmetic with explicit ROUND_HALF_UP to 0.01.
* Parent and child criteria are never both added: a container contributes its
  children, or its own maximum when it has none.
* An unresolved criterion with maximum m contributes [0, m] to an
  *information bound* (not a confidence interval). Bounds and totals are
  refused when a blocking rubric issue exists for the assignment.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Iterable

CENT = Decimal("0.01")


class PointError(ValueError):
    pass


def to_decimal(value: Any) -> Decimal:
    if value is None or value == "":
        raise PointError("missing point value")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise PointError(f"not a number: {value!r}") from exc
    if not number.is_finite():
        raise PointError(f"not a finite number: {value!r}")
    return number.quantize(CENT, rounding=ROUND_HALF_UP)


def fmt(value: Decimal | None) -> str | None:
    return None if value is None else format(value.quantize(CENT, rounding=ROUND_HALF_UP).normalize(), "f")


def scoring_units(criteria: Iterable[Any], code: str) -> list[Any]:
    """Leaf award criteria that make up an assignment total (containers expanded)."""
    rows = [c for c in criteria if c.assignment == code]
    by_parent: dict[str | None, list[Any]] = {}
    for c in rows:
        by_parent.setdefault(c.parent_key, []).append(c)
    units: list[Any] = []

    def expand(key: str) -> None:
        for child in by_parent.get(key, []):
            if child.rule_kind != "award":
                continue
            award_children = [c for c in by_parent.get(child.criterion_key, []) if c.rule_kind == "award"]
            if child.is_container and award_children:
                expand(child.criterion_key)
            else:
                units.append(child)
    expand(code)
    return units


def information_bound(criteria: Iterable[Any], code: str, known: dict[str, Any],
                      blocking: list[str]) -> dict[str, Any]:
    if blocking:
        return {"status": "blocked", "reasons": blocking, "lower": None, "upper": None}
    units = scoring_units(list(criteria), code)
    lower = Decimal(0)
    unresolved = Decimal(0)
    known_keys, unresolved_keys = [], []
    for unit in units:
        maximum = to_decimal(unit.max_points) if unit.max_points is not None else None
        if unit.criterion_key in known:
            value = to_decimal(known[unit.criterion_key])
            if value < 0 or (maximum is not None and value > maximum):
                raise PointError(f"{unit.criterion_key}: {value} outside [0, {maximum}]")
            lower += value
            known_keys.append(unit.criterion_key)
        else:
            if maximum is None:
                return {"status": "blocked", "reasons": [f"{unit.criterion_key} has no maximum"], "lower": None,
                        "upper": None}
            unresolved += maximum
            unresolved_keys.append(unit.criterion_key)
    return {"status": "ok", "lower": fmt(lower), "upper": fmt(lower + unresolved),
            "known": known_keys, "unresolved": unresolved_keys,
            "note": "Information bound from resolved and unresolved criteria; not a confidence interval and not "
                    "evidence that any recorded score is correct."}


def treatment_proposal(treatment: dict[str, Any], max_points: Any) -> dict[str, Any]:
    """Turn an approved point treatment into a criterion proposal, bounded by the rule."""
    maximum = to_decimal(max_points) if max_points is not None else None
    action = treatment.get("action")
    if action == "award":
        value = to_decimal(treatment["points"])
    elif action == "deduct":
        if maximum is None:
            raise PointError("a deduction needs a criterion maximum")
        value = maximum - to_decimal(treatment["points"])
    elif action == "range":
        low, high = to_decimal(treatment["min"]), to_decimal(treatment["max"])
        if low > high or low < 0 or (maximum is not None and high > maximum):
            raise PointError("invalid range")
        return {"kind": "range", "min": fmt(low), "max": fmt(high), "max_points": fmt(maximum)}
    elif action in ("no_numeric_rule", None):
        return {"kind": "no_numeric_rule"}
    else:
        raise PointError(f"unknown action {action!r}")
    if value < 0 or (maximum is not None and value > maximum):
        raise PointError(f"proposal {value} outside [0, {maximum}]")
    return {"kind": "points", "points": fmt(value), "max_points": fmt(maximum)}


def compare_with_draft(proposal: dict[str, Any], criterion_key: str, draft_points: dict[str, Any]) -> dict[str, Any]:
    """Backend comparison of a policy proposal with the grader's draft (never shown to a model)."""
    if proposal.get("kind") == "no_numeric_rule":
        return {"status": "no_numeric_rule"}
    if criterion_key not in (draft_points or {}):
        return {"status": "no_criterion_points",
                "note": "Total-only draft: the proposal cannot be compared with a recorded criterion deduction."}
    draft = to_decimal(draft_points[criterion_key])
    if proposal["kind"] == "points":
        same = draft == to_decimal(proposal["points"])
        return {"status": "consistent" if same else "differs", "draft": fmt(draft), "proposal": proposal["points"]}
    inside = to_decimal(proposal["min"]) <= draft <= to_decimal(proposal["max"])
    return {"status": "consistent" if inside else "differs", "draft": fmt(draft),
            "proposal": f"{proposal['min']}-{proposal['max']}"}
