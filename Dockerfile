# Dockerfile — единый образ для API и бота PartsPrice Hub.
# Какой именно процесс запускать — решает docker-compose через command,
# поэтому образ один, а сервисов два.

# slim — тот же Python 3.12, но без лишних пакетов ОС: образ в разы меньше.
FROM python:3.12-slim

# PYTHONUNBUFFERED — логи сразу попадают в stdout (видны в docker logs без задержки);
# PYTHONDONTWRITEBYTECODE — не мусорить .pyc-файлами внутри контейнера;
# PIP_NO_CACHE_DIR / PIP_DISABLE_PIP_VERSION_CHECK — не хранить кэш pip в образе.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Рабочая директория приложения внутри контейнера.
WORKDIR /app

# Сначала копируем ТОЛЬКО requirements.txt: пока этот файл не меняется,
# Docker переиспользует кэшированный слой с уже установленными зависимостями,
# и обычные правки кода НЕ запускают переустановку pandas и остальных пакетов.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Теперь код и миграции — они меняются часто, поэтому идут ПОСЛЕ слоя зависимостей.
COPY alembic.ini ./
COPY alembic/ alembic/
COPY app/ app/
COPY scripts/ scripts/
COPY sample_data/ sample_data/

# Non-root пользователь: если приложение взломают, у процесса не будет прав root.
# /app/data — под файл SQLite (том), /app/logs — под логи (том).
# Папки создаём заранее и отдаём пользователю: именованные тома Docker при первом
# запуске копируют владельца из образа — иначе тома были бы root-only и запись упала бы.
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/data /app/logs \
    && chown -R appuser:appuser /app
USER appuser

# Порт API (документация; публикацию наружу делает compose).
EXPOSE 8000

# Команда по умолчанию — API; в docker-compose переопределяется для каждого сервиса.
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
