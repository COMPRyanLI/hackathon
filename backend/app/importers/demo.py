"""Import the SYNTHETIC demo fixture as its own dataset, labeled synthetic."""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT
from app.importers.service import build_preview, commit_import

DEMO_DIR = PROJECT_ROOT / "fixtures" / "synthetic" / "demo"


def demo_manifest() -> dict:
    return json.loads((DEMO_DIR / "manifest.json").read_text(encoding="utf-8"))


def import_synthetic_demo(session: Session, actor: str):
    manifest = demo_manifest()
    preview = build_preview(DEMO_DIR / manifest["csv"], DEMO_DIR / manifest["source_dir"],
                            manifest["filename_regex"], manifest["dataset_key"], manifest["assessment_name"])
    return commit_import(session, preview, actor, is_synthetic=True, context={
        "instructions_status": "synthetic_demo",
        "instructions_text": manifest["instructions"],
    })
