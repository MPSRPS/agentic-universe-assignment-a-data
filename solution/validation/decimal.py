"""Decimal / currency amount parsing and normalization."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

_CURRENCY_PREFIX_RE = re.compile(r"^(?:₹|rs\.?|inr)\s*", re.IGNORECASE)
_NUMERIC_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")
_TWO_PLACES = Decimal("0.01")


def parse_decimal(raw: str | None) -> Decimal | None:
    """Parse a non-negative decimal string, stripping currency prefixes and commas.

    Supports optional leading '₹', 'Rs', 'Rs.', or 'INR' (case-insensitive, optional
    whitespace). Commas are treated as thousands separators. Values < 0 are invalid.
    Valid values are rounded to 2 decimal places (ROUND_HALF_UP).
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None

    text = _CURRENCY_PREFIX_RE.sub("", text, count=1).strip()
    if not text:
        return None

    # Validate comma placement isn't empty/garbage; remove thousands separators.
    if "," in text:
        integer_part = text.split(".", 1)[0]
        fractional_part = text.split(".", 1)[1] if "." in text else ""
        if (
            "," in fractional_part
            or integer_part.startswith(",")
            or integer_part.endswith(",")
            or ",," in integer_part
        ):
            return None
        text = text.replace(",", "")

    if not _NUMERIC_RE.match(text):
        return None

    try:
        value = Decimal(text)
    except InvalidOperation:
        return None

    if not value.is_finite() or value < 0:
        return None

    return value.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
