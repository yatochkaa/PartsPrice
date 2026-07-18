"""Роутер загрузки прайс-листов поставщиков.

Доступ: только admin (require_admin) — загрузка данных изменяет БД.
"""
from __future__ import annotations

import logging
import os
import tempfile

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.api.schemas import UploadReportOut
from app.db.models import PriceUpload
from app.db.session import get_session
from app.services.importer import ImporterError, import_price_file

logger = logging.getLogger(__name__)

# Ограничение размера файла: 10 МБ.
MAX_FILE_SIZE = 10 * 1024 * 1024
# Разрешённые расширения.
ALLOWED_SUFFIXES = {".csv", ".xlsx"}

# Весь роутер — только для admin.
router = APIRouter(
    prefix="/uploads",
    tags=["uploads"],
    dependencies=[Depends(require_admin)],
)


def _safe_filename(filename: str | None, ext: str) -> str:
    """Возвращает безопасное имя файла без пути (защита от path traversal).

    os.path.basename убирает любые «../» и абсолютные пути. Если имя пустое —
    подставляем запасное с правильным расширением.
    """
    base = os.path.basename(filename or "").strip()
    return base or f"upload{ext}"


def _validate_suffix(filename: str | None) -> str:
    """Проверяет расширение файла и возвращает его в нижнем регистре."""
    name = filename or ""
    _, ext = os.path.splitext(name)
    ext = ext.lower()
    if ext not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Недопустимый формат файла {ext!r}; разрешены .csv и .xlsx",
        )
    return ext


@router.post("", response_model=UploadReportOut, status_code=status.HTTP_201_CREATED)
async def upload_price_file(
    supplier_id: int = Form(..., description="ID поставщика"),
    file: UploadFile = File(..., description="Прайс-лист .csv или .xlsx"),
    session: AsyncSession = Depends(get_session),
) -> UploadReportOut:
    """Загружает прайс поставщика и запускает импорт.

    Алгоритм:
    1. проверяем расширение (.csv/.xlsx);
    2. по возможности отклоняем слишком большой файл ДО чтения (по file.size);
    3. читаем тело с повторной проверкой лимита 10 МБ (defence in depth);
    4. кладём во временную папку ПОД ИСХОДНЫМ ИМЕНЕМ файла,
       чтобы в отчёте и в price_uploads сохранилось реальное имя (напр. supplier_a.csv);
    5. вызываем import_price_file и возвращаем отчёт;
    6. временная папка удаляется автоматически при выходе из with.
    """
    ext = _validate_suffix(file.filename)

    # Ранняя проверка размера: Starlette проставляет file.size из заголовков
    # multipart, поэтому очевидно большой файл можно отклонить, не читая его
    # целиком в память. Это дешёвая защита от OOM на явно больших загрузках.
    if file.size is not None and file.size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Файл больше лимита {MAX_FILE_SIZE // (1024 * 1024)} МБ",
        )

    # Читаем тело в память. После ранней проверки здесь ожидается файл в пределах
    # лимита; повторную проверку оставляем на случай, если size не был известен.
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Файл больше лимита {MAX_FILE_SIZE // (1024 * 1024)} МБ",
        )
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Пустой файл",
        )

    # Временная ПАПКА + файл с исходным именем: importer берёт filename из path.name,
    # поэтому в отчёте будет реальное имя, а не tmpXXXX.csv.
    safe_name = _safe_filename(file.filename, ext)
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = os.path.join(tmp_dir, safe_name)
        with open(tmp_path, "wb") as tmp_file:
            tmp_file.write(content)

        try:
            report = await import_price_file(session, supplier_id, tmp_path)
        except ImporterError as exc:
            # Ошибка бизнес-логики импорта (нет поставщика/маппинга, битый файл) -> 400.
            logger.warning("Импорт не удался: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            )

    return UploadReportOut.model_validate(report)


@router.get("/{upload_id}", response_model=UploadReportOut)
async def get_upload(
    upload_id: int,
    session: AsyncSession = Depends(get_session),
) -> UploadReportOut:
    """Возвращает сводку по ранее выполненной загрузке.

    Список ошибок строк в БД не хранится (он есть только в ответе POST /uploads),
    поэтому здесь errors всегда пустой — возвращаем только статистику.
    """
    upload = await session.get(PriceUpload, upload_id)
    if upload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Загрузка id={upload_id} не найдена",
        )
    return UploadReportOut(
        supplier_id=upload.supplier_id,
        filename=upload.filename,
        upload_id=upload.id,
        status=upload.status,
        rows_total=upload.rows_total,
        rows_ok=upload.rows_ok,
        rows_failed=upload.rows_failed,
        errors=[],
    )
