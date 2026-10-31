"""Deterministic differences between two bundles, given to the reviewer and
shown in the case view. These are facts about source text, not judgments."""
from __future__ import annotations

import difflib
from typing import Any

from app.db.models import Submission

COUNT_FEATURES = ("line_count", "print_call_count", "loop_count", "function_def_count", "branch_count",
                  "import_count", "string_literal_count", "placeholder_count")


def _side(sub: Submission) -> dict[str, Any]:
    totals: dict[str, Any] = {name: 0 for name in COUNT_FEATURES}
    literals: list[str] = []
    imports: set[str] = set()
    parse = []
    for artifact in sub.artifacts:
        features = artifact.features or {}
        for name in COUNT_FEATURES:
            value = features.get(name)
            if value is None and name == "print_call_count":
                value = features.get("text_print_call_count")
            totals[name] += int(value or 0)
        literals.extend(item["value"] for item in features.get("string_literals", []))
        imports.update(features.get("imports", []))
        parse.append(artifact.parse_status)
    return {"artifact_count": len(sub.artifacts), "totals": totals, "literals": literals,
            "imports": sorted(imports), "parse_statuses": parse}


def bundle_differences(sub_a: Submission, sub_b: Submission) -> dict[str, Any]:
    a, b = _side(sub_a), _side(sub_b)
    set_a, set_b = set(a["literals"]), set(b["literals"])
    source_a = "".join(x.source_text for x in sub_a.artifacts)
    source_b = "".join(x.source_text for x in sub_b.artifacts)
    ratio = difflib.SequenceMatcher(None, source_a.splitlines(), source_b.splitlines(), autojunk=False).ratio()
    identical = [
        {"artifact_a_id": x.id, "artifact_b_id": y.id}
        for x in sub_a.artifacts for y in sub_b.artifacts if x.content_sha256 == y.content_sha256
    ]
    return {
        "side_1": {k: v for k, v in a.items() if k != "literals"},
        "side_2": {k: v for k, v in b.items() if k != "literals"},
        "count_differences": {
            name: [a["totals"][name], b["totals"][name]]
            for name in COUNT_FEATURES if a["totals"][name] != b["totals"][name]
        },
        "string_literals": {
            "shared": len(set_a & set_b),
            "only_side_1": len(set_a - set_b),
            "only_side_2": len(set_b - set_a),
            "examples_only_side_1": [s[:80] for s in sorted(set_a - set_b)[:5]],
            "examples_only_side_2": [s[:80] for s in sorted(set_b - set_a)[:5]],
        },
        "line_sequence_similarity": round(ratio, 3),
        "identical_artifacts_across_sides": identical,
        "bundles_byte_identical": sub_a.bundle_hash == sub_b.bundle_hash,
    }


def differences_text(diff: dict[str, Any]) -> str:
    lines = ["DETERMINISTIC DIFFERENCES (computed by the application from source text):"]
    lines.append(f"- files: Submission 1 has {diff['side_1']['artifact_count']}, Submission 2 has "
                 f"{diff['side_2']['artifact_count']}")
    for name, (x, y) in diff["count_differences"].items():
        lines.append(f"- {name}: {x} vs {y}")
    if not diff["count_differences"]:
        lines.append("- static counts are equal")
    lit = diff["string_literals"]
    lines.append(f"- string literals: {lit['shared']} shared, {lit['only_side_1']} only in Submission 1, "
                 f"{lit['only_side_2']} only in Submission 2")
    lines.append(f"- parse status: {diff['side_1']['parse_statuses']} vs {diff['side_2']['parse_statuses']}")
    lines.append(f"- line-sequence similarity (difflib ratio, retrieval diagnostic only): "
                 f"{diff['line_sequence_similarity']}")
    if diff["identical_artifacts_across_sides"]:
        lines.append(f"- {len(diff['identical_artifacts_across_sides'])} file(s) are byte-identical across the "
                     "two submissions (this alone does not make the submissions equivalent)")
    return "\n".join(lines)
