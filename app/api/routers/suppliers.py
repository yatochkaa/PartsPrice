"""Роутер управления поставщиками и маппингом колонок их прайсов.

Весь роутер требует роли admin: управление поставщиками — админская операция.
Зависимость require_admin навешена на весь APIRouter через dependencies=[...].
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import require_admin
from app.api.schemas import MappingCreate, SupplierCreate, SupplierOut
from app.db.models import ColumnMapping, Supplier
from app.db.session import get_session

logger = logging.getLogger(__name__)

# dependencies=[Depends(require_admin)] — авторизация проверяется для всех ручек роутера.
router = APIRouter(
    prefix="/suppliers",
    tags=["suppliers"],
    dependencies=[Depends(require_admin)],
)


@router.post("", response_model=SupplierOut, status_code=status.HTTP_201_CREATED)
async def create_supplier(
    payload: SupplierCreate,
    session: AsyncSession = Depends(get_session),
) -> Supplier:
    """Создаёт поставщика. Валюта валидируется схемой (RUB/USD/EUR/CNY -> иначе 422)."""
    supplier = Supplier(
        name=payload.name.strip(),
        currency=payload.currency,
        contact=payload.contact,
        is_active=payload.is_active,
    )
    session.add(supplier)
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception("Не удалось создать поставщика %r", payload.name)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Не удалось создать поставщика",
        )
    await session.refresh(supplier)
    logger.info("Создан поставщик id=%s name=%r", supplier.id, supplier.name)
    return supplier


@router.get("", response_model=list[SupplierOut])
async def list_suppliers(
    session: AsyncSession = Depends(get_session),
) -> list[Supplier]:
    """Возвращает всех поставщиков, отсортированных по id."""
    result = await session.execute(select(Supplier).order_by(Supplier.id))
    return list(result.scalars().all())


@router.post("/{supplier_id}/mapping", status_code=status.HTTP_201_CREATED)
async def set_supplier_mapping(
    supplier_id: int,
    payload: MappingCreate,
    session: AsyncSession = Depends(get_session),
) -> dict[str, int | str]:
    """Задаёт маппинг колонок прайса поставщика (повторный вызов — обновление, не дубль)."""
    # selectinload обязателен: ленивая подгрузка column_mapping в async-контексте упадёт.
    result = await session.execute(
        select(Supplier)
        .options(selectinload(Supplier.column_mapping))
        .where(Supplier.id == supplier_id)
    )
    supplier = result.scalar_one_or_none()
    if supplier is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Поставщик id={supplier_id} не найден",
        )

    mapping = supplier.column_mapping
    if mapping is None:
        mapping = ColumnMapping(
            supplier_id=supplier.id,
            oem_col=payload.oem_col,
            brand_col=payload.brand_col,
            name_col=payload.name_col,
            price_col=payload.price_col,
            qty_col=payload.qty_col,
        )
        session.add(mapping)
    else:
        mapping.oem_col = payload.oem_col
        mapping.brand_col = payload.brand_col
        mapping.name_col = payload.name_col
        mapping.price_col = payload.price_col
        mapping.qty_col = payload.qty_col

    try:
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception("Не удалось сохранить маппинг поставщика id=%s", supplier_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Не удалось сохранить маппинг",
        )
    await session.refresh(mapping)
    logger.info("Маппинг поставщика id=%s сохранён (mapping_id=%s)", supplier_id, mapping.id)
    return {"status": "ok", "supplier_id": supplier_id, "mapping_id": mapping.id}
