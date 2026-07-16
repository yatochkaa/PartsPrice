"""Inline-клавиатуры бота.

Вынесены в отдельный модуль, чтобы хендлеры не собирали разметку вручную
и код callback_data был в одном месте.

Формат callback_data:
- "request_access" — нажата кнопка «Запросить доступ»;
- "approve:<telegram_id>" — админ одобрил заявку пользователя;
- "reject:<telegram_id>" — админ отклонил заявку;
- "upload_supplier:<supplier_id>" — выбран поставщик для загружаемого прайса;
- "upload_cancel" — отмена диалога загрузки прайса;
- "sup_open:<supplier_id>" — открыть карточку поставщика;
- "sup_add" — начать диалог добавления поставщика;
- "sup_back" — вернуться к списку поставщиков;
- "sup_rename:<supplier_id>" — переименовать поставщика;
- "sup_currency:<supplier_id>" — показать выбор валюты;
- "sup_setcur:<supplier_id>:<CUR>" — установить валюту;
- "sup_addcur:<CUR>" — валюта нового поставщика (в диалоге добавления);
- "sup_cols:<supplier_id>" — настроить колонки файла;
- "sup_toggle:<supplier_id>" — включить/отключить поставщика;
- "sup_cancel" — отмена диалога управления поставщиками.
"""
from __future__ import annotations

from collections.abc import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# Префиксы callback_data — единый источник правды для клавиатур и фильтров в хендлерах.
CB_REQUEST_ACCESS = "request_access"
CB_APPROVE_PREFIX = "approve:"
CB_REJECT_PREFIX = "reject:"
CB_UPLOAD_SUPPLIER_PREFIX = "upload_supplier:"
CB_UPLOAD_CANCEL = "upload_cancel"
# Управление поставщиками (/suppliers).
CB_SUP_ADD = "sup_add"
CB_SUP_OPEN_PREFIX = "sup_open:"
CB_SUP_BACK = "sup_back"
CB_SUP_RENAME_PREFIX = "sup_rename:"
CB_SUP_CURRENCY_PREFIX = "sup_currency:"
CB_SUP_SETCUR_PREFIX = "sup_setcur:"
CB_SUP_ADDCUR_PREFIX = "sup_addcur:"
CB_SUP_COLS_PREFIX = "sup_cols:"
CB_SUP_TOGGLE_PREFIX = "sup_toggle:"
CB_SUP_CANCEL = "sup_cancel"


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


def _cancel_button() -> InlineKeyboardButton:
    """Кнопка отмены диалога загрузки (используется в клавиатурах загрузки)."""
    return InlineKeyboardButton(text="✖️ Отмена", callback_data=CB_UPLOAD_CANCEL)


def cancel_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура с одной кнопкой «Отмена» (шаг ожидания файла в /upload)."""
    return InlineKeyboardMarkup(inline_keyboard=[[_cancel_button()]])


def suppliers_keyboard(suppliers: Sequence[tuple[int, str]]) -> InlineKeyboardMarkup:
    """Клавиатура выбора поставщика для загружаемого прайса.

    suppliers — пары (id, name); по одной кнопке в строке + «Отмена» внизу.
    Принимаем простые пары, а не ORM-объекты, чтобы модуль клавиатур не зависел от моделей БД.
    """
    rows = [
        [
            InlineKeyboardButton(
                text=name,
                callback_data=f"{CB_UPLOAD_SUPPLIER_PREFIX}{supplier_id}",
            )
        ]
        for supplier_id, name in suppliers
    ]
    rows.append([_cancel_button()])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------------------
# Клавиатуры управления поставщиками (/suppliers)
# ---------------------------------------------------------------------------


def _sup_cancel_button() -> InlineKeyboardButton:
    """Кнопка отмены диалогов управления поставщиками."""
    return InlineKeyboardButton(text="✖️ Отмена", callback_data=CB_SUP_CANCEL)


def sup_cancel_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура с одной кнопкой «Отмена» для текстовых шагов диалогов."""
    return InlineKeyboardMarkup(inline_keyboard=[[_sup_cancel_button()]])


def suppliers_manage_keyboard(
    suppliers: Sequence[tuple[int, str, bool]],
) -> InlineKeyboardMarkup:
    """Клавиатура списка поставщиков: кнопка на каждого + «➕ Добавить».

    suppliers — тройки (id, name, is_active); отключённые помечаются ⛔.
    """
    rows = [
        [
            InlineKeyboardButton(
                text=name if is_active else f"⛔ {name}",
                callback_data=f"{CB_SUP_OPEN_PREFIX}{supplier_id}",
            )
        ]
        for supplier_id, name, is_active in suppliers
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="➕ Добавить поставщика",
                callback_data=CB_SUP_ADD,
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def supplier_card_keyboard(supplier_id: int, is_active: bool) -> InlineKeyboardMarkup:
    """Клавиатура карточки поставщика с действиями редактирования."""
    toggle_text = "⏸ Отключить" if is_active else "▶️ Включить"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏️ Переименовать",
                    callback_data=f"{CB_SUP_RENAME_PREFIX}{supplier_id}",
                ),
                InlineKeyboardButton(
                    text="💱 Валюта",
                    callback_data=f"{CB_SUP_CURRENCY_PREFIX}{supplier_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🗂 Колонки файла",
                    callback_data=f"{CB_SUP_COLS_PREFIX}{supplier_id}",
                ),
                InlineKeyboardButton(
                    text=toggle_text,
                    callback_data=f"{CB_SUP_TOGGLE_PREFIX}{supplier_id}",
                ),
            ],
            [
                InlineKeyboardButton(text="⬅️ К списку", callback_data=CB_SUP_BACK),
            ],
        ]
    )


def currency_keyboard(
    callback_prefix: str, codes: Sequence[str]
) -> InlineKeyboardMarkup:
    """Клавиатура выбора валюты: кнопки кодов в одну строку + «Отмена».

    callback_prefix подставляется перед кодом валюты: так одна клавиатура
    обслуживает и добавление ("sup_addcur:"), и смену валюты ("sup_setcur:<id>:").
    Коды передаются снаружи, чтобы модуль не зависел от моделей БД.
    """
    rows = [
        [
            InlineKeyboardButton(text=code, callback_data=f"{callback_prefix}{code}")
            for code in codes
        ]
    ]
    rows.append([_sup_cancel_button()])
    return InlineKeyboardMarkup(inline_keyboard=rows)
