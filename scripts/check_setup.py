"""Проверка этапа 0: конфиг читается, логгер пишет в консоль и файл."""

import logging

from app.core.config import get_settings
from app.core.logging import setup_logging


def main() -> None:
    # Читаем настройки из .env — если чего-то не хватает,
    # pydantic упадёт здесь с понятным сообщением
    settings = get_settings()

    # Настраиваем логирование уровнем из конфига
    setup_logging(settings.log_level)
    logger = logging.getLogger("check_setup")

    # Секреты не логируем целиком — только факт наличия и безопасные поля
    logger.info("Конфиг загружен успешно")
    logger.info("DATABASE_URL: %s", settings.database_url)
    logger.info("LOG_LEVEL: %s", settings.log_level)
    logger.info("BOT_TOKEN задан: %s", bool(settings.bot_token))
    logger.info("API_SECRET задан: %s", bool(settings.api_secret))
    logger.info("ADMIN_TELEGRAM_ID: %d", settings.admin_telegram_id)
    logger.debug("Это debug-сообщение (видно только при LOG_LEVEL=DEBUG)")


if __name__ == "__main__":
    main()
