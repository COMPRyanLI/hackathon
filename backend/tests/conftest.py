"""Test helpers. All test data is synthetic and generated here or read from
fixtures/synthetic/. Real class files are only touched by tests marked
``realdata`` (skipped when the private snapshot is absent)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pytest

from app.config import Settings
from app.db.session import init_engine, session_scope
from app.importers.service import build_preview, commit_import
from app.providers.base import GenerationSettings, ProviderError, StructuredResult
from app.providers.registry import ProviderRegistry

REGEX = r"^(?P<student_id>S[0-9]+)(?:_.*)?\.py$"


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values = dict(
        _env_file=None,
        private_data_dir=str(tmp_path / "private"),
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        analyst_provider="mock", analyst_model="mock-scripted-v1",
        reviewer_provider="mock", reviewer_model="mock-scripted-v1",
        embedding_provider="mock", embedding_model="mock-hash-embedding-v1",
        start_worker=False,
    )
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def db(settings: Settings):
    init_engine(settings.effective_database_url)
    yield


@pytest.fixture
def registry(settings: Settings) -> ProviderRegistry:
    return ProviderRegistry(settings)


def write_dataset(root: Path, rows: list[tuple], files: dict[str, str],
                  header: tuple = ("student_id", "grader", "SA1")) -> tuple[Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    src = root / "src"
    src.mkdir(exist_ok=True)
    lines = [",".join(header)] + [",".join("" if v is None else str(v) for v in row) for row in rows]
    csv_path = root / "scores.csv"
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for name, text in files.items():
        (src / name).write_bytes(text.encode("utf-8"))
    return csv_path, src


def import_dataset(csv_path: Path, src: Path, dataset_key: str = "test", actor: str = "tester",
                   regex: str = REGEX, **kwargs: Any):
    preview = build_preview(csv_path, src, regex, dataset_key, "SA1")
    with session_scope() as s:
        batch, assessment, created = commit_import(s, preview, actor, **kwargs)
        return batch.id, assessment.id, created


class ScriptedProvider:
    """Test double: returns responses produced by a callable, or raises."""

    name = "mock"

    def __init__(self, respond: Callable[[str, list[dict[str, str]], GenerationSettings], dict[str, Any]],
                 model: str = "scripted-test"):
        self.respond = respond
        self.model = model
        self.calls: list[dict[str, Any]] = []

    def generate_structured(self, role, messages, schema, settings) -> StructuredResult:
        self.calls.append({"role": role, "messages": messages, "settings": settings})
        data = self.respond(role, messages, settings)
        if isinstance(data, ProviderError):
            raise data
        return StructuredResult(data, "{}", self.name, self.model, "test", True, {}, 0)


PRINT3 = 'print("{a}")\nprint("{b}")\nprint("{c}")\n'


def run_pipeline_sync(assessment_id: int, registry: ProviderRegistry, submission_ids: list[int] | None = None,
                      budget: int | None = None, mode: str | None = None) -> int:
    from sqlalchemy import select

    from app.db.models import AnalysisRun, Submission
    from app.jobs.worker import process_queued

    with session_scope() as s:
        ids = submission_ids or list(s.scalars(select(Submission.id).where(Submission.assessment_id == assessment_id)))
        run = AnalysisRun(assessment_id=assessment_id, kind="pipeline", status="queued", actor="tester",
                          params={"submission_ids": sorted(ids), "review_budget": budget, "provider_mode": mode})
        s.add(run)
        s.flush()
        run_id = run.id
    process_queued(registry)
    return run_id


def submission_ids_for(assessment_id: int, codes: list[str]) -> list[int]:
    from sqlalchemy import select

    from app.db.models import Student, Submission

    with session_scope() as s:
        rows = s.execute(select(Submission.id, Student.code).join(Student, Student.id == Submission.student_id)
                         .where(Submission.assessment_id == assessment_id)).all()
        by_code = {code: sid for sid, code in rows}
        return [by_code[c] for c in codes]
