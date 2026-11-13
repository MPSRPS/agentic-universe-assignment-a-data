"""Unit tests for decimal / currency parsing and normalization."""
from __future__ import annotations

from decimal import Decimal

import pytest

from solution.validation.decimal import parse_decimal


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0", Decimal("0.00")),
        ("0.00", Decimal("0.00")),
        ("6850", Decimal("6850.00")),
        ("4737.14", Decimal("4737.14")),
        ("12345.678", Decimal("12345.68")),
        ("12.345", Decimal("12.35")),
        ("₹27,554", Decimal("27554.00")),
        ("₹ 1,23,456.7", Decimal("123456.70")),
        ("Rs. 24787", Decimal("24787.00")),
        ("rs 5000", Decimal("5000.00")),
        ("RS.500.25", Decimal("500.25")),
        ("INR 25301.4", Decimal("25301.40")),
        ("inr100", Decimal("100.00")),
        ("  ₹ 10,000.00  ", Decimal("10000.00")),
    ],
)
def test_parse_decimal_valid(raw: str, expected: Decimal) -> None:
    assert parse_decimal(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "₹",
        "Rs.",
        "INR",
        "-1",
        "-100.50",
        "₹ -50",
        "N/A",
        "-",
        "twelve thousand",
        "NaN",
        "Infinity",
        "1e5",
        "1_000",
        ",100",
        "100,",
        "1,,000",
        "12.3,4",
        "$100",
    ],
)
def test_parse_decimal_invalid(raw: str | None) -> None:
    assert parse_decimal(raw) is None
