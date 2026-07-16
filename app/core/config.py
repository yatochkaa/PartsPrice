<<<<<<< HEAD
"""Настройки приложения.

Читаем переменные окружения через pydantic-settings.
Здесь только те поля, что перечислены в ТЕХСПЕЦ (.env), без лишнего.
"""
from __future__ import annotations
=======
"""Конфигурация приложения.

Настройки читаются из переменных окружения и файла .env
с помощью pydantic-settings. Доступ к настройкам — только
через get_settings(), чтобы конфиг создавался один раз.
"""

from functools import lru_cache
>>>>>>> 08d8de0f238554a50b3b8963ed1e8c6a0f0f996d

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
<<<<<<< HEAD
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
=======
    """Все настройки проекта из ТЕХСПЕЦ.

    Поля обязательные (кроме LOG_LEVEL с дефолтом): если чего-то нет
    в окружении или .env — приложение упадёт на старте с понятной
    ошибкой валидации, а не в рантайме посреди работы.
    """

    # Токен Telegram-бота (выдаёт @BotFather)
    bot_token: str

    # Строка подключения к БД. По умолчанию проект работает на SQLite,
    # но формат URL позволяет переключиться на PostgreSQL (asyncpg)
    # без изменения кода — только заменой значения в .env.
    database_url: str

    # Секрет для доступа к REST API
    api_secret: str

    # Telegram ID администратора — этому пользователю сразу даётся роль admin
    admin_telegram_id: int

    # Уровень логирования (DEBUG/INFO/WARNING/ERROR)
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        # Читаем .env из корня проекта
        env_file=".env",
        env_file_encoding="utf-8",
        # BOT_TOKEN в .env соответствует полю bot_token —
        # pydantic-settings сопоставляет имена без учёта регистра
        case_sensitive=False,
        # Лишние переменные в окружении не должны ломать запуск
>>>>>>> 08d8de0f238554a50b3b8963ed1e8c6a0f0f996d
        extra="ignore",
    )


<<<<<<< HEAD
# Единый экземпляр настроек на всё приложение (импортируется где нужно).
settings = Settings()
=======
@lru_cache
def get_settings() -> Settings:
    """Возвращает единственный экземпляр Settings.

    lru_cache гарантирует, что .env и окружение читаются один раз,
    а все части приложения (API, бот, сервисы) получают один и тот же
    объект настроек. Это же удобно в тестах: cache_clear() позволяет
    пересоздать настройки с другим окружением.
    """
    return Settings()
>>>>>>> 08d8de0f238554a50b3b8963ed1e8c6a0f0f996d
