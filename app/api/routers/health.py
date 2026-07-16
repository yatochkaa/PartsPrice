"""Health-check endpoint для мониторинга и Docker healthcheck."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Проверка живости сервиса: всегда отвечает {"status": "ok"}."""
    return {"status": "ok"}
