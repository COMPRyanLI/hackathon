from __future__ import annotations

from functools import lru_cache

from app import audit
from app.config import PROJECT_ROOT

PROMPT_DIR = PROJECT_ROOT / "prompts"


@lru_cache
def load_prompt(name: str) -> tuple[str, str]:
    """Return (text, version). The version changes whenever the file changes."""
    text = (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")
    return text, f"{name}@{audit.sha256_text(text)[:12]}"


def chunk_listing(label: str, short_hash: str, chunks: list[tuple[str, int, int, str]]) -> str:
    """Source shown with explicit boundaries; quotes must be copied from inside them."""
    parts = [f"ARTIFACT {label} (source_hash {short_hash})"]
    for key, start, end, text in chunks:
        parts.append(f"<<<BEGIN {label} chunk {key} (lines {start}-{end})>>>\n{text}"
                     f"{'' if text.endswith(chr(10)) else chr(10)}<<<END {label} chunk {key}>>>")
    return "\n".join(parts)
