"""Stage C: hybrid candidate retrieval with per-channel diagnostics.

Channels: exact (byte-identical content), lexical (TF-IDF over code tokens and
string trigrams), structural (AST feature counts), source vectors and summary
vectors. Each channel ranks per artifact; ranks are fused by reciprocal rank
fusion and lifted to bundle pairs. Raw scores from different channels are never
averaged. Unavailable channels cast no vote and are disclosed. Similarity is a
retrieval signal only: not correctness, fairness, or misconduct.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sklearn.feature_extraction import DictVectorizer
from sklearn.feature_extraction.text import TfidfVectorizer

from app.extraction.static import lexical_tokens, structural_vector

CHANNELS = ("exact", "lexical", "structural", "source_vector", "summary_vector")


@dataclass
class Unit:
    artifact_id: int
    submission_id: int
    content_sha256: str
    source_text: str
    features: dict[str, Any]
    source_vector: np.ndarray | None = None
    source_vector_reason: str | None = None
    summary_vector: np.ndarray | None = None
    summary_vector_reason: str | None = None


@dataclass
class ChannelResult:
    name: str
    sim: np.ndarray            # NaN where unavailable
    available: np.ndarray      # bool per unit
    unavailable_reason: dict[int, str] = field(default_factory=dict)


def _cosine_rows(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    unit = matrix / norms
    return unit @ unit.T


def lexical_channel(units: list[Unit]) -> ChannelResult:
    docs = [lexical_tokens(u.source_text) for u in units]
    vectorizer = TfidfVectorizer(analyzer=lambda tokens: tokens, sublinear_tf=True, norm="l2")
    matrix = vectorizer.fit_transform(docs)
    sim = (matrix @ matrix.T).toarray()
    return ChannelResult("lexical", sim, np.ones(len(units), dtype=bool))


def structural_channel(units: list[Unit]) -> ChannelResult:
    vectors = [structural_vector(u.features) for u in units]
    available = np.array([v is not None for v in vectors], dtype=bool)
    sim = np.full((len(units), len(units)), np.nan)
    reasons = {u.artifact_id: "AST unavailable (parse failed)" for u, v in zip(units, vectors) if v is None}
    idx = [i for i, ok in enumerate(available) if ok]
    if idx:
        dv = DictVectorizer(sparse=False)
        matrix = dv.fit_transform([{k: math.log1p(v) for k, v in vectors[i].items()} for i in idx])
        sub = _cosine_rows(matrix)
        for a, i in enumerate(idx):
            for b, j in enumerate(idx):
                sim[i, j] = sub[a, b]
    return ChannelResult("structural", sim, available, reasons)


def vector_channel(name: str, units: list[Unit], attr: str) -> ChannelResult:
    vectors = [getattr(u, attr) for u in units]
    reasons = {u.artifact_id: getattr(u, attr + "_reason") or "unavailable"
               for u, v in zip(units, vectors) if v is None}
    available = np.array([v is not None for v in vectors], dtype=bool)
    sim = np.full((len(units), len(units)), np.nan)
    idx = [i for i, ok in enumerate(available) if ok]
    dims = {vectors[i].shape[0] for i in idx}
    if len(dims) > 1:  # never compare vectors of different shapes/model versions
        return ChannelResult(name, sim, np.zeros(len(units), dtype=bool),
                             {u.artifact_id: "mixed embedding dimensions" for u in units})
    if idx:
        sub = _cosine_rows(np.stack([vectors[i] for i in idx]))
        for a, i in enumerate(idx):
            for b, j in enumerate(idx):
                sim[i, j] = sub[a, b]
    return ChannelResult(name, sim, available, reasons)


def exact_channel(units: list[Unit]) -> ChannelResult:
    n = len(units)
    sim = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            if units[i].content_sha256 == units[j].content_sha256:
                sim[i, j] = 1.0
    return ChannelResult("exact", sim, np.ones(n, dtype=bool))


def channel_ranks(units: list[Unit], channel: ChannelResult, top_k: int) -> dict[tuple[int, int], int]:
    """Per-artifact top-k neighbours from other bundles. Returns {(i, j): rank} with i<j,
    keeping the better rank of the two directions. Identical contents are left to
    the exact channel so a shared file cannot crowd out other candidates."""
    ranks: dict[tuple[int, int], int] = {}
    for i, unit in enumerate(units):
        if not channel.available[i]:
            continue
        candidates = []
        for j, other in enumerate(units):
            if other.submission_id == unit.submission_id or not channel.available[j]:
                continue
            same_content = other.content_sha256 == unit.content_sha256
            if channel.name == "exact":
                if same_content:
                    candidates.append((1.0, other.artifact_id, j))
                continue
            if same_content or np.isnan(channel.sim[i, j]):
                continue
            candidates.append((float(channel.sim[i, j]), other.artifact_id, j))
        candidates.sort(key=lambda c: (-c[0], c[1]))
        for rank, (_, _, j) in enumerate(candidates[:top_k], start=1):
            if channel.name == "exact":
                rank = 1
            key = (min(i, j), max(i, j))
            ranks[key] = min(rank, ranks.get(key, rank))
    return ranks


def retrieve(units: list[Unit], top_k: int, rrf_k: int = 60,
             enabled: tuple[str, ...] = CHANNELS) -> dict[str, Any]:
    """``enabled`` restricts which channels vote (used by ablations); all are still diagnosed."""
    channels = {
        "exact": exact_channel(units),
        "lexical": lexical_channel(units),
        "structural": structural_channel(units),
        "source_vector": vector_channel("source_vector", units, "source_vector"),
        "summary_vector": vector_channel("summary_vector", units, "summary_vector"),
    }
    ranks = {name: channel_ranks(units, ch, top_k) for name, ch in channels.items() if name in enabled}

    artifact_pairs: dict[tuple[int, int], dict[str, Any]] = {}
    for name, channel_rank in ranks.items():
        for (i, j), rank in channel_rank.items():
            entry = artifact_pairs.setdefault((i, j), {"ranks": {}, "rrf": 0.0})
            entry["ranks"][name] = rank
            entry["rrf"] += 1.0 / (rrf_k + rank)

    bundles: dict[tuple[int, int], dict[str, Any]] = {}
    for (i, j), entry in artifact_pairs.items():
        ui, uj = units[i], units[j]
        if ui.submission_id > uj.submission_id:
            ui, uj, i, j = uj, ui, j, i
        key = (ui.submission_id, uj.submission_id)
        bundle = bundles.setdefault(key, {"fused": 0.0, "contributing": []})
        bundle["contributing"].append({
            "artifact_a_id": ui.artifact_id, "artifact_b_id": uj.artifact_id,
            "identical_content": ui.content_sha256 == uj.content_sha256,
            "ranks": entry["ranks"], "rrf": round(entry["rrf"], 6),
            "scores": {name: (None if np.isnan(ch.sim[i, j]) else round(float(ch.sim[i, j]), 4))
                       for name, ch in channels.items()},
        })
        # Max over artifact pairs: extra files and a shared file cannot add up.
        bundle["fused"] = max(bundle["fused"], entry["rrf"])

    by_submission: dict[int, list[int]] = {}
    for idx, unit in enumerate(units):
        by_submission.setdefault(unit.submission_id, []).append(idx)
    for (sa, sb), bundle in bundles.items():
        diagnostics = {}
        for name, ch in channels.items():
            side_a = [i for i in by_submission[sa] if ch.available[i]]
            side_b = [j for j in by_submission[sb] if ch.available[j]]
            best_rank = min((c["ranks"][name] for c in bundle["contributing"] if name in c["ranks"]), default=None)
            if not side_a or not side_b:
                missing = [units[i].artifact_id for i in by_submission[sa] + by_submission[sb] if not ch.available[i]]
                diagnostics[name] = {"status": "unavailable", "rank": None, "score": None,
                                     "reason": "; ".join(sorted({ch.unavailable_reason.get(a, "unavailable")
                                                                 for a in missing}))}
                continue
            scores = [ch.sim[i, j] for i in side_a for j in side_b
                      if name == "exact" or units[i].content_sha256 != units[j].content_sha256]
            max_score = None if not scores else round(float(np.nanmax(scores)), 4)
            if name == "exact":
                max_score = 1.0 if any(units[i].content_sha256 == units[j].content_sha256
                                       for i in side_a for j in side_b) else 0.0
            diagnostics[name] = {"status": "ranked" if best_rank else "not_in_top_k", "rank": best_rank,
                                 "score": max_score}
            partial = [units[i].artifact_id for i in by_submission[sa] + by_submission[sb] if not ch.available[i]]
            if partial:
                diagnostics[name]["partial"] = "unavailable for some artifacts: " + "; ".join(
                    sorted({ch.unavailable_reason.get(a, "unavailable") for a in partial}))
        bundle["channels"] = diagnostics
        bundle["reasons"] = _reasons(bundle)

    availability = {name: {"available_artifacts": int(ch.available.sum()), "total_artifacts": len(units),
                           "reasons": sorted(set(ch.unavailable_reason.values()))}
                    for name, ch in channels.items()}
    return {"bundles": bundles, "availability": availability}


def _reasons(bundle: dict[str, Any]) -> list[str]:
    reasons = []
    for name, diag in bundle["channels"].items():
        if diag["status"] == "ranked":
            if name == "exact":
                reasons.append("exact: byte-identical file content (not evidence of equivalent bundles or misconduct)")
            else:
                reasons.append(f"{name}: rank {diag['rank']} (similarity {diag['score']}, retrieval diagnostic only)")
        elif diag["status"] == "unavailable":
            reasons.append(f"{name}: unavailable - {diag['reason']}")
    return reasons


def select_for_review(bundles: dict[tuple[int, int], dict[str, Any]], budget: int,
                      score_gap: dict[tuple[int, int], float | None], all_submission_ids: list[int],
                      seed: int, random_controls: int = 2, gap_share: float = 0.5) -> dict[tuple[int, int], str]:
    """Choose which retrieved pairs the reviewer sees. Scores are used only here,
    after retrieval, and are never shown to the reviewer."""
    selected: dict[tuple[int, int], str] = {}
    ordered = sorted(bundles, key=lambda k: (-bundles[k]["fused"], k))

    def take(key: tuple[int, int], reason: str) -> None:
        if key not in selected and len(selected) < budget:
            selected[key] = reason

    exact = [k for k in ordered if bundles[k]["channels"]["exact"]["status"] == "ranked"]
    for key in exact[: max(1, budget // 6)]:
        take(key, "exact_duplicate")
    for name in CHANNELS[1:]:
        unique = [k for k in ordered
                  if bundles[k]["channels"][name]["status"] == "ranked"
                  and sum(1 for d in bundles[k]["channels"].values() if d["status"] == "ranked") == 1]
        if unique:
            take(unique[0], f"unique_to_{name}")
    controls_budget = min(random_controls, max(0, budget // 10))
    remaining = max(0, budget - len(selected) - controls_budget)
    gap_slots = int(remaining * gap_share)
    pool = [k for k in ordered[: max(3 * budget, budget)] if k not in selected]
    by_gap = sorted((k for k in pool if score_gap.get(k)), key=lambda k: (-(score_gap[k] or 0), k))
    for key in by_gap[:gap_slots]:
        take(key, "retrieved_score_gap_priority")
    for key in ordered:
        if len(selected) >= budget - controls_budget:
            break
        take(key, "retrieved_top_fused")
    rng = random.Random(seed)
    outside = [(a, b) for i, a in enumerate(sorted(all_submission_ids)) for b in sorted(all_submission_ids)[i + 1:]
               if (a, b) not in bundles]
    rng.shuffle(outside)
    for key in outside[:controls_budget]:
        if len(selected) < budget:
            selected[key] = "random_control"
    return selected
