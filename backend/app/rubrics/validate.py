"""Rubric validation. Every finding is recorded as an issue with page/quote
provenance; nothing is silently corrected. Issues that make a total
arithmetic interpretation unsafe block total proposals for that assignment
while criterion-level evidence review continues."""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from app.rubrics.parse import IN_SCOPE, DraftCriterion, ParsedGuide


@dataclass
class DraftIssue:
    assignment: str
    criterion_key: str | None
    kind: str
    message: str
    page: int | None
    source_quote: str
    blocks_total_proposals: bool


def _children(criteria: list[DraftCriterion], key: str) -> list[DraftCriterion]:
    return [c for c in criteria if c.parent_key == key]


def _award_units(criteria: list[DraftCriterion], key: str) -> list[DraftCriterion]:
    return [c for c in _children(criteria, key) if c.rule_kind == "award"]


def validate(parsed: ParsedGuide) -> list[DraftIssue]:
    crit = parsed.criteria
    issues: list[DraftIssue] = []
    by_key = {c.key: c for c in crit}

    for code in sorted(parsed.headings):
        root = by_key.get(code)
        if root is None:
            continue
        units = _award_units(crit, code)
        total = sum((c.max_points or Decimal(0) for c in units), Decimal(0))
        if total != parsed.headings[code]:
            parts = " + ".join(str(c.max_points) for c in units)
            issues.append(DraftIssue(
                code, code, "arithmetic_conflict",
                f"{code} heading says {parsed.headings[code]} points, but the listed award items sum to "
                f"{parts} = {total}. Final total proposals are blocked until the head TA reconciles this; "
                f"individual criteria can still be reviewed.",
                root.page, root.source_quote, True))
        for container in [c for c in units if c.is_container and c.component_role]:
            sub = _award_units(crit, container.key)
            sub_total = sum((c.max_points or Decimal(0) for c in sub), Decimal(0))
            if container.max_points is not None and sub_total != container.max_points:
                issues.append(DraftIssue(
                    code, container.key, "arithmetic_conflict",
                    f"{container.title} lists {container.max_points} points but its items sum to {sub_total}.",
                    container.page, container.source_quote, True))
        for parent in [c for c in crit if c.assignment == code]:
            deductions = [d for d in _children(crit, parent.key) if d.rule_kind == "deduction"]
            if not deductions:
                continue
            ded_total = sum((d.max_points or Decimal(0) for d in deductions), Decimal(0))
            if parent.max_points is not None and ded_total > parent.max_points:
                issues.append(DraftIssue(
                    code, parent.key, "arithmetic_conflict",
                    f"Deductions under '{parent.title[:60]}' total {ded_total}, more than its {parent.max_points} points.",
                    parent.page, parent.source_quote, True))
            for d in deductions:
                if d.phrased_as_award:
                    alt = total + (d.max_points or Decimal(0))
                    issues.append(DraftIssue(
                        code, d.key, "structure_ambiguous",
                        f"'{d.title[:70]}' appears in a deduction list but is phrased as an award. If it is a "
                        f"separate award the {code} items sum to {alt}; as a deduction inside its parent they sum to "
                        f"{total}. Treatment is not specified; do not double-penalize.",
                        d.page, d.source_quote, True))

    for item in [c for c in crit if c.assignment == "shared" and c.applies_to]:
        for code in sorted(IN_SCOPE - set(item.applies_to)):
            issues.append(DraftIssue(
                code, item.key, "exemption_unresolved",
                f"'{item.title[:60]}' ({item.max_points} points) does not apply to {code}. The guide does not say "
                f"whether that {item.max_points} is awarded, redistributed or removed from the practice category.",
                item.page, item.source_quote, True))

    for c in crit:
        text = c.source_quote
        if re.search(r"partial credit", text, re.I):
            issues.append(DraftIssue(
                c.assignment, c.key, "partial_credit_undefined",
                f"{c.max_points} points awarded by judgment ('partial credit'); no point scale is defined. "
                "Draft anchors need head-TA approval before proposals use them.",
                c.page, text, False))
        if re.search(r"check with me", text, re.I):
            issues.append(DraftIssue(
                c.assignment, c.key, "escalation_indicated",
                "The guide asks graders to check with the instructor when required (in-app indication only).",
                c.page, text, False))
        if re.search(r"code that I provided", text, re.I):
            issues.append(DraftIssue(
                c.assignment, c.key, "scaffold_unknown",
                "Starter code is not available, so preserved-scaffold checks cannot be resolved; common code is "
                "only a scaffold candidate.", c.page, text, False))
        if c.rule_kind == "resubmit":
            issues.append(DraftIssue(
                c.assignment, c.key, "precedence_unresolved",
                "Exceptional zero/resubmission policy. Its scope and precedence over criterion points are not "
                "specified; every match needs authorized human confirmation and is never an automatic verdict.",
                c.page, text, False))
    for code in sorted(parsed.headings):
        external = [c for c in crit if c.assignment == code and c.classification == "external_evidence_required"]
        if external:
            pts = sum((c.max_points or Decimal(0) for c in external), Decimal(0))
            issues.append(DraftIssue(
                code, None, "external_evidence",
                f"{len(external)} criteria ({pts} points) need evidence outside this code export (screenshots, "
                "outputs, assignment specification or starter code). They stay unresolved, not zero.",
                external[0].page, "; ".join(c.title[:40] for c in external), False))
    return issues
