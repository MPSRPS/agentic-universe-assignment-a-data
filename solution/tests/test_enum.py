"""Unit tests for enum validation."""
from __future__ import annotations

import pytest

from solution.validation.enum import validate_enum

LANGS = ("en", "hi", "ta", "kn", "te", "mr")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("en", "en"),
        ("EN", "en"),
        (" Hi ", "hi"),
        ("TA", "ta"),
        ("mr", "mr"),
    ],
)
def test_validate_enum_valid(raw: str, expected: str) -> None:
    assert validate_enum(raw, LANGS) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "Hindi",
        "english",
        "tamil",
        "fr",
    ],
)
def test_validate_enum_invalid(raw: str | None) -> None:
    assert validate_enum(raw, LANGS) is None
