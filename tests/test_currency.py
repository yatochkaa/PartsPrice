"""Тесты сервиса получения и кэширования курсов валют."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import aiohttp
import pytest
import pytest_asyncio
from aioresponses import aioresponses
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Currency, ExchangeRate
from app.services.currency import (
    CBR_DAILY_URL,
    CurrencyUnavailableError,
    get_rate,
)


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """Создаёт отдельную in-memory SQLite для каждого теста."""
    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
    )

    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    try:
        yield test_engine
    finally:
        async with test_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await test_engine.dispose()


@pytest_asyncio.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Создаёт асинхронную тестовую сессию SQLAlchemy."""
    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    async with session_factory() as test_session:
        yield test_session
        await test_session.rollback()


@pytest.mark.asyncio
async def test_get_rate_returns_fresh_cache_without_http_request(
    session: AsyncSession,
) -> None:
    """Свежий курс возвращается из БД без обращения к API."""
    expected_rate = Decimal("91.2500")
    session.add(
        ExchangeRate(
            currency=Currency.USD,
            rate=expected_rate,
            fetched_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
    )
    await session.commit()

    with aioresponses() as mocked_http:
        actual_rate = await get_rate(session, "USD")
        assert actual_rate == expected_rate
        assert len(mocked_http.requests) == 0


@pytest.mark.asyncio
async def test_get_rate_refreshes_expired_cache_from_api(
    session: AsyncSession,
) -> None:
    """Протухший курс обновляется значением из API ЦБ РФ."""
    old_fetched_at = datetime.now(timezone.utc) - timedelta(hours=25)
    cached_rate = ExchangeRate(
        currency=Currency.USD,
        rate=Decimal("80.0000"),
        fetched_at=old_fetched_at,
    )
    session.add(cached_rate)
    await session.commit()

    response_body = '{"Valute":{"USD":{"Value":92.55}}}'
    with aioresponses() as mocked_http:
        mocked_http.get(
            CBR_DAILY_URL,
            status=200,
            body=response_body,
            headers={"Content-Type": "application/json"},
        )
        actual_rate = await get_rate(session, "USD")
        assert actual_rate == Decimal("92.55")
        assert len(mocked_http.requests) == 1

    await session.refresh(cached_rate)
    assert cached_rate.rate == Decimal("92.5500")

    rows_count = await session.scalar(
        select(func.count(ExchangeRate.id)).where(
            ExchangeRate.currency == Currency.USD
        )
    )
    assert rows_count == 1


@pytest.mark.asyncio
async def test_get_rate_uses_expired_cache_when_api_is_unavailable(
    session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """При недоступном API возвращается последний сохранённый курс."""
    expected_rate = Decimal("88.7500")
    cached_rate = ExchangeRate(
        currency=Currency.EUR,
        rate=expected_rate,
        fetched_at=datetime.now(timezone.utc) - timedelta(days=2),
    )
    session.add(cached_rate)
    await session.commit()

    caplog.set_level(logging.WARNING, logger="app.services.currency")
    with aioresponses() as mocked_http:
        mocked_http.get(CBR_DAILY_URL, status=503)
        actual_rate = await get_rate(session, "EUR")
        assert actual_rate == expected_rate
        assert len(mocked_http.requests) == 1

    assert "Используется последний сохранённый курс" in caplog.text


@pytest.mark.asyncio
async def test_get_rate_raises_when_api_and_cache_are_unavailable(
    session: AsyncSession,
) -> None:
    """Без API и сохранённого курса выбрасывается специальное исключение."""
    with aioresponses() as mocked_http:
        mocked_http.get(
            CBR_DAILY_URL,
            exception=aiohttp.ClientConnectionError(
                "Тестовая ошибка подключения"
            ),
        )

        with pytest.raises(
            CurrencyUnavailableError,
            match="API недоступен и сохранённого курса нет",
        ):
            await get_rate(session, "CNY")

        assert len(mocked_http.requests) == 1


@pytest.mark.asyncio
async def test_get_rate_returns_one_for_rub_without_http_request(
    session: AsyncSession,
) -> None:
    """Для RUB возвращается Decimal единица без HTTP-запроса."""
    with aioresponses() as mocked_http:
        actual_rate = await get_rate(session, "RUB")
        assert actual_rate == Decimal("1")
        assert isinstance(actual_rate, Decimal)
        assert len(mocked_http.requests) == 0

    rows_count = await session.scalar(select(func.count(ExchangeRate.id)))
    assert rows_count == 0
