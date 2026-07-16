"""Настройки приложения.

Читаем переменные окружения через pydantic-settings.
Здесь только те поля, что перечислены в ТЕХСПЕЦ (.env), без лишнего.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Токен Telegram-бота (на этапе 1 не используется, но объявлен в .env по ТЕХСПЕЦ).
    BOT_TOKEN: str = ""
    # Строка подключения к БД. По умолчанию — локальный SQLite на асинхронном драйвере aiosqlite.
    # Для PostgreSQL достаточно подставить postgresql+asyncpg://... — код менять не нужно.
    DATABASE_URL: str = "sqlite+aiosqlite:///./partsprice.db"
    # Секрет для REST API (используется на более поздних этапах).
    API_SECRET: str = ""
    # Telegram ID администратора — из него seed создаёт пользователя-админа.
    ADMIN_TELEGRAM_ID: int = 0
    # Уровень логирования.
    LOG_LEVEL: str = "INFO"

    # Читаем .env, лишние переменные окружения игнорируем, чтобы не падать.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


# Единый экземпляр настроек на всё приложение (импортируется где нужно).
settings = Settings()
