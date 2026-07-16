"""Поиск запчастей в боте.

Любое текстовое сообщение (не команда) от manager/admin — поисковый запрос:
1. сначала ищем по OEM (номер нормализуется: «W712/75», «w712-75» и «w712 75» — один ключ);
2. если по OEM ничего нет — ищем по подстроке в названии (без учёта регистра);
3. ответ — карточки HTML: деталь и бренд жирным, предложения по возрастанию цены,
   лучшая цена помечена ✅.

Хендлер работает только вне FSM-состояний (StateFilter(None)), чтобы не перехватывать
сообщения во время диалога загрузки прайса (/upload).
"""
from __future__ import annotations

import html
import logging
from datetime import datetime, timezone
from decimal import Decimal

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User, UserRole
from app.services.search import PartSearchResult, find_by_name, find_by_oem

logger = logging.getLogger(__name__)

router = Router(name="search")

# Сколько деталей показываем за один запрос:
# защита от «простыней» и лимита Telegram на длину сообщения (4096 символов).
MAX_PARTS = 5


def _fmt_price(value: Decimal) -> str:
    """Форматирует цену: два знака после точки, пробел как разделитель тысяч."""
    return f"{value:,.2f}".replace(",", " ")


def _fmt_date(value: datetime | None) -> str:
    """Дата обновления предложения: в БД хранится UTC, показываем локальную дату."""
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone().strftime("%d.%m.%Y")


def _format_part(part: PartSearchResult) -> str:
    """Собирает HTML-карточку детали со списком предложений (уже отсортированы по цене)."""
    lines = [
        f"🔧 <b>{html.escape(part.oem_raw)}</b> — <b>{html.escape(part.brand)}</b>",
        html.escape(part.name),
        "",
    ]
    if not part.offers:
        lines.append("Предложений от поставщиков пока нет.")
        return "\n".join(lines)

    for index, offer in enumerate(part.offers):
        # Первое предложение — самое дешёвое (сервис сортирует по price_rub).
        mark = "✅" if index == 0 else "▫️"
        currency_note = "" if offer.currency == "RUB" else f" (пересчёт из {offer.currency})"
        date = _fmt_date(offer.updated_at)
        lines.append(
            f"{mark} {html.escape(offer.supplier_name)} — "
            f"{_fmt_price(offer.price_rub)} ₽{currency_note} — "
            f"{offer.quantity} шт. — {date}"
        )
    return "\n".join(lines)


@router.message(F.text, ~F.text.startswith("/"), StateFilter(None))
async def text_search(
    message: Message,
    session: AsyncSession,
    user: User | None,
) -> None:
    """Обрабатывает любой текст как поисковый запрос (доступно manager и admin)."""
    # Доступ: незнакомым и pending поиск недоступен.
    if user is None:
        await message.answer(
            "У вас пока нет доступа к поиску. Нажмите /start, чтобы отправить заявку."
        )
        return
    if user.role == UserRole.pending:
        await message.answer(
            "⏳ Ваша заявка ещё на рассмотрении у администратора. Поиск станет доступен после одобрения."
        )
        return

    query = (message.text or "").strip()
    if not query:
        return

    # 1. Сначала точный поиск по OEM (запрос нормализуется внутри сервиса).
    results = await find_by_oem(session, query)

    # 2. Не нашли по номеру — ищем по подстроке в названии.
    if not results:
        results = await find_by_name(session, query, limit=MAX_PARTS)

    if not results:
        await message.answer(
            f"😕 По запросу «{html.escape(query)}» ничего не найдено.\n\n"
            "Подсказки:\n"
            "• пришлите номер детали (OEM), например <code>W712/75</code> — "
            "регистр, пробелы и дефисы не важны;\n"
            "• или слово из названия, например <code>фильтр</code>."
        )
        return

    shown = results[:MAX_PARTS]
    cards = [_format_part(part) for part in shown]
    text = "\n\n".join(cards)

    # Если результатов больше лимита — честно говорим об этом.
    if len(results) > MAX_PARTS or len(results) == MAX_PARTS:
        text += (
            f"\n\nПоказано деталей: {len(shown)}. "
            "Если нужной нет — уточните запрос (точный OEM или более конкретное слово)."
        )

    logger.info(
        "Поиск от telegram_id=%s: %r -> %d деталей",
        user.telegram_id,
        query,
        len(shown),
    )
    await message.answer(text)
