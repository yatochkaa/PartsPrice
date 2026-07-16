"""Управление поставщиками в боте: /suppliers (только для админа).

Что умеет:
- список поставщиков с датами последних загрузок и кнопками;
- карточка поставщика: «✏️ Переименовать», «💱 Валюта», «🗂 Колонки файла»,
  «⏸ Отключить / ▶️ Включить»;
- «➕ Добавить поставщика» — пошаговый диалог (FSM):
  название -> валюта (кнопками) -> названия 5 колонок его файла.

Зачем колонки: каждый поставщик называет колонки в прайсе по-своему
(«Артикул» против «OEM»), и без этого соответствия импортёр не поймёт файл.

Удаления поставщика нет намеренно: вместе с ним каскадом удалились бы все его
загрузки и предложения. Вместо удаления используйте «⏸ Отключить».
"""
from __future__ import annotations

import html
import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import AdminFilter
from app.bot.keyboards import (
    CB_SUP_ADD,
    CB_SUP_ADDCUR_PREFIX,
    CB_SUP_BACK,
    CB_SUP_CANCEL,
    CB_SUP_COLS_PREFIX,
    CB_SUP_CURRENCY_PREFIX,
    CB_SUP_OPEN_PREFIX,
    CB_SUP_RENAME_PREFIX,
    CB_SUP_SETCUR_PREFIX,
    CB_SUP_TOGGLE_PREFIX,
    currency_keyboard,
    sup_cancel_keyboard,
    supplier_card_keyboard,
    suppliers_manage_keyboard,
)
from app.db.models import ColumnMapping, Currency, PriceUpload, Supplier

logger = logging.getLogger(__name__)

router = Router(name="suppliers")

# Максимальная длина названий — как у соответствующих колонок в БД (String(255)).
MAX_NAME_LEN = 255

# Коды валют для клавиатур; единственный источник правды — enum Currency.
_CURRENCY_CODES = tuple(c.value for c in Currency)


class SupplierStates(StatesGroup):
    """Состояния диалогов управления поставщиками."""

    add_name = State()      # ждём название нового поставщика
    add_currency = State()  # ждём выбор валюты кнопкой
    rename = State()        # ждём новое название существующего поставщика
    col_oem = State()       # дальше — 5 шагов названий колонок файла
    col_brand = State()
    col_name = State()
    col_price = State()
    col_qty = State()


# Шаги диалога настройки колонок: (поле маппинга, состояние, описание, пример).
_COLUMN_STEPS: tuple[tuple[str, State, str, str], ...] = (
    ("oem_col", SupplierStates.col_oem, "с артикулом (OEM)", "Артикул"),
    ("brand_col", SupplierStates.col_brand, "с брендом", "Бренд"),
    ("name_col", SupplierStates.col_name, "с наименованием", "Наименование"),
    ("price_col", SupplierStates.col_price, "с ценой", "Цена"),
    ("qty_col", SupplierStates.col_qty, "с остатком (количеством)", "Остаток"),
)
_COLUMN_STATES = tuple(step[1] for step in _COLUMN_STEPS)
# Соответствие «строка состояния -> номер шага» для общего хендлера.
_COLUMN_STATE_INDEX = {step[1].state: i for i, step in enumerate(_COLUMN_STEPS)}


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def _fmt_dt(value: datetime | str | None) -> str:
    """Форматирует дату/время: в БД хранится UTC, показываем локальное время сервера."""
    if value is None:
        return "—"
    if isinstance(value, str):
        # SQLite может вернуть строку — пробуем разобрать, иначе показываем как есть.
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value[:16].replace("T", " ")
    if value.tzinfo is None:
        # Наивные даты из БД считаем UTC.
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone().strftime("%d.%m.%Y %H:%M")


def _parse_id(data: str | None, prefix: str) -> int | None:
    """Достаёт числовой id из callback_data вида «prefix<id>»; None при мусоре."""
    if not data:
        return None
    try:
        return int(data.removeprefix(prefix))
    except ValueError:
        return None


def _valid_name(raw: str | None) -> str | None:
    """Обрезает пробелы и проверяет длину; None, если название не годится."""
    name = (raw or "").strip()
    if not name or len(name) > MAX_NAME_LEN:
        return None
    return name


async def _name_taken(
    session: AsyncSession, name: str, exclude_id: int | None = None
) -> bool:
    """Проверяет, занято ли название (без учёта регистра).

    Сравниваем на стороне Python: lower() в SQLite не работает с кириллицей.
    Поставщиков немного, поэтому полная выборка — не проблема.
    """
    rows = (await session.execute(select(Supplier.id, Supplier.name))).all()
    target = name.lower()
    return any(
        row.name.strip().lower() == target and row.id != exclude_id for row in rows
    )


async def _render_list(
    session: AsyncSession,
) -> tuple[str, InlineKeyboardMarkup]:
    """Собирает текст списка поставщиков и клавиатуру управления."""
    # Последняя загрузка каждого поставщика — одним запросом (подзапрос с max).
    last_upload = (
        select(
            PriceUpload.supplier_id.label("supplier_id"),
            func.max(PriceUpload.uploaded_at).label("last_at"),
        )
        .group_by(PriceUpload.supplier_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(Supplier, last_upload.c.last_at)
            .outerjoin(last_upload, Supplier.id == last_upload.c.supplier_id)
            .order_by(Supplier.id)
        )
    ).all()

    if not rows:
        return (
            "Поставщиков пока нет. Добавьте первого кнопкой ниже.",
            suppliers_manage_keyboard([]),
        )

    lines = ["🏭 <b>Поставщики</b>"]
    buttons: list[tuple[int, str, bool]] = []
    for supplier, last_at in rows:
        status = (
            f"последняя загрузка {_fmt_dt(last_at)}" if last_at else "загрузок не было"
        )
        inactive = "" if supplier.is_active else " — ⛔ отключён"
        lines.append(
            f"{supplier.id}. <b>{html.escape(supplier.name)}</b> "
            f"({supplier.currency.value}) — {status}{inactive}"
        )
        buttons.append((supplier.id, supplier.name, supplier.is_active))
    lines.append("")
    lines.append("Нажмите на поставщика, чтобы изменить его настройки.")
    return "\n".join(lines), suppliers_manage_keyboard(buttons)


async def _render_card(
    session: AsyncSession, supplier_id: int
) -> tuple[str, InlineKeyboardMarkup] | None:
    """Собирает карточку поставщика с настройками; None, если поставщик удалён."""
    supplier = await session.get(Supplier, supplier_id)
    if supplier is None:
        return None
    mapping = await session.scalar(
        select(ColumnMapping).where(ColumnMapping.supplier_id == supplier_id)
    )
    last_at = await session.scalar(
        select(func.max(PriceUpload.uploaded_at)).where(
            PriceUpload.supplier_id == supplier_id
        )
    )

    status = "активен ✅" if supplier.is_active else "отключён ⛔ (скрыт при /upload)"
    lines = [
        f"🏭 <b>{html.escape(supplier.name)}</b>",
        f"Валюта прайса: {supplier.currency.value}",
        f"Статус: {status}",
        f"Последняя загрузка: {_fmt_dt(last_at)}",
        "",
    ]
    if mapping is not None:
        lines.append("Колонки файла:")
        lines.append(f"• артикул (OEM): {html.escape(mapping.oem_col)}")
        lines.append(f"• бренд: {html.escape(mapping.brand_col)}")
        lines.append(f"• наименование: {html.escape(mapping.name_col)}")
        lines.append(f"• цена: {html.escape(mapping.price_col)}")
        lines.append(f"• остаток: {html.escape(mapping.qty_col)}")
    else:
        lines.append(
            "⚠️ Колонки файла не настроены — импорт прайса не сработает. "
            "Нажмите «🗂 Колонки файла»."
        )
    return "\n".join(lines), supplier_card_keyboard(supplier.id, supplier.is_active)


async def _ask_column(message: Message, index: int) -> None:
    """Задаёт вопрос про очередную колонку файла."""
    _, _, description, example = _COLUMN_STEPS[index]
    await message.answer(
        f"Шаг {index + 1} из {len(_COLUMN_STEPS)}. "
        f"Как в файле называется колонка {description}?\n"
        f"Например: <code>{example}</code>",
        reply_markup=sup_cancel_keyboard(),
    )


# ---------------------------------------------------------------------------
# /suppliers — список с кнопками
# ---------------------------------------------------------------------------


@router.message(Command("suppliers"), AdminFilter())
async def cmd_suppliers(message: Message, session: AsyncSession) -> None:
    """Показывает список поставщиков с кнопками управления."""
    text, keyboard = await _render_list(session)
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith(CB_SUP_OPEN_PREFIX), AdminFilter())
async def supplier_open(callback: CallbackQuery, session: AsyncSession) -> None:
    """Открывает карточку поставщика (по кнопке из списка)."""
    supplier_id = _parse_id(callback.data, CB_SUP_OPEN_PREFIX)
    card = None if supplier_id is None else await _render_card(session, supplier_id)
    if card is None:
        await callback.answer("Поставщик не найден.", show_alert=True)
        return
    await callback.answer()
    text, keyboard = card
    await callback.message.edit_text(text, reply_markup=keyboard)


@router.callback_query(F.data == CB_SUP_BACK, AdminFilter())
async def supplier_back(callback: CallbackQuery, session: AsyncSession) -> None:
    """Возвращает из карточки к списку поставщиков."""
    await callback.answer()
    text, keyboard = await _render_list(session)
    await callback.message.edit_text(text, reply_markup=keyboard)


# ---------------------------------------------------------------------------
# Добавление поставщика (FSM: название -> валюта -> 5 колонок)
# ---------------------------------------------------------------------------


@router.callback_query(F.data == CB_SUP_ADD, AdminFilter())
async def supplier_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Начинает диалог добавления поставщика."""
    await state.set_data({"mode": "add"})
    await state.set_state(SupplierStates.add_name)
    await callback.answer()
    await callback.message.answer(
        "Как называется новый поставщик?", reply_markup=sup_cancel_keyboard()
    )


@router.message(StateFilter(SupplierStates.add_name), F.text)
async def supplier_add_name(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    """Принимает название нового поставщика и спрашивает валюту."""
    name = _valid_name(message.text)
    if name is None:
        await message.answer(
            "⚠️ Название должно быть непустым и не длиннее 255 символов. "
            "Попробуйте ещё раз или /cancel."
        )
        return
    if await _name_taken(session, name):
        await message.answer(
            f"⚠️ Поставщик «{html.escape(name)}» уже есть. "
            "Пришлите другое название или /cancel."
        )
        return
    await state.update_data(name=name)
    await state.set_state(SupplierStates.add_currency)
    await message.answer(
        f"Название: <b>{html.escape(name)}</b>.\n"
        "В какой валюте цены в его прайсе?",
        reply_markup=currency_keyboard(CB_SUP_ADDCUR_PREFIX, _CURRENCY_CODES),
    )


@router.callback_query(
    StateFilter(SupplierStates.add_currency),
    F.data.startswith(CB_SUP_ADDCUR_PREFIX),
    AdminFilter(),
)
async def supplier_add_currency(callback: CallbackQuery, state: FSMContext) -> None:
    """Принимает валюту нового поставщика и запускает шаги про колонки."""
    code = (callback.data or "").removeprefix(CB_SUP_ADDCUR_PREFIX)
    if code not in _CURRENCY_CODES:
        await callback.answer("Некорректная валюта.", show_alert=True)
        return
    await state.update_data(currency=code)
    await state.set_state(_COLUMN_STEPS[0][1])
    await callback.answer()
    await callback.message.edit_text(f"Валюта: {code}.")
    await _ask_column(callback.message, 0)


@router.message(StateFilter(SupplierStates.add_currency))
async def supplier_add_currency_hint(message: Message) -> None:
    """Подсказка, если на шаге валюты прислали сообщение вместо нажатия кнопки."""
    await message.answer("Выберите валюту кнопкой под сообщением выше или /cancel.")


# ---------------------------------------------------------------------------
# Общие шаги «названия колонок» (для добавления и для редактирования)
# ---------------------------------------------------------------------------


@router.message(StateFilter(*_COLUMN_STATES), F.text)
async def supplier_column_value(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    """Принимает название очередной колонки; после последней — сохраняет всё в БД."""
    value = _valid_name(message.text)
    if value is None:
        await message.answer(
            "⚠️ Название колонки должно быть непустым и не длиннее 255 символов. "
            "Попробуйте ещё раз или /cancel."
        )
        return

    current = await state.get_state()
    index = _COLUMN_STATE_INDEX.get(current or "")
    if index is None:  # защита от рассинхрона состояний
        await state.clear()
        await message.answer("⚠️ Диалог сбилс��. Начните заново: /suppliers.")
        return

    field = _COLUMN_STEPS[index][0]
    await state.update_data(**{field: value})

    # Есть следующий шаг — спрашиваем дальше.
    if index + 1 < len(_COLUMN_STEPS):
        await state.set_state(_COLUMN_STEPS[index + 1][1])
        await _ask_column(message, index + 1)
        return

    # Все 5 колонок собраны — сохраняем.
    await _finish_columns(message, state, session)


async def _finish_columns(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    """Сохраняет результат диалога: нового поставщика или новые колонки."""
    data = await state.get_data()
    cols = {step[0]: data.get(step[0]) for step in _COLUMN_STEPS}
    mode = data.get("mode")

    # Данные могли потеряться (бот перезапускался — FSM хранится в памяти).
    if any(v is None for v in cols.values()) or mode not in ("add", "edit"):
        await state.clear()
        await message.answer("⚠️ Диалог сбился. Начните заново: /suppliers.")
        return

    if mode == "add":
        name = data.get("name")
        currency = data.get("currency")
        if not name or currency not in _CURRENCY_CODES:
            await state.clear()
            await message.answer("⚠️ Диалог сбился. Начните заново: /suppliers.")
            return
        supplier = Supplier(name=name, currency=Currency(currency), is_active=True)
        session.add(supplier)
        await session.flush()  # получаем supplier.id до создания маппинга
        session.add(ColumnMapping(supplier_id=supplier.id, **cols))
        await session.commit()
        await state.clear()
        logger.info("Добавлен поставщик id=%s name=%r", supplier.id, supplier.name)
        card = await _render_card(session, supplier.id)
        prefix = "✅ Поставщик добавлен — можно загружать его прайсы через /upload.\n\n"
    else:
        supplier_id = data.get("supplier_id")
        supplier = None if supplier_id is None else await session.get(Supplier, supplier_id)
        if supplier is None:
            await state.clear()
            await message.answer("⚠️ Поставщик не найден. Начните заново: /suppliers.")
            return
        mapping = await session.scalar(
            select(ColumnMapping).where(ColumnMapping.supplier_id == supplier.id)
        )
        if mapping is None:
            session.add(ColumnMapping(supplier_id=supplier.id, **cols))
        else:
            for key, value in cols.items():
                setattr(mapping, key, value)
        await session.commit()
        await state.clear()
        logger.info("Обновлены колонки поставщика id=%s", supplier.id)
        card = await _render_card(session, supplier.id)
        prefix = "✅ Колонки файла сохранены.\n\n"

    if card is not None:
        text, keyboard = card
        await message.answer(prefix + text, reply_markup=keyboard)


@router.message(StateFilter(SupplierStates.add_name, SupplierStates.rename, *_COLUMN_STATES))
async def supplier_dialog_hint(message: Message) -> None:
    """Подсказка, если в текстовом шаге прислали не текст (фото, файл и т.п.)."""
    await message.answer("Жду текстовый ответ. Отмена — /cancel.")


# ---------------------------------------------------------------------------
# Переименование
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith(CB_SUP_RENAME_PREFIX), AdminFilter())
async def supplier_rename_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Начинает диалог переименования поставщика."""
    supplier_id = _parse_id(callback.data, CB_SUP_RENAME_PREFIX)
    if supplier_id is None:
        await callback.answer("Некорректные данные кнопки.", show_alert=True)
        return
    await state.set_data({"supplier_id": supplier_id})
    await state.set_state(SupplierStates.rename)
    await callback.answer()
    await callback.message.answer(
        "Пришлите новое название поставщика:", reply_markup=sup_cancel_keyboard()
    )


@router.message(StateFilter(SupplierStates.rename), F.text)
async def supplier_rename_value(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    """Принимает новое название и сохраняет его."""
    name = _valid_name(message.text)
    if name is None:
        await message.answer(
            "⚠️ Название должно быть непустым и не длиннее 255 символов. "
            "Попробуйте ещё раз или /cancel."
        )
        return

    data = await state.get_data()
    supplier_id = data.get("supplier_id")
    supplier = None if supplier_id is None else await session.get(Supplier, supplier_id)
    if supplier is None:
        await state.clear()
        await message.answer("⚠️ Поставщик не найден. Начните заново: /suppliers.")
        return
    if await _name_taken(session, name, exclude_id=supplier.id):
        await message.answer(
            f"⚠️ Название «{html.escape(name)}» уже занято. "
            "Пришлите другое или /cancel."
        )
        return

    supplier.name = name
    await session.commit()
    await state.clear()
    card = await _render_card(session, supplier.id)
    if card is not None:
        text, keyboard = card
        await message.answer("✅ Название обновлено.\n\n" + text, reply_markup=keyboard)


# ---------------------------------------------------------------------------
# Смена валюты
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith(CB_SUP_CURRENCY_PREFIX), AdminFilter())
async def supplier_currency_start(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    """Показывает выбор новой валюты для поставщика."""
    supplier_id = _parse_id(callback.data, CB_SUP_CURRENCY_PREFIX)
    supplier = (
        None if supplier_id is None else await session.get(Supplier, supplier_id)
    )
    if supplier is None:
        await callback.answer("Поставщик не найден.", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text(
        f"Текущая валюта «{html.escape(supplier.name)}»: {supplier.currency.value}.\n"
        "Выберите новую:",
        reply_markup=currency_keyboard(
            f"{CB_SUP_SETCUR_PREFIX}{supplier.id}:", _CURRENCY_CODES
        ),
    )


@router.callback_query(F.data.startswith(CB_SUP_SETCUR_PREFIX), AdminFilter())
async def supplier_currency_set(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    """Сохраняет новую валюту поставщика."""
    payload = (callback.data or "").removeprefix(CB_SUP_SETCUR_PREFIX)
    supplier_id_str, _, code = payload.partition(":")
    if not supplier_id_str.isdigit() or code not in _CURRENCY_CODES:
        await callback.answer("Некорректные данные кнопки.", show_alert=True)
        return
    supplier = await session.get(Supplier, int(supplier_id_str))
    if supplier is None:
        await callback.answer("Поставщик не найден.", show_alert=True)
        return

    supplier.currency = Currency(code)
    await session.commit()
    await callback.answer("Валюта обновлена")
    card = await _render_card(session, supplier.id)
    if card is not None:
        text, keyboard = card
        await callback.message.edit_text(
            "💱 Валюта обновлена. Уже загруженные цены не пересчитываются — "
            "новый курс применится при следующей загрузке прайса.\n\n" + text,
            reply_markup=keyboard,
        )


# ---------------------------------------------------------------------------
# Настройка колонок файла (редактирование существующего поставщика)
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith(CB_SUP_COLS_PREFIX), AdminFilter())
async def supplier_cols_start(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    """Начинает диалог настройки колонок для существующего поставщика."""
    supplier_id = _parse_id(callback.data, CB_SUP_COLS_PREFIX)
    supplier = (
        None if supplier_id is None else await session.get(Supplier, supplier_id)
    )
    if supplier is None:
        await callback.answer("Поставщик не найден.", show_alert=True)
        return
    await state.set_data({"supplier_id": supplier.id, "mode": "edit"})
    await state.set_state(_COLUMN_STEPS[0][1])
    await callback.answer()
    await callback.message.answer(
        f"Настройка колонок файла для «{html.escape(supplier.name)}». "
        "Отвечайте одним названием колонки на каждый вопрос."
    )
    await _ask_column(callback.message, 0)


# ---------------------------------------------------------------------------
# Включение/отключение
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith(CB_SUP_TOGGLE_PREFIX), AdminFilter())
async def supplier_toggle(callback: CallbackQuery, session: AsyncSession) -> None:
    """Переключает активность поставщика (отключённый скрыт при /upload)."""
    supplier_id = _parse_id(callback.data, CB_SUP_TOGGLE_PREFIX)
    supplier = (
        None if supplier_id is None else await session.get(Supplier, supplier_id)
    )
    if supplier is None:
        await callback.answer("Поставщик не найден.", show_alert=True)
        return

    supplier.is_active = not supplier.is_active
    await session.commit()
    await callback.answer("Включён" if supplier.is_active else "Отключён")
    card = await _render_card(session, supplier.id)
    if card is not None:
        text, keyboard = card
        await callback.message.edit_text(text, reply_markup=keyboard)


# ---------------------------------------------------------------------------
# Отмена диалога кнопкой
# ---------------------------------------------------------------------------


@router.callback_query(F.data == CB_SUP_CANCEL)
async def supplier_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """Кнопка «Отмена» в диалогах управления поставщиками."""
    await state.clear()
    await callback.answer()
    await callback.message.edit_text("Действие отменено.")


# ---------------------------------------------------------------------------
# Вежливый отказ для не-админов (регистрируется последним)
# ---------------------------------------------------------------------------


@router.message(Command("suppliers"))
async def suppliers_denied(message: Message) -> None:
    """Отказ: админский хендлер выше не подошёл (AdminFilter не прошёл)."""
    await message.answer("⛔ Эта команда доступна только администратору.")
