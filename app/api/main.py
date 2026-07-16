"""Точка входа REST API PartsPrice Hub.

Создаёт приложение FastAPI, настраивает логирование через lifespan
и подключает роутеры: health, suppliers, search.
Авторизации на этом этапе нет — она будет добавлена отдельным шагом в deps.py.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routers import health, search, suppliers
from app.core.config import settings
from app.core.logging import setup_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Жизненный цикл приложения.

    Логирование настраиваем именно здесь, а не на импорте модуля:
    lifespan гарантированно выполняется один раз при старте сервера
    (и в тестах через TestClient), поэтому обработчики логов
    не дублируются при повторных импортах.
    """
    setup_logging(settings.LOG_LEVEL)
    logger.info("PartsPrice Hub API запущен")
    yield
    # Точка для будущего освобождения ресурсов (например, engine.dispose()).
    logger.info("PartsPrice Hub API остановлен")


app = FastAPI(
    title="PartsPrice Hub API",
    description="Агрегатор прайс-листов поставщиков автозапчастей",
    version="0.1.0",
    lifespan=lifespan,
)

# Подключение роутеров. Префиксы и теги заданы внутри самих роутеров.
app.include_router(health.router)
app.include_router(suppliers.router)
app.include_router(search.router)
