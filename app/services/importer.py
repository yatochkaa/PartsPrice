"""Импорт прайс-листов поставщиков (CSV/Excel) в БД.

Алгоритм на один файл:
1. читаем файл (CSV с автоопределением разделителя ';' / ',' или .xlsx) через pandas;
2. берём маппинг колонок поставщика из column_mappings;
3. каждую строку прогоняем через normalizer; битая строка не роняет файл, а попадает в отчёт;
4. цена пересчитывается в рубли по курсу (один раз на файл, не на строку);
5. parts — upsert по (oem_normalized, brand); offers — upsert по (part_id, supplier_id);
6. всё в одной транзакции: упали — откат и status=failed;
7. итоги пишем в price_uploads.
"""
from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    ColumnMapping,
    Currency,
    Offer,
    Part,
    PriceUpload,
    Supplier,
    UploadStatus,
)
from app.services.currency import get_rate
from app.services.normalizer import (
    normalize_brand,
    normalize_oem,
    parse_price,
    parse_quantity,
)

logger = logging.getLogger(__name__)

__all__ = ["ImporterError", "RowError", "ImportReport", "import_price_file"]

# Сколько ошибок строк храним в отчёте (первые 20 по техзаданию).
MAX_ERRORS_STORED = 20
# Размер батча: через каждые N успешных строк делаем flush.
BATCH_SIZE = 500


class ImporterError(RuntimeError):
    """Фатальная ошибка импорта (нет маппинга/колонки, файл не читается, откат транзакции)."""


@dataclass
class RowError:
    """Ошибка строки: номер (1-based, с учётом шапки) и причина."""

    line: int
    reason: str


@dataclass
class ImportReport:
    """Итоговый отчёт по импорту файла."""

    supplier_id: int
    filename: str
    upload_id: int | None
    status: UploadStatus
    rows_total: int
    rows_ok: int
    rows_failed: int
    errors: list[RowError] = field(default_factory=list)


def _detect_delimiter(path: Path) -> str:
    """Автоопределение разделителя по шапке: ';' или ','.

    Считаем разделители только в первой строке (заголовке), чтобы запятые в ценах ("1 234,50")
    не сбивали выбор. При равенстве предпочитаем ';'.
    """
    with open(path, encoding="utf-8-sig", newline="") as fh:
        header = fh.readline()
    return ";" if header.count(";") >= header.count(",") else ","


def _read_dataframe(path: Path) -> pd.DataFrame:
    """Синхронное чтение файла в DataFrame (вызывается в отдельном потоке).

    Всё читаем как строки (dtype=str, keep_default_na=False):
    — пустые ячейки становятся "", а не NaN (иначе normalize_oem(NaN) не отловит пустоту);
    — числа не превращаются в numpy-типы, чтобы parse_price/parse_quantity получали чистые str.
    """
    suffix = path.suffix.lower()
    if suffix == ".csv":
        delimiter = _detect_delimiter(path)
        return pd.read_csv(
            path,
            sep=delimiter,
            dtype=str,
            keep_default_na=False,
            encoding="utf-8-sig",
        )
    if suffix == ".xlsx":
        return pd.read_excel(
            path,
            dtype=str,
            keep_default_na=False,
            engine="openpyxl",
        )
    raise ImporterError(
        f"Неподдерживаемый формат файла: {suffix!r} (ожидаются .csv или .xlsx)"
    )


def _ensure_columns(df: pd.DataFrame, mapping: ColumnMapping, filename: str) -> None:
    """Проверяет, что все ожидаемые по маппингу колонки есть в файле."""
    required = [
        mapping.oem_col,
        mapping.brand_col,
        mapping.name_col,
        mapping.price_col,
        mapping.qty_col,
    ]
    present = set(df.columns)
    missing = [col for col in required if col not in present]
    if missing:
        raise ImporterError(
            f"В файле {filename!r} нет колонок {missing}; есть: {sorted(present)}"
        )


def _cell(row: pd.Series, column: str) -> str:
    """Безопасно достаёт значение ячейки как строку (NaN -> "")."""
    value = row[column]
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value)


async def _load_supplier(session: AsyncSession, supplier_id: int) -> Supplier:
    """Загружает поставщика вместе с маппингом (selectinload — без ленивой загрузки в async)."""
    result = await session.execute(
        select(Supplier)
        .options(selectinload(Supplier.column_mapping))
        .where(Supplier.id == supplier_id)
    )
    supplier = result.scalar_one_or_none()
    if supplier is None:
        raise ImporterError(f"Поставщик id={supplier_id} не найден")
    return supplier


async def _get_or_create_part(
    session: AsyncSession,
    oem_normalized: str,
    oem_raw: str,
    brand: str,
    name: str,
) -> Part:
    """upsert детали по уникальной паре (oem_normalized, brand)."""
    result = await session.execute(
        select(Part).where(
            Part.oem_normalized == oem_normalized,
            Part.brand == brand,
        )
    )
    part = result.scalar_one_or_none()
    if part is None:
        part = Part(
            oem_normalized=oem_normalized,
            oem_raw=oem_raw.strip(),
            brand=brand,
            name=name,
        )
        session.add(part)
    else:
        # Обновляем отображаемые поля на последние значения из прайса.
        part.oem_raw = oem_raw.strip()
        part.name = name
    return part


async def _get_or_create_offer(
    session: AsyncSession,
    part: Part,
    supplier_id: int,
) -> Offer:
    """upsert предложения по (part_id, supplier_id). Новая загрузка обновляет, а не дублирует."""
    # Если деталь только что создана (id ещё нет) — существующего предложения быть не может.
    if part.id is not None:
        result = await session.execute(
            select(Offer).where(
                Offer.part_id == part.id,
                Offer.supplier_id == supplier_id,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing
    # Плейсхолдеры обязательных полей — реальные значения проставляются сразу после вызова.
    offer = Offer(
        part=part,
        supplier_id=supplier_id,
        price_rub=Decimal("0"),
        price_original=Decimal("0"),
        currency=Currency.RUB,
        quantity=0,
    )
    session.add(offer)
    return offer


async def _process_rows(
    session: AsyncSession,
    df: pd.DataFrame,
    mapping: ColumnMapping,
    supplier: Supplier,
    upload: PriceUpload,
    rate: Decimal,
) -> tuple[int, int, list[RowError]]:
    """Обрабатывает строки DataFrame: нормализация + upsert. Битые строки — в отчёт."""
    # Кэши в пределах одного файла: защищают от дублей строк внутри файла
    # (одинаковый OEM+бренд — одна деталь и одно предложение, последняя строка побеждает).
    parts_cache: dict[tuple[str, str], Part] = {}
    offers_cache: dict[tuple[str, str], Offer] = {}
    errors: list[RowError] = []
    rows_ok = 0
    rows_failed = 0
    pending = 0

    for idx, row in df.iterrows():
        # Номер строки в файле: +1 на заголовок, +1 для перевода в 1-based.
        line_no = int(idx) + 2
        try:
            oem_raw = _cell(row, mapping.oem_col)
            oem_normalized = normalize_oem(oem_raw)
            brand = normalize_brand(_cell(row, mapping.brand_col))
            name = _cell(row, mapping.name_col).strip()
            price_original = parse_price(_cell(row, mapping.price_col))
            quantity = parse_quantity(_cell(row, mapping.qty_col))
        except ValueError as exc:
            rows_failed += 1
            if len(errors) < MAX_ERRORS_STORED:
                errors.append(RowError(line=line_no, reason=str(exc)))
            logger.debug("Строка %s отклонена: %s", line_no, exc)
            continue

        if not brand:
            # Бренд обязателен для уникальности (oem_normalized, brand).
            rows_failed += 1
            if len(errors) < MAX_ERRORS_STORED:
                errors.append(RowError(line=line_no, reason="пустой бренд"))
            continue

        # Пересчёт в рубли по курсу файла. Деньги — Decimal, округляем до копеек.
        price_rub = (price_original * rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        key = (oem_normalized, brand)
        part = parts_cache.get(key)
        if part is None:
            part = await _get_or_create_part(
                session, oem_normalized, oem_raw, brand, name
            )
            parts_cache[key] = part
        else:
            part.oem_raw = oem_raw.strip()
            part.name = name

        offer = offers_cache.get(key)
        if offer is None:
            offer = await _get_or_create_offer(session, part, supplier.id)
            offers_cache[key] = offer

        # Проставляем актуальные значения (при дубле в файле побеждает последняя строка).
        offer.price_original = price_original
        offer.price_rub = price_rub
        offer.currency = supplier.currency
        offer.quantity = quantity
        offer.upload = upload

        rows_ok += 1
        pending += 1
        if pending >= BATCH_SIZE:
            # Вставка батчами: сбрасываем накопленное в БД (без commit — транзакция одна на файл).
            await session.flush()
            pending = 0

    await session.flush()
    return rows_ok, rows_failed, errors


async def _record_failure(
    session: AsyncSession,
    supplier_id: int,
    filename: str,
    rows_total: int,
) -> None:
    """Отдельной транзакцией фиксируем факт провала загрузки (status=failed)."""
    try:
        failed = PriceUpload(
            supplier_id=supplier_id,
            filename=filename,
            rows_total=rows_total,
            rows_ok=0,
            rows_failed=rows_total,
            status=UploadStatus.failed,
        )
        session.add(failed)
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception("Не удалось записать status=failed для файла %s", filename)


async def import_price_file(
    session: AsyncSession,
    supplier_id: int,
    file_path: str,
) -> ImportReport:
    """Импортирует прайс-лист поставщика и возвращает отчёт."""
    path = Path(file_path)
    logger.info("Импорт прайса: supplier_id=%s, файл=%s", supplier_id, path)

    # 1. Поставщик и маппинг колонок.
    supplier = await _load_supplier(session, supplier_id)
    mapping = supplier.column_mapping
    if mapping is None:
        raise ImporterError(
            f"У поставщика id={supplier_id} не задан маппинг колонок (column_mappings)"
        )

    # 2. Чтение файла В ОТДЕЛЬНОМ ПОТОКЕ.
    #    pandas синхронный и блокирующий: чтение большого CSV/Excel заняло бы event loop
    #    целиком и затормозило бы весь асинхронный сервис (бот/API).
    #    asyncio.to_thread выносит блокирующую работу в пул потоков, не блокируя цикл событий.
    try:
        df = await asyncio.to_thread(_read_dataframe, path)
    except ImporterError:
        raise
    except Exception as exc:
        raise ImporterError(f"Не удалось прочитать файл {path.name}: {exc}") from exc

    # 3. Проверка наличия колонок по маппингу.
    _ensure_columns(df, mapping, path.name)
    rows_total = int(len(df))

    # 4. Курс ВАЛЮТЫ ОДИН РАЗ НА ФАЙЛ (не на каждую строку).
    try:
        rate = await get_rate(session, supplier.currency.value)
    except Exception as exc:
        raise ImporterError(
            f"Не удалось получить курс {supplier.currency.value}: {exc}"
        ) from exc

    # 5. Загрузка + строки — ОДНА ТРАНЗАКЦИЯ. Упали — rollback и status=failed.
    upload = PriceUpload(
        supplier_id=supplier.id,
        filename=path.name,
        rows_total=rows_total,
        status=UploadStatus.pending,
    )
    session.add(upload)
    try:
        await session.flush()  # получаем upload.id до коммита
        upload_id = upload.id
        rows_ok, rows_failed, errors = await _process_rows(
            session, df, mapping, supplier, upload, rate
        )
        upload.rows_ok = rows_ok
        upload.rows_failed = rows_failed
        upload.status = UploadStatus.done
        await session.commit()
    except Exception as exc:
        await session.rollback()
        logger.exception("Импорт файла %s провалился, откат транзакции", path.name)
        await _record_failure(session, supplier_id, path.name, rows_total)
        raise ImporterError(f"Импорт файла {path.name} провалился: {exc}") from exc

    logger.info(
        "Импорт завершён: total=%s ok=%s failed=%s",
        rows_total,
        rows_ok,
        rows_failed,
    )
    return ImportReport(
        supplier_id=supplier_id,
        filename=path.name,
        upload_id=upload_id,
        status=UploadStatus.done,
        rows_total=rows_total,
        rows_ok=rows_ok,
        rows_failed=rows_failed,
        errors=errors,
    )
