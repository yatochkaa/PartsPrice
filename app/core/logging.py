"""Настройка логирования проекта.

Формат: "время | уровень | модуль | сообщение".
Вывод — в консоль и в файл logs/app.log с ротацией
(RotatingFileHandler: 5 МБ на файл, 3 бэкапа).
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Единый формат для консоли и файла
LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Директория и файл логов
LOG_DIR = Path("logs")
LOG_FILE = LOG_DIR / "app.log"

# 5 МБ на файл, 3 бэкапа (app.log.1, app.log.2, app.log.3)
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3


def setup_logging(level: str = "INFO") -> None:
    """Настраивает корневой логгер приложения.

    :param level: уровень логирования строкой (DEBUG/INFO/WARNING/ERROR).
                  Неизвестное значение не роняет приложение —
                  используется INFO с предупреждением в лог.
    """
    # Преобразуем строку в числовой уровень.
    # getattr вернёт None для мусорного значения — обрабатываем это сами,
    # чтобы опечатка в .env не валила весь сервис на старте.
    numeric_level = getattr(logging, level.upper(), None)
    invalid_level = not isinstance(numeric_level, int)
    if invalid_level:
        numeric_level = logging.INFO

    # Создаём папку под логи заранее: RotatingFileHandler сам её не создаст
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # Файловый лог недоступен (например, нет прав) — работаем
        # только с консолью, но приложение не падает.
        print(f"Не удалось создать директорию логов {LOG_DIR}: {exc}", file=sys.stderr)

    formatter = logging.Formatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT)

    # Консольный обработчик
    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_handler.setFormatter(formatter)

    handlers: list[logging.Handler] = [console_handler]

    # Файловый обработчик с ротацией
    try:
        file_handler = RotatingFileHandler(
            filename=LOG_FILE,
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)
    except OSError as exc:
        print(f"Не удалось открыть файл логов {LOG_FILE}: {exc}", file=sys.stderr)

    # Настраиваем корневой логгер. force=True снимает ранее навешанные
    # обработчики — иначе при повторном вызове setup_logging (например,
    # в тестах) каждое сообщение дублировалось бы.
    logging.basicConfig(level=numeric_level, handlers=handlers, force=True)

    logger = logging.getLogger(__name__)
    if invalid_level:
        logger.warning("Неизвестный уровень логирования %r, используется INFO", level)
    logger.info("Логирование настроено: уровень %s", logging.getLevelName(numeric_level))
