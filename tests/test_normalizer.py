"""Тесты чистых функций нормализации (pytest, только parametrize)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.normalizer import (
    normalize_brand,
    normalize_oem,
    parse_price,
    parse_quantity,
)


# --- normalize_oem: корректные случаи (вкл. кириллицу) --------------------
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("W712/75", "W71275"),
        ("w712/75", "W71275"),
        ("W712 / 75", "W71275"),
        ("  gdb - 1330 ", "GDB1330"),
        ("gdb-1330", "GDB1330"),
        ("C-25114", "C25114"),
        ("BKR6E-11", "BKR6E11"),
        ("5PK-1110", "5PK1110"),
        ("HU 719/7X", "HU7197X"),
        ("a.b.c", "ABC"),
        # Кириллические двойники -> латиница
        ("ВК820/1", "BK8201"),             # В,К кириллические
        ("вк820/1", "BK8201"),             # нижний регистр кириллицы
        ("СU2545", "CU2545"),              # С кириллическая
        ("О000", "O000"),                  # О кириллическая
        ("АВЕКМНОРСТУХ", "ABEKMHOPCTYX"),  # полная таблица замен
        ("авекмнорстух", "ABEKMHOPCTYX"),  # то же в нижнем регистре
    ],
)
def test_normalize_oem_valid(raw, expected):
    assert normalize_oem(raw) == expected


# --- normalize_oem: ошибки -------------------------------------------------
@pytest.mark.parametrize("raw", ["", "   ", "///", " - . / ", "\t\n"])
def test_normalize_oem_invalid(raw):
    with pytest.raises(ValueError):
        normalize_oem(raw)


# --- normalize_brand -------------------------------------------------------
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("MANN", "Mann"),
        ("  trw  ", "Trw"),
        ("gates", "Gates"),
        ("  mann   filter  ", "Mann Filter"),
        ("NGK", "Ngk"),
        ("", ""),
    ],
)
def test_normalize_brand(raw, expected):
    assert normalize_brand(raw) == expected


# --- parse_price: корректные случаи ---------------------------------------
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1 234,50", Decimal("1234.50")),
        ("1234.5", Decimal("1234.50")),
        ("1,234.50", Decimal("1234.50")),
        ("1234", Decimal("1234.00")),
        ("890,50", Decimal("890.50")),
        ("6.80", Decimal("6.80")),
        ("1 234 567,89", Decimal("1234567.89")),
        ("0", Decimal("0.00")),
        (1234, Decimal("1234.00")),
        (6.8, Decimal("6.80")),
        ("10.005", Decimal("10.01")),   # ROUND_HALF_UP: 5 округляется вверх
        ("10.004", Decimal("10.00")),
        (Decimal("99.999"), Decimal("100.00")),
    ],
)
def test_parse_price_valid(raw, expected):
    assert parse_price(raw) == expected


# --- parse_price: ошибки ---------------------------------------------------
@pytest.mark.parametrize(
    "raw",
    ["", "   ", "нет цены", "abc", "-100,00", "-1", None, True],
)
def test_parse_price_invalid(raw):
    with pytest.raises(ValueError):
        parse_price(raw)


# --- parse_quantity: корректные случаи ------------------------------------
@pytest.mark.parametrize(
    "raw, expected",
    [
        (">10", 10),
        ("10", 10),
        ("", 0),
        ("   ", 0),
        ("0", 0),
        (34, 34),
        ("> 5", 5),
    ],
)
def test_parse_quantity_valid(raw, expected):
    assert parse_quantity(raw) == expected


# --- parse_quantity: ошибки -----------------------------------------------
@pytest.mark.parametrize("raw", ["-5", "abc", "10.5", ">-5", True])
def test_parse_quantity_invalid(raw):
    with pytest.raises(ValueError):
        parse_quantity(raw)
