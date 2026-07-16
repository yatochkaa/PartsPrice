"""Админ-команды бота: /upload, /report, /pending, /cancel.

(Управление поставщиками — /suppliers — вынесено в handlers/suppliers.py.)

Все команды защищены AdminFilter; для чужих — вежливый отказ (последний хендлер без
фильтра: aiogram проверяет хендлеры по порядку регистрации, отказ срабатывает,
только если админский хендлер не подошёл).

Загрузка прайса — конечный автомат (FSM) из двух состояний:
1. waiting_file — ждём документ .csv/.xlsx до 10 МБ;
2. waiting_supplier — файл скачан во временную папку, ждём inline-выбор поставщика;
затем вызывается штатный import_price_file и отправляется отчёт (с ошибками строк).
"""
from __future__ import annotations

import html
import logging
import os
import shutil
import tempfile
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import AdminFilter
from app.bot.keyboards import (
    CB_UPLOAD_CANCEL,
    CB_UPLOAD_SUPPLIER_PREFIX,
    cancel_keyboard,
    suppliers_keyboard,
)
from app.db.models import (
    ColumnMapping,
    Offer,
    Part,
    PriceUpload,
    Supplier,
    UploadStatus,
    User,
    UserRole,
)
from app.services.importer import ImporterError, import_price_file

logger = logging.getLogger(__name__)

router = Router(name="admin")

# Ограничения на загружаемый файл — те же, что в REST API.
MAX_FILE_SIZE = 10 * 1024 * 1024
ALLOWED_SUFFIXES = {".csv", ".xlsx"}
# Сколько ошибок строк показываем в отчёте в Telegram.
MAX_ERRORS_SHOWN = 10

_STATUS_MARKS = {
    UploadStatus.done: "✅",
    UploadStatus.failed: "❌",
    UploadStatus.pending: "⏳",
}


class UploadStates(StatesGroup):
    """Состояния диалога загрузки прайса."""

    waiting_file = State()
    waiting_supplier = State()


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


async def _cleanup_upload(state: FSMContext) -> None:
    """Удаляет временную папку с файлом (если была) и сбрасывает состояние FSM."""
    data = await state.get_data()
    tmp_dir = data.get("tmp_dir")
    if tmp_dir:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    await state.clear()


# ---------------------------------------------------------------------------
# /cancel — отмена текущего диалога (загрузка прайса или настройка поставщика)
# ---------------------------------------------------------------------------


@router.message(Command("cancel"), AdminFilter())
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    """Отменяет текущий диалог (или сообщает, что отменять нечего)."""
    if await state.get_state() is None:
        await message.answer("Нечего отменять.")
        return
    await _cleanup_upload(state)
    await message.answer("Действие отменено.")


# ---------------------------------------------------------------------------
# /upload — загрузка прайса (FSM)
# ---------------------------------------------------------------------------


@router.message(Command("upload"), AdminFilter())
async def cmd_upload(message: Message, state: FSMContext) -> None:
    """Начинает диалог загрузки: просим прислать файл прайса."""
    # Если предыдущий диалог не был завершён — прибираем за ним.
    await _cleanup_upload(state)
    await state.set_state(UploadStates.waiting_file)
    await message.answer(
        "📤 Пришлите файл прайс-листа документом: <b>.csv</b> или <b>.xlsx</b>, до 10 МБ.\n"
        "Отменить — кнопкой ниже или командой /cancel.",
        reply_markup=cancel_keyboard(),
    )


@router.message(UploadStates.waiting_file, F.document)
async def upload_receive_file(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """Принимает документ: проверяет расширение и размер, скачивает во временную папку."""
    document = message.document

    # 1. Имя и расширение.
    filename = os.path.basename(document.file_name or "").strip()
    _, ext = os.path.splitext(filename)
    if ext.lower() not in ALLOWED_SUFFIXES:
        await message.answer(
            f"⚠️ Недопустимый формат файла {html.escape(ext or '(без расширения)')}. "
            "Разрешены .csv и .xlsx. Пришлите другой файл или /cancel."
        )
        return

    # 2. Размер (file_size может отсутствовать — тогда доверяем Telegram-лимитам).
    if document.file_size and document.file_size > MAX_FILE_SIZE:
        await message.answer(
            "⚠️ Файл больше лимита 10 МБ. Пришлите файл поменьше или /cancel."
        )
        return

    # 3. Скачиваем во временную ПАПКУ под исходным именем: importer берёт имя файла
    #    из пути, поэтому в отчёте и в price_uploads будет реальное имя, а не tmpXXXX.
    tmp_dir = tempfile.mkdtemp(prefix="partsprice_bot_")
    tmp_path = os.path.join(tmp_dir, filename)
    try:
        await message.bot.download(document, destination=tmp_path)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        logger.exception("Не удалось скачать файл %s из Telegram", filename)
        await message.answer("⚠️ Не удалось скачать файл. Попробуйте ещё раз или /cancel.")
        return

    # 4. Список поставщиков для выбора: только активные И с настроенными колонками —
    #    без маппинга колонок импорт заведомо упадёт, такие в списке не показываем.
    suppliers = (
        await session.scalars(
            select(Supplier)
            .join(ColumnMapping)
            .where(Supplier.is_active.is_(True))
            .order_by(Supplier.id)
        )
    ).all()
    if not suppliers:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        await state.clear()
        await message.answer(
            "⚠️ Нет поставщиков, готовых к загрузке: нужен активный поставщик "
            "с настроенными колонками файла. Откройте /suppliers — "
            "«➕ Добавить поставщика» или «🗂 Колонки файла»."
        )
        return

    await state.update_data(tmp_dir=tmp_dir, file_path=tmp_path, filename=filename)
    await state.set_state(UploadStates.waiting_supplier)
    await message.answer(
        f"📄 Файл <b>{html.escape(filename)}</b> получен.\nВыберите поставщика:",
        reply_markup=suppliers_keyboard([(s.id, s.name) for s in suppliers]),
    )


@router.message(UploadStates.waiting_file)
async def upload_waiting_file_hint(message: Message) -> None:
    """Подсказка, если вместо документа прислали что-то другое."""
    await message.answer(
        "Жду файл прайса документом (.csv или .xlsx, до 10 МБ). Отмена — /cancel."
    )


@router.callback_query(UploadStates.waiting_supplier, F.data.startswith(CB_UPLOAD_SUPPLIER_PREFIX))
async def upload_choose_supplier(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """Поставщик выбран: запускаем импорт и отправляем отчёт."""
    try:
        supplier_id = int(callback.data.removeprefix(CB_UPLOAD_SUPPLIER_PREFIX))
    except ValueError:
        await callback.answer("Некорректные данные кнопки.", show_alert=True)
        return

    data = await state.get_data()
    file_path = data.get("file_path")
    filename = data.get("filename", "файл")

    # Файл мог потеряться (например, бот перезапускался — FSM хранится в памяти).
    if not file_path or not os.path.exists(file_path):
        await _cleanup_upload(state)
        await callback.answer()
        await callback.message.edit_text(
            "⚠️ Файл не найден (возможно, бот перезапускался). Начните заново: /upload."
        )
        return

    await callback.answer()
    await callback.message.edit_text(f"⏳ Импортирую <b>{html.escape(filename)}</b>…")

    try:
        report = await import_price_file(session, supplier_id, file_path)
    except ImporterError as exc:
        await _cleanup_upload(state)
        await callback.message.answer(f"❌ Импорт не удался: {html.escape(str(exc))}")
        return

    # Собираем отчёт. Ошибки строк доступны только здесь (в БД они не хранятся).
    lines = [
        f"📄 <b>{html.escape(report.filename)}</b> — загрузка №{report.upload_id} завершена",
        f"Всего строк: {report.rows_total}",
        f"Успешно: {report.rows_ok}",
        f"С ошибками: {report.rows_failed}",
    ]
    if report.errors:
        lines.append("")
        lines.append("Первые ошибки:")
        for error in report.errors[:MAX_ERRORS_SHOWN]:
            lines.append(f"• строка {error.line}: {html.escape(error.reason)}")
        if report.rows_failed > MAX_ERRORS_SHOWN:
            lines.append(f"… и ещё {report.rows_failed - MAX_ERRORS_SHOWN}")

    await _cleanup_upload(state)
    await callback.message.answer("\n".join(lines))


@router.message(UploadStates.waiting_supplier)
async def upload_waiting_supplier_hint(message: Message) -> None:
    """Подсказка, если на шаге выбора поставщика прислали сообщение."""
    await message.answer("Выберите поставщика кнопкой под сообщением выше или /cancel.")


@router.callback_query(F.data == CB_UPLOAD_CANCEL)
async def upload_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """Кнопка «Отмена» в диалоге загрузки."""
    await _cleanup_upload(state)
    await callback.answer()
    await callback.message.edit_text("Загрузка отменена.")


# ---------------------------------------------------------------------------
# /report — сводка по базе
# ---------------------------------------------------------------------------


@router.message(Command("report"), AdminFilter())
async def cmd_report(message: Message, session: AsyncSession) -> None:
    """Показывает число деталей и предложений и последние 5 загрузок."""
    parts_count = await session.scalar(select(func.count()).select_from(Part))
    offers_count = await session.scalar(select(func.count()).select_from(Offer))

    uploads = (
        await session.execute(
            select(PriceUpload, Supplier.name)
            .join(Supplier, PriceUpload.supplier_id == Supplier.id)
            .order_by(PriceUpload.uploaded_at.desc(), PriceUpload.id.desc())
            .limit(5)
        )
    ).all()

    lines = [
        "📊 <b>Сводка по базе</b>",
        f"Деталей: {parts_count}",
        f"Предложений: {offers_count}",
    ]
    if uploads:
        lines.append("")
        lines.append("Последние загрузки:")
        for upload, supplier_name in uploads:
            mark = _STATUS_MARKS.get(upload.status, "❓")
            lines.append(
                f"{mark} {html.escape(upload.filename)} ({html.escape(supplier_name)}) — "
                f"{upload.rows_ok}/{upload.rows_total}, ошибок {upload.rows_failed} — "
                f"{_fmt_dt(upload.uploaded_at)}"
            )
    else:
        lines.append("")
        lines.append("Загрузок ещё не было.")
    await message.answer("\n".join(lines))


# ---------------------------------------------------------------------------
# /pending — заявки на доступ
# ---------------------------------------------------------------------------


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
        f"• {html.escape(u.name or 'без имени')} — ID {u.telegram_id}"
        for u in users
    ]
    await message.answer("⏳ Ожидают одобрения:\n" + "\n".join(lines))


# ---------------------------------------------------------------------------
# Вежливый отказ для не-админов (регистрируется последним)
# ---------------------------------------------------------------------------


@router.message(Command("pending", "upload", "report", "cancel"))
async def admin_commands_denied(message: Message) -> None:
    """Вежливый отказ: админские хендлеры выше не подошли (AdminFilter не прошёл)."""
    await message.answer("⛔ Эта команда доступна только администратору.")
