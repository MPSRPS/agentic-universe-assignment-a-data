"""Enum field validation and normalization."""
from __future__ import annotations

from collections.abc import Iterable


def validate_enum(raw: str | None, allowed_values: Iterable[str]) -> str | None:
    """Validate an enum value case-insensitively and return it lower-cased, or None."""
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None

    allowed_lower = {v.strip().lower() for v in allowed_values}
    candidate = text.lower()
    if candidate in allowed_lower:
        return candidate
    return None
