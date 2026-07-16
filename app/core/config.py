"""Конфигурация приложения.

Настройки читаются из переменных окружения и файла .env
с помощью pydantic-settings. Доступ к настройкам — только
через get_settings(), чтобы конфиг создавался один раз.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
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
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Возвращает единственный экземпляр Settings.

    lru_cache гарантирует, что .env и окружение читаются один раз,
    а все части приложения (API, бот, сервисы) получают один и тот же
    объект настроек. Это же удобно в тестах: cache_clear() позволяет
    пересоздать настройки с другим окружением.
    """
    return Settings()
