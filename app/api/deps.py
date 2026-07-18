"""Зависимости авторизации для REST API.

Схема на основе статичного ключа в заголовке X-API-Key:
- require_manager — любой валидный ключ (доступ только на поиск);
- require_admin — админский доступ (загрузка данных и т.п.).

Админ-доступ поддерживает два режима (см. app/core/config.py):
1. ADMIN_API_SECRET НЕ задан (по умолчанию, обратная совместимость):
   админ = валидный общий ключ + заголовок X-Role: admin. Это осознанное
   упрощение для учебного проекта — клиент фактически сам объявляет себя
   админом, разделения секретов между ролями нет.
2. ADMIN_API_SECRET задан (рекомендуется для «боевого» варианта):
   админом считается ТОЛЬКО владелец отдельного админского ключа
   (X-API-Key: <ADMIN_API_SECRET>). Заголовок X-Role больше не влияет.

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

# Заголовок с ролью, дающей админский доступ (в режиме обратной совместимости).
_ADMIN_ROLE = "admin"

# Штатная схема безопасности: ключ в заголовке X-API-Key.
# auto_error=False — если заголовка нет, FastAPI не бросает свою 403, а отдаёт None,
# чтобы мы сами вернули 401 с нужным текстом.
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _valid_api_keys() -> list[str]:
    """Список ключей, дающих доступ к API (пустые значения игнорируем).

    Общий API_SECRET — минимальный доступ (manager). Если задан отдельный
    ADMIN_API_SECRET, он тоже валиден как ключ доступа (и дополнительно
    даёт админские права в require_admin).
    """
    keys: list[str] = []
    if settings.API_SECRET:
        keys.append(settings.API_SECRET)
    if settings.ADMIN_API_SECRET:
        keys.append(settings.ADMIN_API_SECRET)
    return keys


def _check_api_key(api_key: str | None) -> None:
    """Проверяет ключ X-API-Key. Нет ключа или ключ неверный -> 401.

    Сравнение через compare_digest по каждому известному ключу. Если ни один
    ключ не настроен (API_SECRET и ADMIN_API_SECRET пусты), никто не должен
    пройти авторизацию.
    """
    expected_keys = _valid_api_keys()
    if not api_key or not expected_keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется заголовок X-API-Key",
        )
    # compare_digest требует одинаковый тип; обе стороны — str.
    # Проверяем по всем ключам, не прерываясь досрочно, чтобы не давать
    # подсказку по времени о том, какой именно ключ подошёл.
    matched = False
    for expected in expected_keys:
        if secrets.compare_digest(api_key, expected):
            matched = True
    if not matched:
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
    """Доступ только для админа.

    Порядок важен: сначала ключ (нет/неверный -> 401), потом проверка прав
    (нет прав -> 403). Так 401 и 403 не путаются: «не аутентифицирован» и
    «нет прав» — разные ситуации.
    """
    _check_api_key(api_key)

    admin_secret = settings.ADMIN_API_SECRET
    if admin_secret:
        # Строгий режим: админ — только владелец отдельного ADMIN_API_SECRET.
        if not api_key or not secrets.compare_digest(api_key, admin_secret):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Требуется админский ключ (заголовок X-API-Key со значением ADMIN_API_SECRET)",
            )
        return "admin"

    # Обратная совместимость: общий ключ + заголовок X-Role: admin.
    if x_role != _ADMIN_ROLE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Требуется роль admin (заголовок X-Role: admin)",
        )
    return "admin"
