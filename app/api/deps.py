"""Зависимости авторизации для REST API.

Простая схема на основе статичного ключа в заголовке X-API-Key:
- require_manager — любой валидный ключ (доступ только на поиск);
- require_admin — валидный ключ + заголовок X-Role: admin (полный доступ).

Ключ читается через штатную схему APIKeyHeader из fastapi.security — благодаря этому
в Swagger (/docs) появляется кнопка Authorize (замочек), а сама авторизация
отображается как схема безопасности, а не как обычный параметр.
auto_error=False: ошибку 401 формируем сами (с понятным русским сообщением).

Сравнение ключа — через secrets.compare_digest (сравнение за постоянное время,
защита от timing-атак: по времени ответа нельзя побайтово подобрать ключ).
"""
from __future__ import annotations

import logging
import secrets

from fastapi import Header, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.core.config import settings

logger = logging.getLogger(__name__)

# Заголовок с ролью, дающей админский доступ.
_ADMIN_ROLE = "admin"

# Штатная схема безопасности: ключ в заголовке X-API-Key.
# auto_error=False — если заголовка нет, FastAPI не бросает свою 403, а отдаёт None,
# чтобы мы сами вернули 401 с нужным текстом.
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _check_api_key(api_key: str | None) -> None:
    """Проверяет ключ X-API-Key. Нет ключа или ключ неверный -> 401.

    Сравнение через compare_digest. Пустой ключ отбрасываем сразу:
    если API_SECRET не задан, никто не должен пройти авторизацию.
    """
    expected = settings.API_SECRET
    if not api_key or not expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется заголовок X-API-Key",
        )
    # compare_digest требует одинаковый тип; обе стороны — str.
    if not secrets.compare_digest(api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный API-ключ",
        )


async def require_manager(
    api_key: str | None = Security(api_key_header),
) -> str:
    """Доступ для любого владельца валидного ключа (минимальный уровень)."""
    _check_api_key(api_key)
    return "manager"


async def require_admin(
    api_key: str | None = Security(api_key_header),
    x_role: str | None = Header(default=None, alias="X-Role"),
) -> str:
    """Доступ только для админа: валидный ключ + X-Role: admin.

    Порядок важен: сначала ключ (нет/неверный -> 401), потом роль (не admin -> 403).
    Так 401 и 403 не путаются: «не аутентифицирован» и «нет прав» — разные ситуации.
    """
    _check_api_key(api_key)
    if x_role != _ADMIN_ROLE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Требуется роль admin (заголовок X-Role: admin)",
        )
    return "admin"
