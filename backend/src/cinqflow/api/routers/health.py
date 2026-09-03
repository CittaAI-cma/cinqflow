"""Liveness."""

from __future__ import annotations

from fastapi import APIRouter

from cinqflow.settings import Settings


def build_router(settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "llm_provider": settings.llm_provider}

    return router
