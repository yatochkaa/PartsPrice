# PartsPrice Hub — этап 0 (каркас проекта)

Агрегатор прайс-листов поставщиков автозапчастей.
CSV/Excel → нормализация OEM/брендов/цен → БД → поиск через Telegram-бот и REST API.

## Как запустить и проверить

```bash
# Виртуальное окружение на Python 3.12
python3.12 -m venv venv

# Активация: Linux/macOS
source venv/bin/activate
# Активация: Windows (PowerShell)
# venv\Scripts\Activate.ps1

# Зависимости
pip install --upgrade pip
pip install -r requirements.txt

# Локальный .env из примера (заполнить своими значениями)
cp .env.example .env

# Проверка конфига и логгера
python -m scripts.check_setup

# Убедиться, что файл лога создан и заполнен
cat logs/app.log
```

Ожидаемый результат: в консоли и в `logs/app.log` строки вида
`2026-07-16 19:55:01 | INFO | check_setup | Конфиг загружен успешно`.

## Структура (этап 0)

```
partsprice-hub/
├── .gitignore
├── .env.example
├── requirements.txt
├── app/
│   ├── __init__.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py      # Settings на pydantic-settings + get_settings()
│   │   └── logging.py     # setup_logging(): консоль + logs/app.log с ротацией
│   ├── db/__init__.py
│   ├── services/__init__.py
│   ├── api/
│   │   ├── __init__.py
│   │   └── routers/__init__.py
│   └── bot/
│       ├── __init__.py
│       └── handlers/__init__.py
├── tests/__init__.py
├── scripts/
│   ├── __init__.py
│   └── check_setup.py     # мини-скрипт проверки конфига и логгера
├── sample_data/
└── alembic/
```

## Ключевые решения простыми словами

- **`get_settings()` с `lru_cache`** — конфиг читается из `.env` один раз и переиспользуется всеми частями приложения (API, бот, сервисы). В тестах легко подменять настройки через `get_settings.cache_clear()`.
- **Обязательные поля в `Settings`** — если забыли задать `BOT_TOKEN` или `API_SECRET`, приложение упадёт сразу на старте с понятной ошибкой, а не молча сломается позже.
- **`DATABASE_URL` строкой** — переход с SQLite на PostgreSQL сводится к замене одной строки в `.env`, код трогать не нужно.
- **`force=True` в `basicConfig`** — защита от дублирования логов при повторной настройке (актуально для тестов и перезапусков).
- **Логгер не роняет приложение** — если папку/файл логов создать нельзя, пишем предупреждение в stderr и продолжаем работать только с консолью.
- **Секреты в проверочном скрипте не печатаются** — только факт их наличия, чтобы токены не утекали в логи.
