"""Import the SYNTHETIC P1 fixture (SA3- and SA6-style tasks, synthetic guide, synthetic flags)."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT
from app.importers.flags import import_flags
from app.importers.multi import prepare_assessment
from app.importers.service import build_preview, commit_import
from app.rubrics.service import activate, import_text, link_assessment

P1_DIR = PROJECT_ROOT / "fixtures" / "synthetic" / "p1"
DATASET = "synthetic-p1"
REGEX = r"^(?P<student_id>P[0-9]+)(?:_.*)?\.py$"


def import_synthetic_p1(session: Session, actor: str) -> dict[str, Any]:
    rubric, _ = import_text(session, P1_DIR / "guide.txt", actor, is_synthetic=True)
    if rubric.status != "active":
        activate(session, rubric.id, actor)
    out: dict[str, Any] = {"rubric_version_id": rubric.id, "assessments": {}}
    for code in ("SA3", "SA6"):
        preview = build_preview(P1_DIR / "scores.csv", P1_DIR / code, REGEX, DATASET, code)
        _, assessment, created = commit_import(session, preview, actor, is_synthetic=True, context={
            "instructions_status": "synthetic_demo",
            "instructions_text": f"SYNTHETIC {code}-style demo task (invented). Real assignment instructions are "
                                 "not part of this fixture."})
        link_assessment(session, assessment.id, rubric.id, code, actor)
        out["assessments"][code] = {"assessment_id": assessment.id, "created": created,
                                    **prepare_assessment(session, assessment, actor)}
    out["flags"] = import_flags(session, P1_DIR / "flags.csv", DATASET, actor)
    return out
