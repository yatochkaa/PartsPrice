"""Интеграционные тесты REST API (TestClient + in-memory SQLite).

База подменяется через dependency_overrides[get_session]: все запросы
работают с одной in-memory SQLite на StaticPool (общее соединение,
данные живут между запросами в пределах одного теста).

Авторизацию НЕ подменяем — проверяем настоящую логику deps.py,
подставив известный API_SECRET через monkeypatch.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.api import deps
from app.api.main import app
from app.core.config import settings
from app.db.models import Base
from app.db.session import get_session

# Известный ключ для тестов и готовые наборы заголовков.
TEST_API_SECRET = "test-secret-key"
ADMIN_HEADERS = {"X-API-Key": TEST_API_SECRET, "X-Role": "admin"}
MANAGER_HEADERS = {"X-API-Key": TEST_API_SECRET}

# Путь к примеру прайса (относительно корня проекта).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_CSV = PROJECT_ROOT / "sample_data" / "supplier_a.csv"

# Столбцы в supplier_a.csv: Артикул;Бренд;Наименование;Цена;Остаток
MAPPING_PAYLOAD = {
    "oem_col": "Артикул",
    "brand_col": "Бренд",
    "name_col": "Наименование",
    "price_col": "Цена",
    "qty_col": "Остаток",
}


@pytest.fixture
def session_factory() -> Iterator[async_sessionmaker[AsyncSession]]:
    """Создаёт in-memory БД со схемой и возвращает фабрику сессий.

    StaticPool + единая ссылка на :memory: -> все сессии видят одни и те же данные.
    Фикстура синхронная (под TestClient), поэтому setup/teardown БД гоним
    в отдельном event loop, созданном явно (без устаревшего get_event_loop()).
    """
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    async def _create_all() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    # Явный event loop для setup/teardown БД (в синхронной фикстуре нет running loop).
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_create_all())
        factory = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        yield factory
    finally:
        loop.run_until_complete(engine.dispose())
        loop.close()


@pytest.fixture
def client(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    """TestClient с подменой сессии БД и известным API_SECRET."""
    # Подставляем секрет в единый объект settings (deps читает его в момент вызова).
    monkeypatch.setattr(settings, "API_SECRET", TEST_API_SECRET)
    monkeypatch.setattr(deps.settings, "API_SECRET", TEST_API_SECRET)

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def test_health_no_auth(client: TestClient) -> None:
    """health доступен без ключа."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_suppliers_requires_key(client: TestClient) -> None:
    """Без X-API-Key -> 401."""
    response = client.post("/suppliers", json={"name": "X", "currency": "RUB"})
    assert response.status_code == 401


def test_suppliers_requires_admin_role(client: TestClient) -> None:
    """Валидный ключ, но без роли admin -> 403."""
    response = client.post(
        "/suppliers",
        json={"name": "X", "currency": "RUB"},
        headers=MANAGER_HEADERS,
    )
    assert response.status_code == 403


def test_full_cycle_supplier_mapping_upload_search(client: TestClient) -> None:
    """Полный цикл: поставщик -> маппинг -> загрузка -> поиск находит деталь."""
    # 1) создаём поставщика (валюта RUB -> курс не требует сети).
    create = client.post(
        "/suppliers",
        json={"name": "Поставщик A", "currency": "RUB"},
        headers=ADMIN_HEADERS,
    )
    assert create.status_code == 201, create.text
    supplier_id = create.json()["id"]

    # 2) задаём маппинг колонок.
    mapping = client.post(
        f"/suppliers/{supplier_id}/mapping",
        json=MAPPING_PAYLOAD,
        headers=ADMIN_HEADERS,
    )
    assert mapping.status_code == 201, mapping.text

    # 3) загружаем реальный прайс supplier_a.csv.
    with open(SAMPLE_CSV, "rb") as fh:
        upload = client.post(
            "/uploads",
            data={"supplier_id": str(supplier_id)},
            files={"file": ("supplier_a.csv", fh, "text/csv")},
            headers=ADMIN_HEADERS,
        )
    assert upload.status_code == 201, upload.text
    report = upload.json()
    assert report["status"] == "done"
    assert report["rows_ok"] > 0
    # Фикс имени: в отчёте должно быть исходное имя, а не tmpXXXX.csv.
    assert report["filename"] == "supplier_a.csv"
    upload_id = report["upload_id"]

    # 3а) GET /uploads/{id} возвращает ту же сводку с тем же именем.
    got = client.get(f"/uploads/{upload_id}", headers=ADMIN_HEADERS)
    assert got.status_code == 200, got.text
    assert got.json()["rows_ok"] == report["rows_ok"]
    assert got.json()["filename"] == "supplier_a.csv"

    # 4) поиск по OEM находит деталь (роль manager достаточна).
    search = client.get(
        "/search", params={"oem": "W712/75"}, headers=MANAGER_HEADERS
    )
    assert search.status_code == 200, search.text
    results = search.json()
    assert len(results) == 1
    assert results[0]["oem_normalized"] == "W71275"
    assert len(results[0]["offers"]) >= 1


def test_upload_rejects_txt(client: TestClient) -> None:
    """Загрузка .txt -> 400."""
    response = client.post(
        "/uploads",
        data={"supplier_id": "1"},
        files={"file": ("prices.txt", b"some text", "text/plain")},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 400


def test_search_without_params_returns_422(client: TestClient) -> None:
    """Поиск без параметров -> 422."""
    response = client.get("/search", headers=MANAGER_HEADERS)
    assert response.status_code == 422
