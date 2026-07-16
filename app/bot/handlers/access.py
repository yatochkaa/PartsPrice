"""Хендлеры запроса и выдачи доступа.

Сценарий:
1. Незнакомый нажимает «Запросить доступ» -> создаётся User с role=pending,
   админу (ADMIN_TELEGRAM_ID) уходит сообщение с кнопками Одобрить/Отклонить.
2. Админ жмёт Одобрить -> role=manager, пользователь получает уведомление.
   Отклонить -> запись удаляется (можно запросить заново), пользователь уведомлён.

Повторная заявка не дублируется: если пользователь уже есть в БД — новая запись не создаётся.
Одобрять/отклонять может только админ (AdminFilter); чужим — вежливый отказ.
"""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import AdminFilter
from app.bot.keyboards import (
    CB_APPROVE_PREFIX,
    CB_REJECT_PREFIX,
    CB_REQUEST_ACCESS,
    approve_reject_keyboard,
)
from app.core.config import settings
from app.db.models import User, UserRole

logger = logging.getLogger(__name__)

router = Router(name="access")


def _parse_target_id(callback_data: str, prefix: str) -> int | None:
    """Достаёт telegram_id из callback_data вида «prefix:12345». None — если формат битый."""
    raw = callback_data[len(prefix):]
    return int(raw) if raw.isdigit() else None


async def _get_user(session: AsyncSession, telegram_id: int) -> User | None:
    """Вспомогательный поиск пользователя по telegram_id."""
    return await session.scalar(
        select(User).where(User.telegram_id == telegram_id)
    )


@router.callback_query(F.data == CB_REQUEST_ACCESS)
async def request_access(
    callback: CallbackQuery,
    user: User | None,
    session: AsyncSession,
    bot: Bot,
) -> None:
    """Создаёт заявку (role=pending) и уведомляет админа."""
    tg_user = callback.from_user

    # Повторная заявка не дублируется: пользователь уже есть в БД.
    if user is not None:
        if user.role == UserRole.pending:
            await callback.answer(
                "Заявка уже отправлена, ожидайте одобрения.", show_alert=True
            )
        else:
            await callback.answer("У вас уже есть доступ.", show_alert=True)
        return

    # Создаём нового пользователя со статусом pending.
    new_user = User(
        telegram_id=tg_user.id,
        role=UserRole.pending,
        name=tg_user.full_name,
    )
    session.add(new_user)
    await session.commit()

    await callback.message.edit_text(
        "✉️ Заявка отправлена администратору. Мы уведомим вас после рассмотрения."
    )

    # Уведомляем админа. Если ADMIN_TELEGRAM_ID не задан — только логируем.
    if settings.ADMIN_TELEGRAM_ID:
        await bot.send_message(
            settings.ADMIN_TELEGRAM_ID,
            "🔔 <b>Новая заявка на доступ</b>\n"
            f"Имя: {tg_user.full_name}\n"
            f"Telegram ID: <code>{tg_user.id}</code>",
            reply_markup=approve_reject_keyboard(tg_user.id),
        )
    else:
        logger.warning(
            "ADMIN_TELEGRAM_ID не задан — заявка пользователя %s не доставлена админу",
            tg_user.id,
        )

    await callback.answer()


@router.callback_query(F.data.startswith(CB_APPROVE_PREFIX), AdminFilter())
async def approve_request(
    callback: CallbackQuery,
    session: AsyncSession,
    bot: Bot,
) -> None:
    """Админ одобряет заявку: role -> manager, уведомляем пользователя."""
    target_id = _parse_target_id(callback.data, CB_APPROVE_PREFIX)
    if target_id is None:
        await callback.answer("Некорректная заявка.", show_alert=True)
        return

    target = await _get_user(session, target_id)
    if target is None:
        await callback.answer("Пользователь не найден.", show_alert=True)
        return

    target.role = UserRole.manager
    await session.commit()

    await callback.message.edit_text(
        f"✅ Заявка одобрена. Пользователь <code>{target_id}</code> теперь manager."
    )
    # Уведомляем самого пользователя (может не выйти, если он не начинал чат — ловим).
    try:
        await bot.send_message(
            target_id,
            "✅ Ваш доступ одобрен! Роль: <b>manager</b>. Отправьте /start.",
        )
    except Exception:
        logger.warning("Не удалось уведомить пользователя %s об одобрении", target_id)

    await callback.answer("Одобрено")


@router.callback_query(F.data.startswith(CB_REJECT_PREFIX), AdminFilter())
async def reject_request(
    callback: CallbackQuery,
    session: AsyncSession,
    bot: Bot,
) -> None:
    """Админ отклоняет заявку: удаляем запись (можно будет запросить заново)."""
    target_id = _parse_target_id(callback.data, CB_REJECT_PREFIX)
    if target_id is None:
        await callback.answer("Некорректная заявка.", show_alert=True)
        return

    target = await _get_user(session, target_id)
    if target is None:
        await callback.answer("Пользователь не найден.", show_alert=True)
        return

    await session.delete(target)
    await session.commit()

    await callback.message.edit_text(
        f"❌ Заявка пользователя <code>{target_id}</code> отклонена."
    )
    try:
        await bot.send_message(
            target_id,
            "❌ К сожалению, ваша заявка на доступ отклонена.",
        )
    except Exception:
        logger.warning("Не удалось уведомить пользователя %s об отклонении", target_id)

    await callback.answer("Отклонено")


@router.callback_query(F.data.startswith((CB_APPROVE_PREFIX, CB_REJECT_PREFIX)))
async def admin_action_denied(callback: CallbackQuery) -> None:
    """Срабатывает, если кнопки Одобрить/Отклонить нажал не админ (AdminFilter не прошёл)."""
    await callback.answer(
        "Только администратор может обрабатывать заявки.", show_alert=True
    )
