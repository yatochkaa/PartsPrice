"""Получение и кэширование курсов валют к российскому рублю."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Final

import aiohttp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Currency, ExchangeRate

logger = logging.getLogger(__name__)

CBR_DAILY_URL: Final[str] = "https://www.cbr-xml-daily.ru/daily_json.js"
CACHE_TTL: Final[timedelta] = timedelta(hours=24)
HTTP_TIMEOUT_SECONDS: Final[int] = 10
SUPPORTED_CURRENCIES: Final[frozenset[str]] = frozenset(
    {Currency.USD.value, Currency.EUR.value, Currency.CNY.value}
)

__all__ = ["CurrencyUnavailableError", "get_rate"]


class CurrencyUnavailableError(RuntimeError):
    """Курс валюты невозможно получить ни из API, ни из кэша."""


async def get_rate(session: AsyncSession, currency: str) -> Decimal:
    """Возвращает курс валюты к рублю, используя 24-часовой кэш в БД."""
    if not isinstance(currency, str):
        raise CurrencyUnavailableError(
            f"Код валюты должен быть строкой, получен {type(currency).__name__}"
        )

    currency_code = currency.strip().upper()

    # Рубль — базовая валюта: БД и HTTP-запрос не нужны.
    if currency_code == Currency.RUB.value:
        return Decimal("1")

    if currency_code not in SUPPORTED_CURRENCIES:
        raise CurrencyUnavailableError(
            f"Неподдерживаемая валюта: {currency_code or currency!r}"
        )

    currency_enum = Currency(currency_code)
    now = datetime.now(timezone.utc)

    # Берём последнюю запись; id разрешает совпадения fetched_at однозначно.
    result = await session.execute(
        select(ExchangeRate)
        .where(ExchangeRate.currency == currency_enum)
        .order_by(ExchangeRate.fetched_at.desc(), ExchangeRate.id.desc())
        .limit(1)
    )
    cached_rate = result.scalar_one_or_none()

    if cached_rate is not None:
        fetched_at = cached_rate.fetched_at

        # SQLite может вернуть datetime без timezone даже для timezone=True.
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        else:
            fetched_at = fetched_at.astimezone(timezone.utc)

        if now - fetched_at < CACHE_TTL:
            logger.debug(
                "Используется свежий курс %s из кэша: %s",
                currency_code,
                cached_rate.rate,
            )
            return Decimal(cached_rate.rate)

    try:
        timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout) as http_session:
            async with http_session.get(CBR_DAILY_URL) as response:
                response.raise_for_status()
                response_text = await response.text()

        # parse_float=Decimal исключает промежуточное преобразование денег во float.
        payload = json.loads(
            response_text,
            parse_float=Decimal,
            parse_int=Decimal,
        )
        raw_rate = payload["Valute"][currency_code]["Value"]
        fetched_rate = (
            raw_rate if isinstance(raw_rate, Decimal) else Decimal(str(raw_rate))
        )

        if not fetched_rate.is_finite() or fetched_rate <= Decimal("0"):
            raise ValueError(
                f"API вернул некорректный курс {currency_code}: {raw_rate!r}"
            )

    except (
        aiohttp.ClientError,
        TimeoutError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
        InvalidOperation,
    ) as exc:
        if cached_rate is not None:
            logger.warning(
                "Не удалось обновить курс %s через API ЦБ РФ: %s. "
                "Используется последний сохранённый курс %s",
                currency_code,
                exc,
                cached_rate.rate,
            )
            return Decimal(cached_rate.rate)

        logger.error(
            "Курс %s недоступен: API не ответил и сохранённого курса нет: %s",
            currency_code,
            exc,
        )
        raise CurrencyUnavailableError(
            f"Не удалось получить курс {currency_code}: "
            "API недоступен и сохранённого курса нет"
        ) from exc

    try:
        if cached_rate is None:
            cached_rate = ExchangeRate(
                currency=currency_enum,
                rate=fetched_rate,
                fetched_at=now,
            )
            session.add(cached_rate)
        else:
            # Новая загрузка обновляет кэш, не создавая дубль.
            cached_rate.rate = fetched_rate
            cached_rate.fetched_at = now

        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception("Не удалось сохранить курс %s в БД", currency_code)
        raise

    logger.info("Курс %s обновлён через API: %s", currency_code, fetched_rate)
    return fetched_rate
