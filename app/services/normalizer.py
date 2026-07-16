"""Чистые функции нормализации данных прайс-листов.

Модуль без БД и без внешних зависимостей: только стандартная библиотека.
Используется импортёром для приведения OEM/брендов/цен/остатков к единому виду.
"""
from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

logger = logging.getLogger(__name__)

__all__ = [
    "normalize_oem",
    "normalize_brand",
    "parse_price",
    "parse_quantity",
]

# Похожие по начертанию кириллические буквы -> латиница.
# Нужны, потому что в прайсах OEM часто набирают русскими буквами,
# визуально неотличимыми от латинских (напр. кириллическая "С" вместо латинской "C").
_CYRILLIC_TO_LATIN = str.maketrans({
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H",
    "О": "O", "Р": "P", "С": "C", "Т": "T", "У": "Y", "Х": "X",
})

# Символы-разделители, которые нужно вырезать из OEM: пробелы, дефисы, точки, слэши.
_OEM_STRIP_RE = re.compile(r"[\s\-./]")


def normalize_oem(raw: str) -> str:
    """Приводит артикул (OEM) к каноническому виду для сравнения.

    Шаги: вырезаем пробелы/дефисы/точки/слэши, переводим в верхний регистр,
    заменяем кириллические двойники на латиницу. Пустой результат -> ValueError.
    """
    if raw is None:
        raise ValueError("OEM не может быть None")
    # 1) убираем разделители; 2) верхний регистр; 3) кириллица -> латиница.
    # Порядок важен: сначала upper(), т.к. таблица замен рассчитана на заглавные буквы.
    cleaned = _OEM_STRIP_RE.sub("", str(raw)).upper().translate(_CYRILLIC_TO_LATIN)
    if not cleaned:
        logger.debug("normalize_oem: пустой результат для %r", raw)
        raise ValueError(f"OEM пустой после нормализации: {raw!r}")
    return cleaned


def normalize_brand(raw: str) -> str:
    """Нормализует бренд: трим, схлопывание повторных пробелов, Title Case."""
    if raw is None:
        return ""
    # str.split() без аргументов бьёт по любым пробельным и убирает пустые токены —
    # это и есть схлопывание пробелов. Затем Title Case.
    collapsed = " ".join(str(raw).split())
    return collapsed.title()


def parse_price(raw) -> Decimal:
    """Разбирает цену в Decimal с округлением до 2 знаков (ROUND_HALF_UP).

    Поддерживает форматы: "1 234,50" (RU), "1,234.50" (US), "1234.5", "1234",
    а также готовые числа (int/float/Decimal).
    Пусто/текст/отрицательное/не число -> ValueError. Деньги — всегда Decimal, не float.
    """
    # bool — подкласс int, но цена булевой быть не должна: явно отбраковываем.
    if isinstance(raw, bool):
        raise ValueError(f"недопустимая цена: {raw!r}")

    if isinstance(raw, (int, Decimal)):
        value = Decimal(raw)
    elif isinstance(raw, float):
        # float -> Decimal через str, иначе поймаем «мусорные хвосты» вроде 6.7999999.
        value = Decimal(str(raw))
    elif isinstance(raw, str):
        s = raw.strip()
        if not s:
            raise ValueError("пустая цена")
        # Убираем пробелы-разделители тысяч (обычные и неразрывные).
        s = s.replace("\xa0", "").replace(" ", "")
        has_comma = "," in s
        has_dot = "." in s
        if has_comma and has_dot:
            # Есть оба разделителя: десятичным считаем тот, что правее.
            if s.rfind(",") > s.rfind("."):
                # запятая — десятичная, точка — тысячи: "1.234,56" -> "1234.56"
                s = s.replace(".", "").replace(",", ".")
            else:
                # точка — десятичная, запятая — тысячи: "1,234.56" -> "1234.56"
                s = s.replace(",", "")
        elif has_comma:
            # Только запятая — трактуем как десятичный разделитель: "890,50" -> "890.50"
            s = s.replace(",", ".")
        # Только точка или без разделителей — оставляем строку как есть.
        try:
            value = Decimal(s)
        except InvalidOperation:
            logger.debug("parse_price: не удалось разобрать %r", raw)
            raise ValueError(f"не удалось разобрать цену: {raw!r}") from None
    else:
        raise ValueError(f"неподдерживаемый тип цены: {type(raw).__name__}")

    # Отбраковываем NaN/Infinity и отрицательные значения.
    if not value.is_finite():
        raise ValueError(f"цена не является конечным числом: {raw!r}")
    if value < 0:
        raise ValueError(f"отрицательная цена: {raw!r}")

    # Округление до копеек по правилу «половина вверх».
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def parse_quantity(raw) -> int:
    """Разбирает остаток (количество) в int.

    ">10" -> 10 (отбрасываем ведущий знак «больше»), пусто -> 0.
    Текст/отрицательное/дробное -> ValueError.
    """
    if isinstance(raw, bool):
        raise ValueError(f"недопустимый остаток: {raw!r}")

    if isinstance(raw, int):
        quantity = raw
    elif isinstance(raw, str):
        s = raw.strip()
        if not s:
            # Пустая ячейка остатка — считаем, что остаток неизвестен -> 0.
            return 0
        # Форматы вида ">10" (в наличии больше 10): убираем ведущий '>'.
        s = s.lstrip(">").strip()
        if not s:
            raise ValueError(f"не удалось разобрать остаток: {raw!r}")
        try:
            quantity = int(s)
        except ValueError:
            logger.debug("parse_quantity: не число %r", raw)
            raise ValueError(f"не удалось разобрать остаток: {raw!r}") from None
    else:
        raise ValueError(f"неподдерживаемый тип остатка: {type(raw).__name__}")

    if quantity < 0:
        raise ValueError(f"отрицательный остаток: {raw!r}")
    return quantity
