"""Проверка этапа 0: конфиг читается, логгер пишет в консоль и файл.

Настройки читаются один раз при импорте app.core.config в виде единого
объекта `settings` с полями в ВЕРХНЕМ регистре (BOT_TOKEN, DATABASE_URL,
API_SECRET, ADMIN_TELEGRAM_ID, LOG_LEVEL). Здесь мы только подтверждаем, что
конфиг загрузился, и печатаем безопасные поля.
"""

import logging

from app.core.config import settings
from app.core.logging import setup_logging


def main() -> None:
    # settings уже собран pydantic-ом на импорте: если чего-то не хватает или
    # значение некорректно (например, ADMIN_TELEGRAM_ID не число), падение
    # произойдёт раньше — с понятным сообщением от pydantic.

    # Настраиваем логирование уровнем из конфига.
    setup_logging(settings.LOG_LEVEL)
    logger = logging.getLogger("check_setup")

    # Секреты не логируем целиком — только факт наличия и безопасные поля.
    logger.info("Конфиг загружен успешно")
    logger.info("DATABASE_URL: %s", settings.DATABASE_URL)
    logger.info("LOG_LEVEL: %s", settings.LOG_LEVEL)
    logger.info("BOT_TOKEN задан: %s", bool(settings.BOT_TOKEN))
    logger.info("API_SECRET задан: %s", bool(settings.API_SECRET))
    # ADMIN_API_SECRET — необязательное поле; используем getattr на случай,
    # если этот фикс применяют без обновлённого app/core/config.py.
    logger.info(
        "ADMIN_API_SECRET задан: %s",
        bool(getattr(settings, "ADMIN_API_SECRET", "")),
    )
    logger.info("ADMIN_TELEGRAM_ID: %d", settings.ADMIN_TELEGRAM_ID)
    logger.debug("Это debug-сообщение (видно только при LOG_LEVEL=DEBUG)")


if __name__ == "__main__":
    main()
