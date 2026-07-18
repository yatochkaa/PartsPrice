"""Смоук-тест на конфиг и scripts/check_setup после починки.

Регрессия на баг: раньше scripts/check_setup.py импортировал несуществующий
`get_settings` и обращался к полям в нижнем регистре (settings.log_level и т.п.),
которых нет в Settings (там поля в ВЕРХНЕМ регистре). Оба случая ловим здесь.
"""
from __future__ import annotations

import importlib


def test_config_exposes_uppercase_fields() -> None:
    """Единый объект settings с полями в ВЕРХНЕМ регистре доступен."""
    from app.core.config import settings

    for field in (
        "BOT_TOKEN",
        "DATABASE_URL",
        "API_SECRET",
        "ADMIN_TELEGRAM_ID",
        "LOG_LEVEL",
    ):
        assert hasattr(settings, field), f"Отсутствует поле {field}"


def test_config_has_optional_admin_secret() -> None:
    """ADMIN_API_SECRET — необязательное поле, по умолчанию пустое (строка)."""
    from app.core.config import settings

    assert hasattr(settings, "ADMIN_API_SECRET")
    assert isinstance(settings.ADMIN_API_SECRET, str)


def test_check_setup_imports_cleanly() -> None:
    """Модуль scripts.check_setup импортируется без ошибок (есть main())."""
    module = importlib.import_module("scripts.check_setup")
    assert hasattr(module, "main")
