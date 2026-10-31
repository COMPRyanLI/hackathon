"""MOCK providers - explicitly selected, always labeled ``is_mock``.

* Scripted responses come only from synthetic fixture files (each file says
  so) and are matched by the content hashes of synthetic sources.
* Without a scripted entry the mock is conservative: the analyst restates
  static facts with exact quotes, the reviewer returns insufficient_evidence,
  the note scan returns uncertain. It never invents findings.
* Mock embeddings are hashed character n-grams: deterministic, local, NOT
  semantic. They exist so the pipeline can be exercised without Ollama.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from app.providers.base import EmbeddingResult, GenerationSettings, StructuredResult

MOCK_GENERATION_MODEL = "mock-scripted-v1"
MOCK_EMBEDDING_MODEL = "mock-hash-embedding-v1"
MOCK_LABEL = "MOCK PROVIDER OUTPUT - not produced by a language model; not a real finding."


def fixture_key(role: str, content_hashes: list[str], extra: str = "") -> str:
    return f"{role}:{'|'.join(sorted(content_hashes))}" + (f":{extra}" if extra else "")


def load_scripted(fixture_dir: Path) -> dict[str, dict[str, Any]]:
    """Load scripted synthetic responses keyed by the hash of referenced sources."""
    scripted: dict[str, dict[str, Any]] = {}
    if not fixture_dir.is_dir():
        return scripted
    base = fixture_dir.parent
    for path in sorted(fixture_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for entry in data.get("entries", []):
            hashes = {}
            for rel in entry["sources"]:
                hashes[rel] = hashlib.sha256((base / rel).read_bytes()).hexdigest()
            key = fixture_key(entry["role"], list(hashes.values()), entry.get("extra", ""))
            scripted[key] = {"response": entry["response"], "source_hashes": hashes,
                             "label": data.get("label", "SYNTHETIC FIXTURE")}
    return scripted


def _substitute_refs(node: Any, source_hashes: dict[str, str], labels_by_sha: dict[str, str]) -> Any:
    if isinstance(node, dict):
        out = {k: _substitute_refs(v, source_hashes, labels_by_sha) for k, v in node.items()}
        artifact = out.get("artifact")
        if isinstance(artifact, str) and artifact.startswith("@"):
            sha = source_hashes.get(artifact[1:], "")
            out["artifact"] = labels_by_sha.get(sha, "artifact_unknown")
            if out.get("source_hash") in (None, "", "@"):
                out["source_hash"] = sha[:8]
        return out
    if isinstance(node, list):
        return [_substitute_refs(v, source_hashes, labels_by_sha) for v in node]
    return node


def _first_line(chunks: list[dict[str, Any]], pattern: str) -> tuple[str, str] | None:
    regex = re.compile(pattern)
    for chunk in chunks:
        for line in chunk["text"].splitlines():
            if regex.search(line) and line.strip():
                return chunk["key"], line
    return None


def _fallback_analyst(ctx: dict[str, Any]) -> dict[str, Any]:
    label, short, chunks, features = ctx["label"], ctx["short_hash"], ctx["chunks"], ctx["features"]
    observations = []

    def observe(text: str, pattern: str) -> None:
        found = _first_line(chunks, pattern)
        if found:
            observations.append({"text": text, "evidence_refs": [
                {"artifact": label, "source_hash": short, "chunk_id": found[0], "quote": found[1]}]})

    count = features.get("print_call_count", features.get("text_print_call_count", 0))
    if count:
        observe(f"Contains {count} print call(s) (static count).", r"\bprint\s*\(")
    if features.get("loop_count"):
        observe("Contains at least one loop construct (static count).", r"^\s*(for|while)\b")
    if features.get("function_def_count"):
        observe(f"Defines {features['function_def_count']} function(s) (static count).", r"^\s*def\b")
    if features.get("imports"):
        observe(f"Imports: {', '.join(features['imports'])}.", r"^\s*(import|from)\b")
    limitations = []
    if features.get("placeholder_count"):
        limitations.append("Anonymization placeholders are present; identifiers or printed text may be altered.")
    if not features.get("parse_ok", True):
        limitations.append("Static parsing failed; AST facts are unavailable.")
    return {
        "observed_purpose": MOCK_LABEL,
        "approach_summary": "MOCK: deterministic restatement of static facts only; no semantic analysis.",
        "explicit_requirement_evidence": [],
        "factual_observations": observations,
        "possible_issues": [],
        "anonymization_limitations": limitations,
        "unknowns": ["Mock provider: the program's content and quality were not analyzed."],
        "coverage_status": "full_source_seen",
    }


def _fallback_reviewer(ctx: dict[str, Any]) -> dict[str, Any]:
    return {
        "relation": "insufficient_evidence",
        "scope": ctx.get("expected_scope", "component"),
        "requirement_basis": "unavailable",
        "shared_observations": [],
        "material_differences": [],
        "contradicted_analyst_claims": [],
        "alternative_explanations": [],
        "missing_context": ["MOCK PROVIDER: no semantic review was performed; a real reviewer model is required."],
        "clarification_question": "",
        "suggested_category": "incomplete_or_anonymized_evidence",
        "coverage_status": "full_source_seen",
    }


def _fallback_note(_ctx: dict[str, Any]) -> dict[str, Any]:
    return {"status": "uncertain", "rationale": "MOCK PROVIDER: applicability was not evaluated by a model.",
            "evidence": []}


_FALLBACKS = {"analyst": _fallback_analyst, "reviewer": _fallback_reviewer, "note_applicability": _fallback_note}


class MockGenerationProvider:
    name = "mock"

    def __init__(self, fixture_dir: Path | None = None, model: str = MOCK_GENERATION_MODEL):
        self.model = model
        self.scripted = load_scripted(fixture_dir) if fixture_dir else {}
        self.calls: list[dict[str, Any]] = []  # inspected by tests

    def generate_structured(self, role: str, messages: list[dict[str, str]], schema: dict[str, Any],
                            settings: GenerationSettings) -> StructuredResult:
        self.calls.append({"role": role, "messages": messages, "fixture_key": settings.fixture_key})
        ctx = settings.mock_context or {}
        entry = self.scripted.get(settings.fixture_key or "")
        if entry is not None:
            data = _substitute_refs(entry["response"], entry["source_hashes"], ctx.get("labels_by_sha", {}))
            usage = {"scripted_fixture": True, "fixture_label": entry["label"]}
        else:
            data = _FALLBACKS[role](ctx)
            usage = {"scripted_fixture": False}
        return StructuredResult(data, json.dumps(data), self.name, self.model, "mock", True, usage, 0)


class MockEmbeddingProvider:
    name = "mock"

    def __init__(self, dims: int = 256, model: str = MOCK_EMBEDDING_MODEL):
        self.dims = dims
        self.model = model

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dims
        features = re.findall(r"\w+", text.lower()) + [text[i:i + 3] for i in range(max(0, len(text) - 2))]
        for feature in features:
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % self.dims
            vec[index] += 1.0 if digest[4] & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed(self, texts: list[str], representation_version: str) -> EmbeddingResult:
        return EmbeddingResult([self._vector(t) for t in texts], self.name, self.model, "mock", self.dims, True, 0)
