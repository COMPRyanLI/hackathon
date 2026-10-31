"""Evidence-reference validation.

Every model claim must cite an opaque artifact label, that artifact's source
hash, a real chunk id, and a quote that occurs verbatim in that chunk. Claims
with no valid reference are rejected and recorded, never silently kept.
Valid quotes do not prove the interpretation is right; they make it checkable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.db.models import Artifact

MIN_QUOTE_CHARS = 4


@dataclass
class ArtifactView:
    label: str
    artifact_id: int
    content_sha256: str
    chunks: dict[str, tuple[str, int]]  # chunk_key -> (text, start_line)

    @property
    def short_hash(self) -> str:
        return self.content_sha256[:8]


def build_view(artifact: Artifact, label: str) -> ArtifactView:
    return ArtifactView(label, artifact.id, artifact.content_sha256,
                        {c.chunk_key: (c.text, c.start_line) for c in artifact.chunks})


def resolve_ref(ref: dict[str, Any], views: dict[str, ArtifactView]) -> tuple[dict[str, Any] | None, str | None]:
    view = views.get(str(ref.get("artifact", "")).strip())
    if view is None:
        return None, "unknown_artifact"
    given_hash = str(ref.get("source_hash", "")).strip().lower()
    if not (len(given_hash) >= 8 and view.content_sha256.startswith(given_hash)):
        return None, "source_hash_mismatch"
    chunk = view.chunks.get(str(ref.get("chunk_id", "")).strip())
    if chunk is None:
        return None, "unknown_chunk"
    text, start_line = chunk
    quote = str(ref.get("quote", ""))
    if len(quote.strip()) < MIN_QUOTE_CHARS:
        return None, "quote_too_short"
    index = text.find(quote)
    if index < 0:
        quote = quote.strip()
        index = text.find(quote)
    if index < 0:
        return None, "quote_not_found"
    line_start = start_line + text[:index].count("\n")
    return {
        "artifact": view.label,
        "artifact_id": view.artifact_id,
        "source_sha256": view.content_sha256,
        "chunk_id": ref["chunk_id"],
        "quote": quote,
        "line_start": line_start,
        "line_end": line_start + quote.count("\n"),
    }, None


def validate_claims(claims: list[dict[str, Any]], views: dict[str, ArtifactView], field: str,
                    text_key: str = "text") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for claim in claims:
        resolved, failures = [], []
        for ref in claim.get("evidence_refs", []):
            ok, reason = resolve_ref(ref, views)
            if ok:
                resolved.append(ok)
            else:
                failures.append({"ref": ref, "reason": reason})
        if resolved:
            kept = {k: v for k, v in claim.items() if k != "evidence_refs"}
            kept["evidence_refs"] = resolved
            if failures:
                kept["dropped_refs"] = len(failures)
                rejected.append({"field": field, "text": claim.get(text_key, ""), "reason": "some_refs_invalid",
                                 "failures": failures, "claim_kept": True})
            accepted.append(kept)
        else:
            rejected.append({"field": field, "text": claim.get(text_key, ""),
                             "reason": "no_valid_evidence" if failures else "no_evidence_refs",
                             "failures": failures, "claim_kept": False})
    return accepted, rejected
