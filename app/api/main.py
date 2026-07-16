"""Точка входа REST API PartsPrice Hub.

Создаёт приложение FastAPI, настраивает логирование через lifespan
и подключает роутеры: health, suppliers, uploads, search.
Авторизация — на уровне роутеров через зависимости из app/api/deps.py.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routers import health, search, suppliers, uploads
from app.core.config import settings
from app.core.logging import setup_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Жизненный цикл: настраиваем логирование один раз при старте."""
    setup_logging(settings.LOG_LEVEL)
    logger.info("PartsPrice Hub API запущен")
    yield
    logger.info("PartsPrice Hub API остановлен")


app = FastAPI(
    title="PartsPrice Hub API",
    description="Агрегатор прайс-листов поставщиков автозапчастей",
    version="0.2.0",
    lifespan=lifespan,
)

# health — без авторизации; остальные роутеры защищены внутри себя.
app.include_router(health.router)
app.include_router(suppliers.router)
app.include_router(uploads.router)
app.include_router(search.router)
