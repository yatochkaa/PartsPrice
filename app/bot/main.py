"""Точка входа Telegram-бота (aiogram 3, long polling).

Запуск: python -m app.bot.main

Собираем Bot и Dispatcher, вешаем middleware (сначала сессия БД, потом роль),
подключаем роутеры и запускаем polling.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.bot.handlers import access, admin, start
from app.bot.middlewares import DbSessionMiddleware, UserRoleMiddleware
from app.core.config import settings
from app.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    """Настраивает базовое логирование по уровню из .env (LOG_LEVEL)."""
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _build_dispatcher() -> Dispatcher:
    """Собирает Dispatcher: middleware + роутеры."""
    dp = Dispatcher()

    # Middleware на весь поток апдейтов. Порядок важен:
    # сначала сессия БД (кладёт session в data), потом поиск пользователя (читает session).
    dp.update.outer_middleware(DbSessionMiddleware(AsyncSessionLocal))
    dp.update.outer_middleware(UserRoleMiddleware())

    # Роутеры по областям ответственности.
    dp.include_router(start.router)
    dp.include_router(access.router)
    dp.include_router(admin.router)

    return dp


async def main() -> None:
    """Запускает бота в режиме long polling."""
    _setup_logging()

    # Без токена бот запустить нельзя — падаем с понятным сообщением.
    if not settings.BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN не задан в .env — укажите токен от @BotFather."
        )

    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = _build_dispatcher()

    logger.info("Бот запущен, начинаю polling…")
    # Сбрасываем накопившиеся апдейты, чтобы не обрабатывать старые сообщения.
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot)
    finally:
        # Корректно закрываем HTTP-сессию бота.
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
