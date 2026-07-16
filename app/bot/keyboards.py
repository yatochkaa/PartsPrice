"""Inline-клавиатуры бота.

Вынесены в отдельный модуль, чтобы хендлеры не собирали разметку вручную
и код callback_data был в одном месте.

Формат callback_data:
- "request_access"      — нажата кнопка «Запросить доступ»;
- "approve:"  — админ одобрил заявку пользователя с данным telegram_id;
- "reject:"   — админ отклонил заявку.
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# Префиксы callback_data — единый источник правды для клавиатур и фильтров в хендлерах.
CB_REQUEST_ACCESS = "request_access"
CB_APPROVE_PREFIX = "approve:"
CB_REJECT_PREFIX = "reject:"


def request_access_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура с одной кнопкой «Запросить доступ» (для незнакомых)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔑 Запросить доступ",
                    callback_data=CB_REQUEST_ACCESS,
                )
            ]
        ]
    )


def approve_reject_keyboard(telegram_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для админа: одобрить/отклонить заявку конкретного пользователя.

    telegram_id зашиваем в callback_data, чтобы обработчик знал, кого одобрять.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Одобрить (manager)",
                    callback_data=f"{CB_APPROVE_PREFIX}{telegram_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"{CB_REJECT_PREFIX}{telegram_id}",
                ),
            ]
        ]
    )
