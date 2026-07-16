# PartsPrice Hub — этап 1 (база данных)

## Куда класть файлы

Распакуйте архив в корень вашего репозитория PartsPrice. Структура:

```
PartsPrice/
├─ alembic.ini                  # конфиг Alembic (в корне)
├─ .env.example                 # шаблон .env — скопируйте в .env
├─ requirements.txt
├─ README_STAGE1.md             # этот файл
├─ alembic/
│  ├─ env.py                    # асинхронный env (готовый)
│  ├─ script.py.mako            # шаблон миграций
│  └─ versions/                 # сюда лягут автогенерированные миграции
└─ app/
   ├─ __init__.py
   ├─ core/
   │  ├─ __init__.py
   │  └─ config.py              # настройки из .env
   └─ db/
      ├─ __init__.py
      ├─ models.py              # все таблицы
      └─ session.py             # асинхронный engine + get_session
   scripts/
   ├─ __init__.py
   └─ seed.py                    # админ + 3 поставщика
```

> Важно: папка `scripts/` лежит в корне репозитория, рядом с `app/`, а не внутри `app/`.

## Почему могло «сломаться»

Самые частые причины на этом этапе:
1. **Нет `__init__.py`** — тогда `import app...` падает с ModuleNotFoundError. В архиве они уже есть.
2. **Alembic не видит `app`** — решено в `alembic/env.py` через добавление корня в sys.path.
3. **URL базы не подхватывается** — env.py берёт его из настроек (.env), а не из alembic.ini.
4. **Синхронный env.py от `alembic init`** — заменён на асинхронный.

## Запуск

```bash
# 1. окружение и зависимости
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. настройки
cp .env.example .env    # затем впишите ADMIN_TELEGRAM_ID

# 3. первая миграция (autogenerate по моделям)
alembic revision --autogenerate -m "initial schema"

# 4. применить
alembic upgrade head

# 5. seed (админ + 3 поставщика)
python -m scripts.seed

# 6. проверка таблиц
sqlite3 partsprice.db ".tables"
sqlite3 partsprice.db "SELECT name, currency FROM suppliers;"
sqlite3 partsprice.db "SELECT telegram_id, role FROM users;"
```

Ожидаемые таблицы: suppliers, column_mappings, price_uploads, parts, offers, users, exchange_rates (+ alembic_version).

Команды запускайте из корня репозитория (там, где лежит alembic.ini).
