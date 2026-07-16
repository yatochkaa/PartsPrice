"""Хендлер команды /start.

Логика зависит от роли (её кладёт UserRoleMiddleware в data["user"]):
- незнакомый (user is None) -> кнопка «Запросить доступ»;
- pending -> сообщение, что заявка на рассмотрении;
- manager / admin -> приветствие со списком команд по роли.
"""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from app.bot.keyboards import request_access_keyboard
from app.db.models import User, UserRole

router = Router(name="start")

# Списки команд по ролям — показываем в приветствии.
_MANAGER_COMMANDS = (
    "🔎 Просто пришлите номер детали (OEM) или слово из названия — я найду цены.\n"
    "Примеры: <code>W712/75</code> или <code>фильтр</code>"
)
_ADMIN_COMMANDS = (
    "🔎 Просто пришлите номер детали (OEM) или слово из названия — поиск цен\n"
    "/upload — загрузить прайс-лист (.csv/.xlsx)\n"
    "/suppliers — поставщики: список, добавление и настройка\n"
    "/report — сводка по базе\n"
    "/pending — заявки на доступ\n"
    "/cancel — отменить текущее действие"
)


@router.message(CommandStart())
async def cmd_start(message: Message, user: User | None) -> None:
    """Отвечает на /start с учётом роли пользователя."""
    # Незнакомый — предлагаем запросить доступ.
    if user is None:
        await message.answer(
            "👋 Здравствуйте! Это бот PartsPrice Hub — поиск автозапчастей по прайсам поставщиков.\n\n"
            "У вас пока нет доступа. Нажмите кнопку ниже, чтобы отправить заявку администратору.",
            reply_markup=request_access_keyboard(),
        )
        return

    # Заявка уже подана, но ещё не одобрена.
    if user.role == UserRole.pending:
        await message.answer(
            "⏳ Ваша заявка на доступ отправлена и ожидает одобрения администратора."
        )
        return

    # Админ.
    if user.role == UserRole.admin:
        await message.answer(
            "🛠 Здравствуйте, администратор!\n\n"
            "Доступные команды:\n"
            f"{_ADMIN_COMMANDS}"
        )
        return

    # Менеджер (единственная оставшаяся роль).
    await message.answer(
        "✅ Здравствуйте! Доступ менеджера подтверждён.\n\n"
        "Как искать:\n"
        f"{_MANAGER_COMMANDS}"
    )
