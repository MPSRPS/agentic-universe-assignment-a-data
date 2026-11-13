"""Unit tests for phone validation and canonical normalization."""
from __future__ import annotations

import pytest

from solution.validation.phone import normalize_phone


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("9876543210", "+919876543210"),
        ("6000000000", "+916000000000"),
        ("7123456789", "+917123456789"),
        ("8999999999", "+918999999999"),
        ("+91 98765 43210", "+919876543210"),
        ("+919876543210", "+919876543210"),
        ("(+91) 9244795133", "+919244795133"),
        ("91-77187-50036", "+917718750036"),
        ("919876543210", "+919876543210"),
        ("09876543210", "+919876543210"),
        ("  0 98765-43210  ", "+919876543210"),
    ],
)
def test_normalize_phone_valid_formats(raw: str, expected: str) -> None:
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "N/A",
        "123456789",           # 9 digits
        "98765432101",         # 11 digits not starting with 0
        "00919876543210",      # 14 digits
        "5123456789",          # 10 digits starting with 5
        "0123456789",          # 10 digits starting with 0
        "915123456789",        # 12 digits starting with 91, remaining starts with 5
        "05123456789",         # 11 digits starting with 0, remaining starts with 5
        "022-2123-4567",       # landline
        "+1 415 555 0134",     # US number
        "819876543210",        # 12 digits not starting with 91
    ],
)
def test_normalize_phone_invalid(raw: str | None) -> None:
    assert normalize_phone(raw) is None
