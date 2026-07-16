"""Роутер поиска деталей: по OEM или по подстроке названия.

Доступ: любой валидный ключ (require_manager) — поиск разрешён и менеджерам, и админам.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_manager
from app.api.schemas import SearchResultOut
from app.db.session import get_session
from app.services.search import PartSearchResult, find_by_name, find_by_oem

logger = logging.getLogger(__name__)

# Весь роутер требует минимум роль manager (валидный ключ).
router = APIRouter(
    tags=["search"],
    dependencies=[Depends(require_manager)],
)


@router.get("/search", response_model=list[SearchResultOut])
async def search_parts(
    oem: str | None = Query(default=None, description="Поиск по артикулу (OEM)"),
    q: str | None = Query(default=None, description="Поиск по подстроке названия"),
    session: AsyncSession = Depends(get_session),
) -> list[PartSearchResult]:
    """Ищет детали с предложениями. Если задан oem — ищем по нему, иначе по q. Оба пусты -> 422."""
    oem_text = (oem or "").strip()
    q_text = (q or "").strip()

    if not oem_text and not q_text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Укажите параметр oem или q",
        )

    if oem_text:
        results = await find_by_oem(session, oem_text)
        logger.debug("GET /search oem=%r: %d результатов", oem_text, len(results))
        return results

    results = await find_by_name(session, q_text)
    logger.debug("GET /search q=%r: %d результатов", q_text, len(results))
    return results
