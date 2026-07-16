"""Асинхронный движок и фабрика сессий SQLAlchemy 2.x.

Один engine на процесс. Сессии создаём через async_sessionmaker.
get_session() — зависимость для FastAPI Depends и для скриптов.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

logger = logging.getLogger(__name__)

# Асинхронный движок. echo=False, чтобы не засорять логи SQL-запросами в проде.
# pool_pre_ping=True — проверяем «живость» соединения перед выдачей (актуально для PostgreSQL).
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    future=True,
    pool_pre_ping=True,
)

# Фабрика сессий. expire_on_commit=False — объекты остаются доступны после commit
# (иначе обращение к атрибутам после коммита вызовет ленивую подгрузку и ошибку в async).
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Отдаёт сессию БД и гарантированно закрывает её.

    Используется как зависимость (Depends) в FastAPI и в скриптах.
    При исключении делаем rollback, чтобы не оставить «повисшую» транзакцию.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            # Логируем и откатываем — вызывающий код сам решит, что делать с ошибкой.
            logger.exception("Ошибка в сессии БД, выполняю rollback")
            await session.rollback()
            raise
