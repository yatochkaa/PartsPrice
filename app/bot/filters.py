"""Пользовательские фильтры aiogram.

AdminFilter пропускает только пользователей с ролью admin.
Объект user попадает в фильтр из data (его кладёт UserRoleMiddleware) —
айограм автоматически подставляет ключи data в аргументы фильтра по имени.
"""
from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import TelegramObject

from app.db.models import User, UserRole


class AdminFilter(BaseFilter):
    """Истина только если текущий пользователь — админ."""

    async def __call__(
        self,
        event: TelegramObject,
        user: User | None = None,
    ) -> bool:
        # user может быть None (незнакомый) или с другой ролью — тогда фильтр не пропускает.
        return user is not None and user.role == UserRole.admin
