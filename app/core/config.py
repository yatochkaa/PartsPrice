"""Настройки приложения.

Читаем переменные окружения и файл .env через pydantic-settings.
Поля из ТЕХСПЕЦ (.env): BOT_TOKEN, DATABASE_URL, API_SECRET,
ADMIN_TELEGRAM_ID, LOG_LEVEL, а также необязательный ADMIN_API_SECRET.

ВАЖНО: именно этот вариант (единый объект `settings` с полями в верхнем
регистре) импортируют app/db/session.py и app/api/main.py — менять имя
`settings` или регистр полей нельзя без правки этих файлов.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Все настройки проекта из ТЕХСПЕЦ."""

    # Токен Telegram-бота (выдаёт @BotFather). На ранних этапах не используется,
    # поэтому допускаем пустое значение — API должен запускаться без бота.
    BOT_TOKEN: str = ""

    # Строка подключения к БД. По умолчанию — локальный SQLite на асинхронном
    # драйвере aiosqlite. Для PostgreSQL достаточно подставить в .env
    # postgresql+asyncpg://... — код менять не нужно.
    DATABASE_URL: str = "sqlite+aiosqlite:///./partsprice.db"

    # Секрет для доступа к REST API (используется на этапе авторизации в deps.py).
    API_SECRET: str = ""

    # (Необязательно) Отдельный секрет для админских операций.
    # Если задан — админом считается только владелец этого ключа, а связка
    # «общий API_SECRET + X-Role: admin» админ-доступа больше не даёт.
    # Если пусто — сохраняется прежнее (упрощённое) поведение с X-Role.
    ADMIN_API_SECRET: str = ""

    # Telegram ID администратора — из него seed создаёт пользователя-админа.
    ADMIN_TELEGRAM_ID: int = 0

    # Уровень логирования (DEBUG/INFO/WARNING/ERROR).
    LOG_LEVEL: str = "INFO"

    model_config = SettingsConfigDict(
        # Читаем .env из корня проекта.
        env_file=".env",
        env_file_encoding="utf-8",
        # Имена переменных сопоставляются без учёта регистра.
        case_sensitive=False,
        # Лишние переменные окружения не должны ронять запуск.
        extra="ignore",
    )


# Единый экземпляр настроек на всё приложение:
# его импортируют session.py, api/main.py и другие модули.
settings = Settings()
