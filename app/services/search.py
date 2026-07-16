"""Поиск запчастей и предложений по OEM и по названию.

Функции только читают БД. Предложения и поставщики грузятся через selectinload,
чтобы не получить проблему N+1 (отдельный запрос на каждую деталь/предложение).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Offer, Part
from app.services.normalizer import normalize_oem

logger = logging.getLogger(__name__)

__all__ = ["OfferView", "PartSearchResult", "find_by_oem", "find_by_name"]


@dataclass(frozen=True)
class OfferView:
    """Одно предложение поставщика для выдачи пользователю."""

    supplier_id: int
    supplier_name: str
    price_rub: Decimal
    currency: str
    quantity: int
    updated_at: datetime | None


@dataclass(frozen=True)
class PartSearchResult:
    """Деталь вместе со списком предложений (отсортированных по цене)."""

    part_id: int
    oem_normalized: str
    oem_raw: str
    brand: str
    name: str
    offers: list[OfferView]


# selectinload грузит offers одним доп. запросом на всю коллекцию деталей, а supplier — ещё
# одним. Так мы избегаем N+1: без отдельного SELECT на каждую строку.
_LOAD_OFFERS = selectinload(Part.offers).selectinload(Offer.supplier)


def _build_result(part: Part) -> PartSearchResult:
    """Собирает DTO из ORM-объекта; предложения сортируются по цене в рублях (дешёвые сверху)."""
    ordered = sorted(part.offers, key=lambda o: o.price_rub)
    offers = [
        OfferView(
            supplier_id=o.supplier_id,
            supplier_name=o.supplier.name if o.supplier is not None else "",
            price_rub=o.price_rub,
            currency=o.currency.value,
            quantity=o.quantity,
            updated_at=o.updated_at,
        )
        for o in ordered
    ]
    return PartSearchResult(
        part_id=part.id,
        oem_normalized=part.oem_normalized,
        oem_raw=part.oem_raw,
        brand=part.brand,
        name=part.name,
        offers=offers,
    )


async def find_by_oem(session: AsyncSession, query: str) -> list[PartSearchResult]:
    """Находит детали по нормализованному OEM (любое написание запроса — один ключ)."""
    try:
        oem_normalized = normalize_oem(query)
    except ValueError:
        # Пустой/мусорный запрос — просто нет результатов.
        return []
    result = await session.execute(
        select(Part)
        .where(Part.oem_normalized == oem_normalized)
        .options(_LOAD_OFFERS)
    )
    parts = result.scalars().all()
    logger.debug("find_by_oem(%r -> %s): найдено %d", query, oem_normalized, len(parts))
    return [_build_result(p) for p in parts]


async def find_by_name(
    session: AsyncSession,
    query: str,
    limit: int = 10,
) -> list[PartSearchResult]:
    """Находит детали по подстроке в названии (без учёта регистра).

    ВАЖНО про регистр: встроенная в SQLite функция lower() приводит к нижнему регистру
    только латиницу (ASCII) и не трогает кириллицу. Поэтому регистронезависимое
    сравнение делаем на стороне Python (str.lower() понимает Unicode) — так поиск
    одинаково работает и на SQLite, и на PostgreSQL.
    """
    text = (query or "").strip()
    if not text:
        return []
    needle = text.lower()
    # Грузим детали с предложениями одним пакетом (selectinload — без N+1), сортируем по имени.
    result = await session.execute(
        select(Part).options(_LOAD_OFFERS).order_by(Part.name)
    )
    parts = result.scalars().all()
    # Регистронезависимое совпадение по подстроке + ограничение количества.
    matched = [p for p in parts if needle in (p.name or "").lower()]
    logger.debug("find_by_name(%r): найдено %d (limit=%d)", query, len(matched), limit)
    return [_build_result(p) for p in matched[:limit]]
