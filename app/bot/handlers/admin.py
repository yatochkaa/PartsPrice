"""Админ-команды бота.

Здесь демонстрируется AdminFilter: команда /pending доступна только админу,
чужим — вежливый отказ (второй хендлер без фильтра, срабатывает когда AdminFilter не прошёл).

Порядок важен: сначала регистрируется хендлер с AdminFilter, потом — «отказный».
aiogram проверяет хендлеры по порядку регистрации.
"""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import AdminFilter
from app.db.models import User, UserRole

router = Router(name="admin")


@router.message(Command("pending"), AdminFilter())
async def cmd_pending(message: Message, session: AsyncSession) -> None:
    """Показывает список заявок на доступ (role=pending). Только для админа."""
    users = (
        await session.scalars(
            select(User).where(User.role == UserRole.pending)
        )
    ).all()
    if not users:
        await message.answer("Заявок на доступ нет.")
        return

    lines = [
        f"• {u.name or 'без имени'} — ID <code>{u.telegram_id}</code>"
        for u in users
    ]
    await message.answer("⏳ Ожидают одобрения:\n" + "\n".join(lines))


@router.message(Command("pending"))
async def cmd_pending_denied(message: Message) -> None:
    """Вежливый отказ для не-админов (AdminFilter выше не прошёл)."""
    await message.answer("⛔ Эта команда доступна только администратору.")
