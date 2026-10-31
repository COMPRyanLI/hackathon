"""FastAPI entry point. Serves the API under /api and, when built, the React UI.

Bind to loopback (127.0.0.1). Mutation requests must carry the
``X-GradeLens-Client`` header and, if an Origin is present, come from an
allowed local origin; this blocks cross-site form posts.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import PROJECT_ROOT, Settings, get_settings
from app.db.session import init_engine
from app.jobs.worker import Worker, mark_interrupted
from app.providers.registry import ProviderRegistry

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
CLIENT_HEADER = "x-gradelens-client"
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def create_app(settings: Settings | None = None, registry: ProviderRegistry | None = None,
               start_worker: bool | None = None) -> FastAPI:
    settings = settings or get_settings()
    registry = registry or ProviderRegistry(settings)
    run_worker = settings.start_worker if start_worker is None else start_worker

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_engine(settings.effective_database_url)
        interrupted = mark_interrupted()
        if interrupted:
            logging.getLogger("gradelens").info("marked %s run(s) as interrupted", interrupted)
        worker = None
        if run_worker:
            worker = Worker(registry)
            worker.start()
        app.state.worker = worker
        yield
        if worker:
            worker.stop()
        registry.ollama.close()

    app = FastAPI(title="GradeLens", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.registry = registry

    app.add_middleware(CORSMiddleware, allow_origins=settings.effective_origins, allow_credentials=False,
                       allow_methods=["GET", "POST", "PATCH"], allow_headers=["Content-Type", "X-GradeLens-Client"])

    @app.middleware("http")
    async def csrf_guard(request: Request, call_next):
        if request.method in MUTATING and request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            if origin and origin not in settings.effective_origins:
                return JSONResponse({"detail": "Cross-origin mutation rejected"}, status_code=403)
            if request.headers.get(CLIENT_HEADER) != "1":
                return JSONResponse({"detail": "Missing X-GradeLens-Client header"}, status_code=403)
        return await call_next(request)

    app.include_router(router)
    from app.api.routes_p1 import router as p1_router

    app.include_router(p1_router)

    if FRONTEND_DIST.is_dir():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            candidate = (FRONTEND_DIST / path).resolve()
            if path and candidate.is_file() and FRONTEND_DIST.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(FRONTEND_DIST / "index.html")
    else:
        @app.get("/", include_in_schema=False)
        def no_ui():
            return JSONResponse({"detail": "Frontend not built. Run scripts\\setup.ps1, or use the API at /docs."})

    return app


app = create_app()
