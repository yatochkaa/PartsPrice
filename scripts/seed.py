"""Скрипт начального наполнения БД тестовыми данными.

Запуск из корня проекта:
    python -m scripts.seed

Что делает (всё идемпотентно — можно запускать повторно):
1. создаёт таблицы, если их ещё нет (удобно для быстрого старта без alembic);
2. создаёт/обновляет админа из ADMIN_TELEGRAM_ID (role=admin);
3. кладёт в кэш демо-курс USD (чтобы импорт поставщика в USD работал без интернета);
4. создаёт трёх поставщиков с маппингами колонок;
5. загружает их прайсы из sample_data/ через штатный import_price_file.

Сам импорт — upsert: повторный запуск обновляет предложения, а не плодит дубли.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import (
    Base,
    ColumnMapping,
    Currency,
    ExchangeRate,
    Supplier,
    User,
    UserRole,
)
from app.db.session import AsyncSessionLocal, engine
from app.services.importer import ImporterError, import_price_file

logger = logging.getLogger("seed")

# Корень проекта и папка с примерами прайсов (scripts/ лежит в корне).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = PROJECT_ROOT / "sample_data"

# Демо-курс USD->RUB для сида. В реальной работе курс тянется из ЦБ РФ
# (currency.get_rate). Мы кладём его в кэш только если его ещё нет, чтобы
# seed работал даже без интернета и был детерминированным.
DEMO_USD_RATE = Decimal("90.00")

# Маппинги колонок под разные форматы файлов.
_RU_MAPPING = {
    "oem_col": "Артикул",
    "brand_col": "Бренд",
    "name_col": "Наименование",
    "price_col": "Цена",
    "qty_col": "Остаток",
}
_EN_MAPPING = {
    "oem_col": "OEM",
    "brand_col": "Brand",
    "name_col": "Description",
    "price_col": "Price USD",
    "qty_col": "Qty",
}


@dataclass(frozen=True)
class SupplierSeed:
    """Описание поставщика для сида: имя, валюта, файл прайса и маппинг колонок."""

    name: str
    currency: Currency
    filename: str
    mapping: dict[str, str]


# Три поставщика: A и C — рублёвые (русские колонки), B — в USD (английские колонки).
# Файл supplier_c.csv содержит намеренно битые строки — показывает обработку ошибок.
SUPPLIERS: tuple[SupplierSeed, ...] = (
    SupplierSeed("Supplier A", Currency.RUB, "supplier_a.csv", _RU_MAPPING),
    SupplierSeed("Supplier B", Currency.USD, "supplier_b.csv", _EN_MAPPING),
    SupplierSeed("Supplier C", Currency.RUB, "supplier_c.csv", _RU_MAPPING),
)


async def _create_tables() -> None:
    """Создаёт таблицы, если их нет (no-op, если alembic уже отработал)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Схема БД готова (таблицы созданы или уже существовали)")


async def _ensure_admin(session: AsyncSession) -> None:
    """Создаёт или повышает до admin пользователя с ADMIN_TELEGRAM_ID."""
    admin_id = settings.ADMIN_TELEGRAM_ID
    if not admin_id:
        logger.warning(
            "ADMIN_TELEGRAM_ID не задан в .env — админ не создан. "
            "Укажите свой Telegram ID и запустите seed заново."
        )
        return

    user = await session.scalar(
        select(User).where(User.telegram_id == admin_id)
    )
    if user is None:
        session.add(
            User(telegram_id=admin_id, role=UserRole.admin, name="Администратор")
        )
        logger.info("Создан админ telegram_id=%s", admin_id)
    else:
        user.role = UserRole.admin
        logger.info("Пользователь telegram_id=%s повышен до admin", admin_id)


async def _ensure_usd_rate(session: AsyncSession) -> None:
    """Кладёт демо-курс USD в кэш, если его ещё нет (чтобы импорт USD шёл без сети)."""
    existing = await session.scalar(
        select(ExchangeRate).where(ExchangeRate.currency == Currency.USD)
    )
    if existing is None:
        session.add(
            ExchangeRate(
                currency=Currency.USD,
                rate=DEMO_USD_RATE,
                fetched_at=datetime.now(timezone.utc),
            )
        )
        logger.info("Добавлен демо-курс USD=%s (кэш)", DEMO_USD_RATE)
    else:
        logger.info("Курс USD уже есть в кэше: %s", existing.rate)


async def _ensure_supplier(session: AsyncSession, spec: SupplierSeed) -> Supplier:
    """Создаёт поставщика и маппинг, если их ещё нет. Возвращает поставщика."""
    supplier = await session.scalar(
        select(Supplier).where(Supplier.name == spec.name)
    )
    if supplier is None:
        supplier = Supplier(
            name=spec.name,
            currency=spec.currency,
            is_active=True,
        )
        session.add(supplier)
        await session.flush()  # нужен id для маппинга
        logger.info("Создан поставщик %r (id=%s, %s)", spec.name, supplier.id, spec.currency.value)

    mapping = await session.scalar(
        select(ColumnMapping).where(ColumnMapping.supplier_id == supplier.id)
    )
    if mapping is None:
        session.add(ColumnMapping(supplier_id=supplier.id, **spec.mapping))
        logger.info("Задан маппинг колонок для %r", spec.name)

    return supplier


async def seed() -> None:
    """Основная процедура наполнения."""
    await _create_tables()

    async with AsyncSessionLocal() as session:
        # 1. Админ, курс USD и поставщики с маппингами — в одной подготовительной транзакции.
        await _ensure_admin(session)
        await _ensure_usd_rate(session)
        suppliers = [await _ensure_supplier(session, spec) for spec in SUPPLIERS]
        await session.commit()

        # 2. Загружаем прайсы. import_price_file сам управляет транзакцией файла.
        for supplier, spec in zip(suppliers, SUPPLIERS):
            file_path = SAMPLE_DIR / spec.filename
            if not file_path.exists():
                logger.warning("Файл прайса не найден, пропуск: %s", file_path)
                continue
            try:
                report = await import_price_file(session, supplier.id, str(file_path))
            except ImporterError as exc:
                logger.error("Импорт %s провалился: %s", spec.filename, exc)
                continue
            logger.info(
                "Загружен %s: всего=%s, ok=%s, ошибок=%s",
                spec.filename,
                report.rows_total,
                report.rows_ok,
                report.rows_failed,
            )

    await engine.dispose()
    logger.info("Готово. БД наполнена тестовыми данными.")


def main() -> None:
    """Точка входа для `python -m scripts.seed`."""
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(seed())


if __name__ == "__main__":
    main()
