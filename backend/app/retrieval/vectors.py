"""Source and summary embeddings (kept as separate channels).

Vectors are L2-normalised float32, cached by representation + input hash +
provider/model/digest + preprocessing. Missing or failed embeddings stay
visible as unavailable; they are never treated as zero similarity.
"""
from __future__ import annotations

from typing import Any, Callable

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.db.models import Artifact, EmbeddingRecord, SubmissionEvidence
from app.providers.base import ProviderError
from app.providers.registry import ProviderRegistry

SOURCE_REP = "source_v1"
SUMMARY_REP = "summary_v1"
SOURCE_PREPROCESSING = "line-chunks-40-mean-l2-v1"
SUMMARY_PREPROCESSING = "validated-facts-l2-v1"
MAX_SPLIT_DEPTH = 3


def current_digest(registry: ProviderRegistry) -> str:
    provider = registry.embedding()
    if provider.name == "ollama":
        info = registry.ollama.require_model(provider.model)
        return info.get("digest") or "unknown"
    return "mock"


def embedding_key(rep: str, input_sha: str, provider: str, model: str, digest: str, preprocessing: str) -> str:
    return audit.stable_hash([rep, input_sha, provider, model, digest, preprocessing])


def _normalise(vectors: list[list[float]]) -> np.ndarray:
    matrix = np.asarray(vectors, dtype=np.float32)
    mean = matrix.mean(axis=0)
    norm = float(np.linalg.norm(mean))
    return mean / norm if norm > 0 else mean


def _embed_text_with_split(registry: ProviderRegistry, text: str, rep: str, depth: int = 0) -> list[list[float]]:
    """Explicit chunking for overlength input instead of silent truncation."""
    try:
        return registry.embedding().embed([text], rep).vectors
    except ProviderError as exc:
        lines = text.splitlines(keepends=True)
        if exc.kind != "overlength" or depth >= MAX_SPLIT_DEPTH or len(lines) < 2:
            raise
        middle = len(lines) // 2
        return (_embed_text_with_split(registry, "".join(lines[:middle]), rep, depth + 1)
                + _embed_text_with_split(registry, "".join(lines[middle:]), rep, depth + 1))


def _store(session: Session, key: str, rep: str, input_sha: str, provider: Any, digest: str, preprocessing: str,
           vector: np.ndarray | None, error: ProviderError | None) -> EmbeddingRecord:
    row = session.scalar(select(EmbeddingRecord).where(EmbeddingRecord.cache_key == key))
    if row is None:
        row = EmbeddingRecord(cache_key=key, representation=rep, input_sha256=input_sha, provider=provider.name,
                              model=provider.model, model_digest=digest, preprocessing=preprocessing,
                              is_mock=provider.name == "mock", status="failed")
        session.add(row)
    if vector is not None:
        row.vector, row.dims, row.status = vector.astype(np.float32).tobytes(), int(vector.shape[0]), "ok"
        row.error_kind = row.error_message = None
    else:
        row.status, row.error_kind, row.error_message = "failed", error.kind, error.message
    session.flush()
    return row


def ensure_embeddings(session: Session, registry: ProviderRegistry, items: list[tuple[str, list[str]]], rep: str,
                      preprocessing: str, cancel_check: Callable[[], bool] | None = None) -> dict[str, Any]:
    """items: (input_sha, texts). Each item's vector is the normalised mean of its texts."""
    stats = {"total": len(items), "cached": 0, "embedded": 0, "failed": 0, "error": None}
    provider = registry.embedding()
    try:
        digest = current_digest(registry)
    except ProviderError as exc:
        stats["failed"] = len(items)
        stats["error"] = {"kind": exc.kind, "message": exc.message}
        return stats
    pending = []
    for input_sha, texts in items:
        key = embedding_key(rep, input_sha, provider.name, provider.model, digest, preprocessing)
        row = session.scalar(select(EmbeddingRecord).where(EmbeddingRecord.cache_key == key))
        if row is not None and row.status == "ok":
            stats["cached"] += 1
        else:
            pending.append((key, input_sha, texts))
    for key, input_sha, texts in pending:
        if cancel_check and cancel_check():
            break
        try:
            vectors: list[list[float]] = []
            for text in texts:
                vectors.extend(_embed_text_with_split(registry, text, rep))
            _store(session, key, rep, input_sha, provider, digest, preprocessing, _normalise(vectors), None)
            stats["embedded"] += 1
        except ProviderError as exc:
            _store(session, key, rep, input_sha, provider, digest, preprocessing, None, exc)
            stats["failed"] += 1
            stats["error"] = {"kind": exc.kind, "message": exc.message}
            if exc.provider_unavailable:
                stats["failed"] += len(pending) - stats["embedded"] - stats["failed"]
                break
    return stats


def source_items(artifacts: list[Artifact]) -> list[tuple[str, list[str]]]:
    seen, items = set(), []
    for artifact in artifacts:
        if artifact.content_sha256 not in seen:
            seen.add(artifact.content_sha256)
            items.append((artifact.content_sha256, [c.text for c in artifact.chunks if c.text.strip()] or [" "]))
    return items


def summary_items(evidences: list[SubmissionEvidence]) -> list[tuple[str, list[str]]]:
    seen, items = set(), []
    for ev in evidences:
        if ev.status != "failed" and ev.summary_text.strip():
            sha = audit.sha256_text(ev.summary_text)
            if sha not in seen:
                seen.add(sha)
                items.append((sha, [ev.summary_text]))
    return items


def load_vector(session: Session, registry: ProviderRegistry, rep: str, input_sha: str,
                preprocessing: str) -> tuple[np.ndarray | None, str | None]:
    provider = registry.embedding()
    try:
        digest = current_digest(registry)
    except ProviderError as exc:
        return None, f"embedding provider unavailable ({exc.kind})"
    key = embedding_key(rep, input_sha, provider.name, provider.model, digest, preprocessing)
    row = session.scalar(select(EmbeddingRecord).where(EmbeddingRecord.cache_key == key))
    if row is None:
        return None, "not embedded"
    if row.status != "ok" or row.vector is None:
        return None, f"embedding failed ({row.error_kind})"
    return np.frombuffer(row.vector, dtype=np.float32), None
