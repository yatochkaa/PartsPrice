"""Тесты поиска (in-memory SQLite, данные наполняем напрямую)."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Currency, Offer, Part, Supplier
from app.services.search import find_by_name, find_by_oem


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _seed(session):
    now = datetime.now(timezone.utc)
    s1 = Supplier(name="Поставщик A", currency=Currency.RUB, is_active=True)
    s2 = Supplier(name="Поставщик B", currency=Currency.RUB, is_active=True)
    session.add_all([s1, s2])
    await session.flush()

    oil = Part(
        oem_normalized="W71275",
        oem_raw="W712/75",
        brand="Mann",
        name="Фильтр масляный",
    )
    pads = Part(
        oem_normalized="GDB1330",
        oem_raw="GDB1330",
        brand="Trw",
        name="Колодки тормозные передние",
    )
    session.add_all([oil, pads])
    await session.flush()

    session.add_all(
        [
            Offer(
                part_id=oil.id, supplier_id=s1.id, price_rub=Decimal("530.00"),
                price_original=Decimal("530.00"), currency=Currency.RUB,
                quantity=28, updated_at=now,
            ),
            Offer(
                part_id=oil.id, supplier_id=s2.id, price_rub=Decimal("512.00"),
                price_original=Decimal("512.00"), currency=Currency.RUB,
                quantity=34, updated_at=now,
            ),
            Offer(
                part_id=pads.id, supplier_id=s1.id, price_rub=Decimal("2890.00"),
                price_original=Decimal("2890.00"), currency=Currency.RUB,
                quantity=12, updated_at=now,
            ),
        ]
    )
    await session.commit()


@pytest.mark.asyncio
async def test_find_by_oem_different_spelling(session):
    await _seed(session)
    # Разное написание одного артикула ведёт к одной детали.
    for query in ("W712/75", "w712 / 75", "  W-712-75  "):
        results = await find_by_oem(session, query)
        assert len(results) == 1
        assert results[0].oem_normalized == "W71275"


@pytest.mark.asyncio
async def test_find_by_oem_sorted_by_price(session):
    await _seed(session)
    results = await find_by_oem(session, "W712/75")
    offers = results[0].offers
    prices = [o.price_rub for o in offers]
    assert prices == [Decimal("512.00"), Decimal("530.00")]  # по возрастанию
    assert offers[0].supplier_name == "Поставщик B"


@pytest.mark.asyncio
async def test_find_by_oem_empty_result(session):
    await _seed(session)
    assert await find_by_oem(session, "UNKNOWN-OEM-999") == []
    assert await find_by_oem(session, "   ") == []


@pytest.mark.asyncio
async def test_find_by_name_case_insensitive(session):
    await _seed(session)
    results = await find_by_name(session, "ФИЛЬТР")
    assert len(results) == 1
    assert results[0].oem_normalized == "W71275"
    # предложения тоже отсортированы по цене
    assert [o.price_rub for o in results[0].offers] == [
        Decimal("512.00"),
        Decimal("530.00"),
    ]


@pytest.mark.asyncio
async def test_find_by_name_limit_and_empty(session):
    await _seed(session)
    # буква 'р' есть и в "Фильтр", и в "Колодки тормозные" -> 2 совпадения
    all_r = await find_by_name(session, "р")
    assert len(all_r) == 2
    # limit ограничивает количество деталей
    limited = await find_by_name(session, "р", limit=1)
    assert len(limited) == 1
    # пустой запрос -> пусто
    assert await find_by_name(session, "") == []
    # нет совпадений -> пусто
    assert await find_by_name(session, "неттакого") == []
