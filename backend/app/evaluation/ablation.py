"""Evaluation and ablations (plan section 10).

Retrieval ablation: recall of human-labeled useful pairs within a fixed
candidate budget for lexical+AST, source-vector-only, and full hybrid
retrieval (including summary vectors). Optional reviewer ablation: the
reviewer on original source only vs. analyst summaries plus source, compared
with labeled expected relations.

Results are only as good as the labels. The bundled labels describe the
SYNTHETIC demo fixture; real-class recall needs independent human labels and
must not be generalized from an incompletely labeled sample.

Usage (from backend/):
    python -m app.evaluation.ablation --assessment-id 2 --labels ../fixtures/synthetic/demo/eval_labels.json
    python -m app.evaluation.ablation --assessment-id 2 --labels ... --reviewer --provider-mode configured
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app import audit
from app.analysis.analyst import cached_evidence, run_analyst
from app.analysis.reviewer import run_reviewer
from app.config import get_settings
from app.db.models import AssessmentVersion, Student, Submission
from app.db.session import init_engine, session_scope
from app.providers.registry import ProviderRegistry
from app.retrieval.hybrid import Unit, retrieve
from app.retrieval.vectors import (
    SOURCE_PREPROCESSING,
    SOURCE_REP,
    SUMMARY_PREPROCESSING,
    SUMMARY_REP,
    ensure_embeddings,
    load_vector,
    source_items,
    summary_items,
)

CONFIGS = {
    "lexical_ast_only": ("lexical", "structural"),
    "source_vectors_only": ("source_vector",),
    "hybrid_with_summary_vectors": ("exact", "lexical", "structural", "source_vector", "summary_vector"),
}


def _units(s, registry: ProviderRegistry, assessment: AssessmentVersion, prepare: bool) -> tuple[list[Unit], dict[int, str]]:
    subs = list(s.scalars(select(Submission).where(Submission.assessment_id == assessment.id).order_by(Submission.id)))
    codes = {sub.id: s.get(Student, sub.student_id).code for sub in subs}
    artifacts = [a for sub in subs for a in sub.artifacts]
    if prepare:
        for artifact in artifacts:
            run_analyst(s, registry, artifact, assessment)
        ensure_embeddings(s, registry, source_items(artifacts), SOURCE_REP, SOURCE_PREPROCESSING)
        evidences = [e for e in (cached_evidence(s, registry, a, assessment) for a in artifacts) if e]
        ensure_embeddings(s, registry, summary_items(evidences), SUMMARY_REP, SUMMARY_PREPROCESSING)
    units = []
    for sub in subs:
        for artifact in sub.artifacts:
            unit = Unit(artifact.id, sub.id, artifact.content_sha256, artifact.source_text, artifact.features or {})
            unit.source_vector, unit.source_vector_reason = load_vector(s, registry, SOURCE_REP,
                                                                        artifact.content_sha256, SOURCE_PREPROCESSING)
            ev = cached_evidence(s, registry, artifact, assessment)
            if ev is not None and ev.status != "failed" and ev.summary_text.strip():
                unit.summary_vector, unit.summary_vector_reason = load_vector(
                    s, registry, SUMMARY_REP, audit.sha256_text(ev.summary_text), SUMMARY_PREPROCESSING)
            else:
                unit.summary_vector_reason = "no validated summary"
            units.append(unit)
    return units, codes


def retrieval_ablation(units: list[Unit], codes: dict[int, str], labels: list[dict[str, Any]], budget: int,
                       top_k: int) -> dict[str, Any]:
    by_code = {code: sid for sid, code in codes.items()}
    wanted = {tuple(sorted((by_code[a], by_code[b]))): f"{a}-{b}" for a, b in (l["pair"] for l in labels)}
    out = {}
    for name, enabled in CONFIGS.items():
        result = retrieve(units, top_k, enabled=enabled)
        ranked = sorted(result["bundles"], key=lambda k: (-result["bundles"][k]["fused"], k))
        ranked = [k for k in ranked if result["bundles"][k]["fused"] > 0]
        positions = {wanted[k]: (ranked.index(k) + 1 if k in ranked else None) for k in wanted}
        hits = sum(1 for pos in positions.values() if pos is not None and pos <= budget)
        out[name] = {"recall_at_budget": round(hits / len(wanted), 3) if wanted else None,
                     "budget": budget, "labeled_pairs": len(wanted), "rank_of_each_labeled_pair": positions,
                     "candidates_retrieved": len(ranked),
                     "unavailable_channels": {c: v["reasons"] for c, v in result["availability"].items()
                                              if c in enabled and v["available_artifacts"] < v["total_artifacts"]}}
    return out


def reviewer_ablation(s, registry: ProviderRegistry, assessment: AssessmentVersion, codes: dict[int, str],
                      labels: list[dict[str, Any]]) -> dict[str, Any]:
    by_code = {code: sid for sid, code in codes.items()}
    rows = []
    for label in labels:
        a, b = sorted((by_code[label["pair"][0]], by_code[label["pair"][1]]))
        sub_a, sub_b = s.get(Submission, a), s.get(Submission, b)
        row = {"pair": "-".join(label["pair"]), "expected": label.get("expected_relation")}
        for variant, include in (("source_only", False), ("summaries_plus_source", True)):
            started = time.monotonic()
            comparison = run_reviewer(s, registry, sub_a, sub_b, assessment, include_summaries=include)
            row[variant] = {
                "status": comparison.status, "error": comparison.error_kind,
                "relation": (comparison.result or {}).get("relation"),
                "matches_label": (comparison.result or {}).get("relation") == label.get("expected_relation"),
                "rejected_claims": len(comparison.rejected_claims or []),
                "contradictions": len((comparison.result or {}).get("contradicted_analyst_claims", [])),
                "seconds": round(time.monotonic() - started, 1), "is_mock": comparison.is_mock,
                "usage": comparison.usage,
            }
        rows.append(row)
    summary = {}
    for variant in ("source_only", "summaries_plus_source"):
        done = [r[variant] for r in rows if r[variant]["status"] != "failed"]
        summary[variant] = {"completed": len(done), "failed": len(rows) - len(done),
                            "relation_matches_label": sum(1 for d in done if d["matches_label"]),
                            "unsupported_claims_rejected": sum(d["rejected_claims"] for d in done)}
    return {"pairs": rows, "summary": summary}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--assessment-id", type=int, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--budget", type=int, default=10)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--provider-mode", choices=("configured", "mock"), default="configured")
    parser.add_argument("--prepare", action="store_true", help="run analyst + embeddings first if not cached")
    parser.add_argument("--reviewer", action="store_true", help="also run the reviewer ablation (model calls)")
    parser.add_argument("--out", type=Path, help="write JSON results here (keep under data/private for real data)")
    args = parser.parse_args()

    settings = get_settings()
    init_engine(settings.effective_database_url)
    registry = ProviderRegistry(settings).for_mode(args.provider_mode)
    labels_doc = json.loads(args.labels.read_text(encoding="utf-8"))
    with session_scope() as s:
        assessment = s.get(AssessmentVersion, args.assessment_id)
        if assessment is None:
            raise SystemExit(f"assessment {args.assessment_id} not found")
        if args.provider_mode == "mock" and not assessment.is_synthetic:
            raise SystemExit("mock mode is only allowed for synthetic assessments")
        units, codes = _units(s, registry, assessment, args.prepare)
        report: dict[str, Any] = {
            "labels": labels_doc.get("label", ""), "assessment_id": assessment.id,
            "synthetic": assessment.is_synthetic, "providers": registry.snapshot(),
            "retrieval": retrieval_ablation(units, codes, labels_doc["useful_pairs"], args.budget, args.top_k),
            "caveat": "Small labeled set; existing scores and model judgments are not ground truth.",
        }
        if args.reviewer:
            report["reviewer"] = reviewer_ablation(s, registry, assessment, codes, labels_doc["useful_pairs"])
    text = json.dumps(report, indent=2, default=str)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
