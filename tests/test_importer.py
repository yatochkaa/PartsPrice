"""Тесты импортёра прайс-листов (in-memory SQLite, файлы из sample_data/)."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.db.models import (
    Base,
    ColumnMapping,
    Currency,
    Offer,
    Part,
    PriceUpload,
    Supplier,
    UploadStatus,
)
from app.services import importer
from app.services.importer import ImporterError, import_price_file

# sample_data/ лежит в корне проекта (на уровень выше tests/).
SAMPLE_DIR = Path(__file__).resolve().parents[1] / "sample_data"

A_MAP = dict(
    oem_col="Артикул",
    brand_col="Бренд",
    name_col="Наименование",
    price_col="Цена",
    qty_col="Остаток",
)
B_MAP = dict(
    oem_col="OEM",
    brand_col="Brand",
    name_col="Description",
    price_col="Price USD",
    qty_col="Qty",
)


@pytest_asyncio.fixture
async def session():
    # StaticPool + одно соединение: in-memory SQLite живёт, пока жива связь.
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


async def _make_supplier(session, name, currency, mapping_cols, with_mapping=True):
    supplier = Supplier(name=name, currency=currency, is_active=True)
    if with_mapping:
        supplier.column_mapping = ColumnMapping(**mapping_cols)
    session.add(supplier)
    await session.commit()
    return supplier


async def _count(session, model) -> int:
    return (
        await session.execute(select(func.count()).select_from(model))
    ).scalar_one()


@pytest.mark.asyncio
async def test_clean_import(session):
    supplier = await _make_supplier(session, "Supplier A", Currency.RUB, A_MAP)
    report = await import_price_file(
        session, supplier.id, str(SAMPLE_DIR / "supplier_a.csv")
    )
    assert report.status == UploadStatus.done
    assert report.rows_total == 30
    assert report.rows_ok == 30
    assert report.rows_failed == 0
    assert report.errors == []
    assert await _count(session, Part) == 30
    assert await _count(session, Offer) == 30


@pytest.mark.asyncio
async def test_dirty_import_report(session):
    supplier = await _make_supplier(session, "Supplier C", Currency.RUB, A_MAP)
    report = await import_price_file(
        session, supplier.id, str(SAMPLE_DIR / "supplier_c.csv")
    )
    # Файл не падает из-за битых строк — статус done.
    assert report.status == UploadStatus.done
    assert report.rows_total == 30
    # 4 битых: пустой OEM, текст в цене, отриц. остаток, отриц. цена.
    assert report.rows_failed == 4
    assert report.rows_ok == 26
    assert len(report.errors) == 4
    # 26 успешных строк, но cu2545 дублируется -> 25 уникальных деталей.
    assert await _count(session, Part) == 25
    assert await _count(session, Offer) == 25


@pytest.mark.asyncio
async def test_reimport_no_duplicates(session):
    supplier = await _make_supplier(session, "Supplier A", Currency.RUB, A_MAP)
    path = str(SAMPLE_DIR / "supplier_a.csv")
    await import_price_file(session, supplier.id, path)
    await import_price_file(session, supplier.id, path)
    # Повторная загрузка ОБНОВЛЯЕТ, а не плодит дубли.
    assert await _count(session, Part) == 30
    assert await _count(session, Offer) == 30
    assert await _count(session, PriceUpload) == 2


@pytest.mark.asyncio
async def test_currency_conversion_mocked(session, monkeypatch):
    supplier = await _make_supplier(session, "Supplier B", Currency.USD, B_MAP)

    async def fake_get_rate(sess, currency):
        assert currency == "USD"
        return Decimal("100")

    # Мокаем курс: не ходим в сеть, 1 USD = 100 RUB.
    monkeypatch.setattr(importer, "get_rate", fake_get_rate)

    report = await import_price_file(
        session, supplier.id, str(SAMPLE_DIR / "supplier_b.csv")
    )
    assert report.rows_ok == 30
    assert report.rows_failed == 0

    offer = (
        await session.execute(
            select(Offer).join(Part).where(Part.oem_normalized == "W71275")
        )
    ).scalar_one()
    assert offer.currency == Currency.USD
    assert offer.price_original == Decimal("6.80")
    assert offer.price_rub == Decimal("680.00")  # 6.80 * 100


@pytest.mark.asyncio
async def test_missing_column_raises(session):
    bad_map = dict(A_MAP, oem_col="НетТакойКолонки")
    supplier = await _make_supplier(session, "Supplier X", Currency.RUB, bad_map)
    with pytest.raises(ImporterError):
        await import_price_file(
            session, supplier.id, str(SAMPLE_DIR / "supplier_a.csv")
        )


@pytest.mark.asyncio
async def test_missing_mapping_raises(session):
    supplier = await _make_supplier(
        session, "Supplier Y", Currency.RUB, A_MAP, with_mapping=False
    )
    with pytest.raises(ImporterError):
        await import_price_file(
            session, supplier.id, str(SAMPLE_DIR / "supplier_a.csv")
        )
