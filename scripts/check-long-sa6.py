r"""Opt-in local-only smoke check; separate ignored database, no policy or grades changed.

Run from the repository root:
  backend\.venv\Scripts\python.exe scripts\check-long-sa6.py
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select
from app.analysis.reviewer import run_reviewer
from app.config import Settings
from app.db.models import AssessmentVersion, Submission
from app.db.session import init_engine, session_scope
from app.importers.service import build_preview, commit_import
from app.providers.registry import ProviderRegistry


def original_hashes():
    paths = [ROOT / "cs1_scores_anon.csv"] + sorted(p for folder in ROOT.glob("SA[1-6]")
                                                   for p in folder.rglob("*") if p.is_file())
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def main():
    before = original_hashes()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    outdir = ROOT / "data" / "private" / f"long-sa6-check-{stamp}"
    outdir.mkdir(parents=True)
    settings = Settings(_env_file=None, database_url=f"sqlite:///{(outdir/'check.db').as_posix()}",
                        private_data_dir=str(outdir), start_worker=False, allow_cloud_inference=False,
                        analyst_provider="ollama", reviewer_provider="ollama", embedding_provider="ollama")
    init_engine(settings.effective_database_url)
    registry = ProviderRegistry(settings)
    preview = build_preview(ROOT/"cs1_scores_anon.csv",ROOT/"SA6",settings.filename_id_regex,
                            "local-long-sa6-check","SA6")
    with session_scope() as session:
        _, assessment, _ = commit_import(session,preview,"local-smoke-check")
        aid = assessment.id
    start = time.monotonic()
    try:
        with session_scope() as session:
            assessment = session.get(AssessmentVersion,aid)
            submissions = list(session.scalars(select(Submission).where(Submission.assessment_id==aid)))
            longest = sorted(submissions,key=lambda s:sum(len(a.source_text) for a in s.artifacts),reverse=True)[:2]
            print("Reviewing the two longest SA6 bundles with local Ollama; no instructions or policy supplied.", flush=True)
            result = run_reviewer(session,registry,*longest,assessment,include_summaries=False)
            report = {"status":result.status,"relation":(result.result or {}).get("relation"),
                      "coverage":(result.result or {}).get("coverage_status"),
                      "provider":result.provider,"model":result.model,"is_mock":result.is_mock,
                      "source_characters":[sum(len(a.source_text) for a in s.artifacts) for s in longest],
                      "error_kind":result.error_kind,"error_message":result.error_message,
                      "partition":(result.usage or {}).get("partition"),"usage":result.usage,
                      "seconds":round(time.monotonic()-start,2)}
    finally:
        registry.ollama.close()
    report["originals_unchanged"] = before == original_hashes()
    report["original_files_checked"] = len(before)
    (outdir/"report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    partition = report["partition"] or {}
    print(json.dumps({k:v for k,v in report.items() if k not in ("partition","usage")},indent=2))
    print("Window statuses:", [p["status"] for p in partition.get("windows",[])])
    print("Report:",outdir/"report.json")
    if not report["originals_unchanged"] or report["status"]=="failed" or partition.get("errors"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
