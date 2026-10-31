"""One CPU feedback lane and one serialized model lane over persistent SQLite jobs.

On startup, runs left in ``running`` are marked ``interrupted``; resuming
re-queues them and cache-valid items are skipped.
"""
from __future__ import annotations

import logging
import threading
import traceback

from sqlalchemy import case, select, update

from app import audit
from app.db.models import AnalysisRun, utcnow
from app.db.session import session_scope
from app.jobs.errors import Cancelled
from app.providers.registry import ProviderRegistry

log = logging.getLogger("gradelens.worker")
KIND_PRIORITY = {"draft_check": 0, "feedback_refresh": 1, "draft_model": 2, "note_scan": 3, "prerelease": 4,
                 "pipeline": 5}
RESUMABLE = ("interrupted", "failed", "cancelled", "partial")
INTERACTIVE_KINDS = ("draft_check", "feedback_refresh")
_claim_lock = threading.Lock()


def mark_interrupted() -> int:
    with session_scope() as s:
        result = s.execute(update(AnalysisRun).where(AnalysisRun.status == "running")
                           .values(status="interrupted", error="Server stopped while this run was in progress."))
        return result.rowcount or 0


def claim_next(interactive: bool | None = None) -> int | None:
    # Both lanes share one process. Serialize read-and-claim transactions.
    with _claim_lock:
        return _claim_next(interactive)


def _claim_next(interactive: bool | None) -> int | None:
    with session_scope() as s:
        # Interactive draft feedback first, then batched refreshes, scans, and long pipelines.
        priority = case(KIND_PRIORITY, value=AnalysisRun.kind, else_=9)
        query = select(AnalysisRun).where(AnalysisRun.status == "queued")
        if interactive is True:
            query = query.where(AnalysisRun.kind.in_(INTERACTIVE_KINDS))
        elif interactive is False:
            query = query.where(AnalysisRun.kind.not_in(INTERACTIVE_KINDS))
        run = s.scalar(query.order_by(priority, AnalysisRun.id).limit(1))
        if run is None:
            return None
        run.status = "running"
        run.started_at = utcnow()
        run.attempts += 1
        run.error = None
        return run.id


def execute(run_id: int, registry: ProviderRegistry) -> None:
    from app.guidance.scan import run_note_scan
    from app.jobs.pipeline import run_pipeline

    with session_scope() as s:
        run = s.get(AnalysisRun, run_id)
        kind, mode = run.kind, (run.params or {}).get("provider_mode")
        run_registry = registry.for_mode(mode)
        # Interactive checks never need model discovery/network access.
        run.provider_snapshot = ({"model_calls": False, "provider_mode": mode or "configured"}
                                 if kind in INTERACTIVE_KINDS else
                                 {**run_registry.snapshot(), "provider_mode": mode or "configured"})
    try:
        if kind == "pipeline":
            run_pipeline(run_id, run_registry)
        elif kind == "note_scan":
            run_note_scan(run_id, run_registry)
        elif kind == "draft_check":
            from app.grading.feedback import run_draft_check
            run_draft_check(run_id, run_registry)
        elif kind == "draft_model":
            from app.grading.feedback import run_draft_model
            run_draft_model(run_id, run_registry)
        elif kind == "feedback_refresh":
            from app.grading.feedback import run_feedback_refresh
            run_feedback_refresh(run_id, run_registry)
        else:
            raise ValueError(f"unknown run kind {kind!r}")
        final_error = None
    except Cancelled:
        with session_scope() as s:
            s.get(AnalysisRun, run_id).status = "cancelled"
        final_error = "cancelled"
    except Exception as exc:  # recorded visibly; never swallowed
        log.error("run %s failed: %s", run_id, exc)
        with session_scope() as s:
            run = s.get(AnalysisRun, run_id)
            run.status = "failed"
            run.error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=5)}"
        final_error = type(exc).__name__
    with session_scope() as s:
        run = s.get(AnalysisRun, run_id)
        run.finished_at = utcnow()
        audit.record(s, run.actor, "run_finished", "analysis_run", run.id, status=run.status, error=final_error)


def process_queued(registry: ProviderRegistry, limit: int = 100) -> int:
    """Synchronously drain the queue (tests and CLI)."""
    processed = 0
    while processed < limit:
        run_id = claim_next()
        if run_id is None:
            break
        execute(run_id, registry)
        processed += 1
    return processed


class Worker(threading.Thread):
    def __init__(self, registry: ProviderRegistry, poll_s: float = 1.0):
        super().__init__(name="gradelens-worker", daemon=True)
        self.registry = registry
        self.poll_s = poll_s
        self._stop_event = threading.Event()
        self.interactive_thread = threading.Thread(target=self._loop, args=(True,),
                                                   name="gradelens-feedback-worker", daemon=True)

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        # One model lane retains serialized GPU use; a separate CPU lane can
        # serve drafts arriving AFTER a long model call has already started.
        self.interactive_thread.start()
        try:
            self._loop(False)
        finally:
            self._stop_event.set()
            self.interactive_thread.join(timeout=5)

    def _loop(self, interactive: bool) -> None:
        while not self._stop_event.is_set():
            try:
                run_id = claim_next(interactive)
            except Exception as exc:  # database briefly unavailable
                log.error("claim failed: %s", exc)
                run_id = None
            if run_id is None:
                self._stop_event.wait(self.poll_s)
                continue
            execute(run_id, self.registry)
