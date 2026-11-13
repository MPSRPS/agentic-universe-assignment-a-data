"""Unit tests for date parsing and normalization."""
from __future__ import annotations

from datetime import date

import pytest

from solution.validation.date import normalize_date, parse_date


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-10-15", date(2026, 10, 15)),
        ("  2026-11-23 ", date(2026, 11, 23)),
        ("15/10/2026", date(2026, 10, 15)),
        ("01/02/2026", date(2026, 2, 1)),      # day-first
        ("15-10-2026", date(2026, 10, 15)),
        ("01-Dec-2026", date(2026, 12, 1)),
        ("01-dec-2026", date(2026, 12, 1)),    # case-insensitive month
        ("01-DEC-2026", date(2026, 12, 1)),
        ("29-Feb-2024", date(2024, 2, 29)),    # leap year
    ],
)
def test_parse_date_valid_formats(raw: str, expected: date) -> None:
    assert parse_date(raw) == expected
    assert normalize_date(raw) == expected.isoformat()


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "31/02/2026",     # impossible day
        "29/02/2025",     # non-leap year
        "2026-13-01",     # month 13
        "2026-00-10",     # month 0
        "00-10-2026",     # day 0
        "10-15-2026",     # month 15 under day-first
        "01-Foo-2026",    # invalid month name
        "2026/10/15",     # unsupported YYYY/MM/DD
        "soon",
        "TBD",
    ],
)
def test_parse_date_invalid(raw: str | None) -> None:
    assert parse_date(raw) is None
    assert normalize_date(raw) is None
