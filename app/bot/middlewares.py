"""Middleware бота.

DbSessionMiddleware — открывает сессию БД на каждый апдейт и кладёт её в data["session"].
UserRoleMiddleware — по telegram_id находит пользователя и кладёт в data["user"]
(None, если незнакомый).

Оба регистрируются как outer-middleware на dp.update: сначала сессия (нужна второму),
потом пользователь. Сессия живёт всю обработку апдейта и закрывается по выходе из with.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update, User as TgUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import User

logger = logging.getLogger(__name__)

# Тип обработчика, который middleware вызывает дальше по цепочке.
Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


def _extract_tg_user(event: TelegramObject) -> TgUser | None:
    """Достаёт автора из апдейта (сообщение или callback). None — если автора нет."""
    if isinstance(event, Update):
        if event.message is not None:
            return event.message.from_user
        if event.callback_query is not None:
            return event.callback_query.from_user
    return None


class DbSessionMiddleware(BaseMiddleware):
    """Открывает сессию БД на апдейт и прокидывает её в data["session"]."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        # Фабрика сессий — AsyncSessionLocal из app.db.session.
        self.session_factory = session_factory

    async def __call__(
        self,
        handler: Handler,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with self.session_factory() as session:
            data["session"] = session
            return await handler(event, data)


class UserRoleMiddleware(BaseMiddleware):
    """По telegram_id находит пользователя и кладёт в data["user"] (или None)."""

    async def __call__(
        self,
        handler: Handler,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        session: AsyncSession = data["session"]
        tg_user = _extract_tg_user(event)

        user: User | None = None
        if tg_user is not None:
            user = await session.scalar(
                select(User).where(User.telegram_id == tg_user.id)
            )
        # Незнакомый пользователь -> None. Это ожидаемое значение для хендлеров/фильтров.
        data["user"] = user
        return await handler(event, data)
