"""Идемпотентное наполнение БД начальными данными.

Создаёт:
- администратора из ADMIN_TELEGRAM_ID;
- трёх поставщиков (A — RUB, B — USD, C — RUB) с маппингами колонок.

Повторный запуск не создаёт дублей: перед вставкой проверяем наличие записи.
Запуск: python -m scripts.seed
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import (
    ColumnMapping,
    Currency,
    Supplier,
    User,
    UserRole,
)
from app.db.session import AsyncSessionLocal, engine

# Простое конфигурирование логов для скрипта (без зависимости от app/core/logging.py).
logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="%(levelname)-5.5s [%(name)s] %(message)s",
)
logger = logging.getLogger("seed")


# Описание поставщиков и их маппингов колонок (строго по ТЕХСПЕЦ).
# A/C — русские заголовки, B — английские.
SUPPLIERS_SEED: list[dict] = [
    {
        "name": "Supplier A",
        "currency": Currency.RUB,
        "mapping": {
            "oem_col": "Артикул",
            "brand_col": "Бренд",
            "name_col": "Наименование",
            "price_col": "Цена",
            "qty_col": "Остаток",
        },
    },
    {
        "name": "Supplier B",
        "currency": Currency.USD,
        "mapping": {
            "oem_col": "OEM",
            "brand_col": "Brand",
            "name_col": "Description",
            "price_col": "Price USD",
            "qty_col": "Qty",
        },
    },
    {
        "name": "Supplier C",
        "currency": Currency.RUB,
        "mapping": {
            "oem_col": "Артикул",
            "brand_col": "Бренд",
            "name_col": "Наименование",
            "price_col": "Цена",
            "qty_col": "Остаток",
        },
    },
]


async def _ensure_admin(session: AsyncSession) -> None:
    """Создаёт админа из ADMIN_TELEGRAM_ID, если его ещё нет."""
    if not settings.ADMIN_TELEGRAM_ID:
        # Без ID администратора создавать нечего — предупреждаем, но не падаем.
        logger.warning("ADMIN_TELEGRAM_ID не задан в .env — админ не будет создан")
        return

    # Проверяем наличие по уникальному telegram_id (гарантия идемпотентности).
    existing = await session.scalar(
        select(User).where(User.telegram_id == settings.ADMIN_TELEGRAM_ID)
    )
    if existing is not None:
        logger.info("Админ уже существует (telegram_id=%s) — пропускаю", existing.telegram_id)
        return

    admin = User(
        telegram_id=settings.ADMIN_TELEGRAM_ID,
        role=UserRole.admin,
        name="Administrator",
    )
    session.add(admin)
    logger.info("Создан админ telegram_id=%s", settings.ADMIN_TELEGRAM_ID)


async def _ensure_supplier(session: AsyncSession, data: dict) -> None:
    """Создаёт поставщика и его маппинг, если поставщика с таким name ещё нет."""
    existing = await session.scalar(
        select(Supplier).where(Supplier.name == data["name"])
    )
    if existing is not None:
        logger.info("Поставщик %r уже существует — пропускаю", data["name"])
        return

    supplier = Supplier(
        name=data["name"],
        currency=data["currency"],
        is_active=True,
    )
    # Привязываем маппинг сразу через relationship — сохранится каскадно.
    supplier.column_mapping = ColumnMapping(**data["mapping"])
    session.add(supplier)
    logger.info("Создан поставщик %r (%s) с маппингом", data["name"], data["currency"].value)


async def seed() -> None:
    """Основная точка входа наполнения данными."""
    logger.info("Старт seed...")
    async with AsyncSessionLocal() as session:
        try:
            await _ensure_admin(session)
            for supplier_data in SUPPLIERS_SEED:
                await _ensure_supplier(session, supplier_data)
            # Один commit на всю операцию — атомарно.
            await session.commit()
            logger.info("Seed завершён успешно")
        except Exception:
            await session.rollback()
            logger.exception("Ошибка во время seed — изменения откатаны")
            raise
    # Закрываем движок, чтобы скрипт корректно завершился без «висящих» соединений.
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
