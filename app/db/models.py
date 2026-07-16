"""ORM-модели БД проекта PartsPrice Hub (SQLAlchemy 2.x).

Используем современный стиль: DeclarativeBase + Mapped + mapped_column.
Деньги храним только в Numeric/Decimal — float для валют запрещён (потеря точности).
"""
from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)

# Точность денежных полей: до 18 знаков всего, 4 знака после запятой.
# Этого хватает и для рублей, и для конвертаций по курсу без потери копеек.
MONEY = Numeric(18, 4)


class Base(DeclarativeBase):
    """Общий базовый класс для всех моделей — от него берётся metadata для Alembic."""


# --- Перечисления (enum) ---------------------------------------------------
# Храним как строковые значения в БД (VARCHAR). Так читаемо и переносимо между SQLite/PostgreSQL.


class Currency(str, enum.Enum):
    """Поддерживаемые валюты поставщиков."""
    RUB = "RUB"
    USD = "USD"
    EUR = "EUR"
    CNY = "CNY"


class UserRole(str, enum.Enum):
    """Роли пользователей: admin — всё, manager — только поиск, pending — ждёт одобрения."""
    admin = "admin"
    manager = "manager"
    pending = "pending"


class UploadStatus(str, enum.Enum):
    """Статус обработки загруженного прайса."""
    pending = "pending"
    done = "done"
    failed = "failed"


# --- Модели ----------------------------------------------------------------


class Supplier(Base):
    """Поставщик автозапчастей."""
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Валюта прайса поставщика (в ней приходят исходные цены).
    currency: Mapped[Currency] = mapped_column(
        SAEnum(Currency, name="currency_enum"), nullable=False
    )
    contact: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Связи. Каскад удаления на уровне ORM — при удалении поставщика чистим зависимые записи.
    column_mapping: Mapped["ColumnMapping | None"] = relationship(
        back_populates="supplier",
        cascade="all, delete-orphan",
        uselist=False,
    )
    uploads: Mapped[list["PriceUpload"]] = relationship(
        back_populates="supplier",
        cascade="all, delete-orphan",
    )
    offers: Mapped[list["Offer"]] = relationship(
        back_populates="supplier",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # для удобной отладки/логов
        return f"<Supplier id={self.id} name={self.name!r} currency={self.currency.value}>"


class ColumnMapping(Base):
    """Маппинг колонок конкретного поставщика на наши поля.

    Разные поставщики называют колонки по-разному (Артикул vs OEM и т.п.),
    поэтому храним соответствие «наше поле -> имя колонки в файле поставщика».
    """
    __tablename__ = "column_mappings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    supplier_id: Mapped[int] = mapped_column(
        ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False
    )
    oem_col: Mapped[str] = mapped_column(String(255), nullable=False)
    brand_col: Mapped[str] = mapped_column(String(255), nullable=False)
    name_col: Mapped[str] = mapped_column(String(255), nullable=False)
    price_col: Mapped[str] = mapped_column(String(255), nullable=False)
    qty_col: Mapped[str] = mapped_column(String(255), nullable=False)

    supplier: Mapped["Supplier"] = relationship(back_populates="column_mapping")

    def __repr__(self) -> str:
        return f"<ColumnMapping id={self.id} supplier_id={self.supplier_id}>"


class PriceUpload(Base):
    """Факт загрузки прайс-листа поставщика и статистика по строкам."""
    __tablename__ = "price_uploads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    supplier_id: Mapped[int] = mapped_column(
        ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    # Время загрузки проставляется на стороне БД (func.now()) — единый источник времени.
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    rows_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_ok: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[UploadStatus] = mapped_column(
        SAEnum(UploadStatus, name="upload_status_enum"),
        nullable=False,
        default=UploadStatus.pending,
    )

    supplier: Mapped["Supplier"] = relationship(back_populates="uploads")
    offers: Mapped[list["Offer"]] = relationship(back_populates="upload")

    def __repr__(self) -> str:
        return f"<PriceUpload id={self.id} file={self.filename!r} status={self.status.value}>"


class Part(Base):
    """Нормализованная запчасть (уникальна по паре OEM+бренд)."""
    __tablename__ = "parts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Нормализованный OEM (без пробелов/дефисов, в верхнем регистре) — по нему ищем.
    oem_normalized: Mapped[str] = mapped_column(String(128), nullable=False)
    # Исходный OEM как в прайсе — оставляем для отображения/аудита.
    oem_raw: Mapped[str] = mapped_column(String(128), nullable=False)
    brand: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)

    offers: Mapped[list["Offer"]] = relationship(
        back_populates="part",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        # Одна запчасть = один нормализованный OEM + бренд (иначе задублируем номенклатуру).
        UniqueConstraint("oem_normalized", "brand", name="uq_parts_oem_brand"),
        # Индекс для быстрого поиска по нормализованному артикулу.
        Index("ix_parts_oem_normalized", "oem_normalized"),
    )

    def __repr__(self) -> str:
        return f"<Part id={self.id} oem={self.oem_normalized!r} brand={self.brand!r}>"


class Offer(Base):
    """Предложение цены конкретного поставщика на конкретную запчасть.

    Уникальность (part_id, supplier_id): у одного поставщика на одну запчасть —
    одно актуальное предложение. Новая загрузка ОБНОВЛЯЕТ строку, а не плодит дубли.
    """
    __tablename__ = "offers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    part_id: Mapped[int] = mapped_column(
        ForeignKey("parts.id", ondelete="CASCADE"), nullable=False
    )
    supplier_id: Mapped[int] = mapped_column(
        ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False
    )
    # Загрузка, в рамках которой предложение обновлено в последний раз.
    upload_id: Mapped[int | None] = mapped_column(
        ForeignKey("price_uploads.id", ondelete="SET NULL"), nullable=True
    )
    # Цена в рублях после конвертации по курсу — по ней сравниваем поставщиков.
    price_rub: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    # Исходная цена в валюте поставщика — храним для прозрачности.
    price_original: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency: Mapped[Currency] = mapped_column(
        SAEnum(Currency, name="currency_enum"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Время последнего обновления предложения: ставится при вставке и меняется при апдейте.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    part: Mapped["Part"] = relationship(back_populates="offers")
    supplier: Mapped["Supplier"] = relationship(back_populates="offers")
    upload: Mapped["PriceUpload | None"] = relationship(back_populates="offers")

    __table_args__ = (
        UniqueConstraint("part_id", "supplier_id", name="uq_offers_part_supplier"),
    )

    def __repr__(self) -> str:
        return (
            f"<Offer id={self.id} part_id={self.part_id} "
            f"supplier_id={self.supplier_id} price_rub={self.price_rub}>"
        )


class User(Base):
    """Пользователь Telegram-бота с ролью доступа."""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # telegram_id может быть большим числом — берём BigInteger. Уникален и проиндексирован.
    telegram_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, unique=True, index=True
    )
    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, name="user_role_enum"),
        nullable=False,
        default=UserRole.pending,
    )
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} tg={self.telegram_id} role={self.role.value}>"


class ExchangeRate(Base):
    """Кэш курса валюты к рублю (обновляется не чаще раза в 24 часа)."""
    __tablename__ = "exchange_rates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    currency: Mapped[Currency] = mapped_column(
        SAEnum(Currency, name="currency_enum"), nullable=False
    )
    # Курс = сколько рублей за 1 единицу валюты. Decimal, т.к. это деньги.
    rate: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<ExchangeRate {self.currency.value}={self.rate} at={self.fetched_at}>"
