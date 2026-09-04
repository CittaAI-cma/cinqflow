"""Control plane. Composition root only: wire settings, dependencies, and
routers into one FastAPI app. Handlers themselves live in `routers/`."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from cinqflow.api.deps import make_get_conn
from cinqflow.api.routers import (
    auth,
    batches,
    canonical_proposals,
    health,
    mapping_versions,
    queue,
    uploads,
    users,
    worklist,
)
from cinqflow.settings import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or get_settings()
    app = FastAPI(title="CINQFLOW", version="0.1.0", description="Stage 1 foundation")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    get_conn = make_get_conn(s)

    app.include_router(health.build_router(s))
    app.include_router(auth.build_router(s, get_conn))
    app.include_router(users.build_router(s, get_conn))
    app.include_router(uploads.build_router(s, get_conn))
    app.include_router(batches.build_router(s, get_conn))
    app.include_router(mapping_versions.build_router(s, get_conn))
    app.include_router(canonical_proposals.build_router(s, get_conn))
    app.include_router(queue.build_router(s, get_conn))
    app.include_router(worklist.build_router(s, get_conn))

    return app


app = create_app()
