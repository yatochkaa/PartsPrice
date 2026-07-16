"""Pydantic-схемы REST API.

Входные схемы (…Create) валидируют тело запроса.
Выходные схемы (…Out) сериализуют ORM-объекты и DTO сервисов:
from_attributes=True позволяет строить схему прямо из объекта
(SQLAlchemy-модели или dataclass), а не только из словаря.
Деньги — только Decimal: pydantic сериализует их в JSON как строки
без потери точности.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import Currency, UploadStatus


# --- Поставщики -------------------------------------------------------------

class SupplierCreate(BaseModel):
    """Тело запроса на создание поставщика."""

    name: str = Field(min_length=1, max_length=255, description="Название поставщика")
    # Валюта валидируется enum'ом из моделей: RUB/USD/EUR/CNY, иначе 422.
    currency: Currency = Field(description="Валюта прайса поставщика")
    contact: str | None = Field(default=None, max_length=255, description="Контакт")
    is_active: bool = Field(default=True, description="Активен ли поставщик")


class SupplierOut(BaseModel):
    """Поставщик в ответе API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    currency: Currency
    contact: str | None
    is_active: bool


# --- Маппинг колонок ---------------------------------------------------------

class MappingCreate(BaseModel):
    """Тело запроса на задание маппинга колонок прайса поставщика."""

    oem_col: str = Field(min_length=1, max_length=255, description="Колонка OEM")
    brand_col: str = Field(min_length=1, max_length=255, description="Колонка бренда")
    name_col: str = Field(min_length=1, max_length=255, description="Колонка названия")
    price_col: str = Field(min_length=1, max_length=255, description="Колонка цены")
    qty_col: str = Field(min_length=1, max_length=255, description="Колонка остатка")


# --- Отчёт о загрузке прайса --------------------------------------------------

class RowErrorOut(BaseModel):
    """Ошибка одной строки прайса в отчёте (вспомогательная часть UploadReportOut)."""

    model_config = ConfigDict(from_attributes=True)

    line: int
    reason: str


class UploadReportOut(BaseModel):
    """Итоговый отчёт по импорту файла (сериализация ImportReport из importer.py)."""

    model_config = ConfigDict(from_attributes=True)

    supplier_id: int
    filename: str
    upload_id: int | None
    status: UploadStatus
    rows_total: int
    rows_ok: int
    rows_failed: int
    errors: list[RowErrorOut]


# --- Поиск --------------------------------------------------------------------

class OfferOut(BaseModel):
    """Предложение поставщика (вспомогательная часть SearchResultOut)."""

    model_config = ConfigDict(from_attributes=True)

    supplier_id: int
    supplier_name: str
    # Decimal, а не float: цена сериализуется без потери копеек.
    price_rub: Decimal
    currency: str
    quantity: int
    updated_at: datetime | None


class SearchResultOut(BaseModel):
    """Деталь со списком предложений (сериализация PartSearchResult из search.py)."""

    model_config = ConfigDict(from_attributes=True)

    part_id: int
    oem_normalized: str
    oem_raw: str
    brand: str
    name: str
    offers: list[OfferOut]
