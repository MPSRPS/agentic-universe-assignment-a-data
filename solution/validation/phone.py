"""Indian mobile phone validation and canonical normalization."""
from __future__ import annotations

import re

_NON_DIGITS_RE = re.compile(r"\D+")
_VALID_START_DIGITS = frozenset({"6", "7", "8", "9"})


def normalize_phone(raw: str | None) -> str | None:
    """Normalize an Indian mobile number to '+91XXXXXXXXXX', or return None if invalid.

    Rules:
    - Strip every non-digit character.
    - If 12 digits starting with '91', drop the '91'.
    - If 11 digits starting with '0', drop the '0'.
    - Valid only if the remaining string is exactly 10 digits starting with 6, 7, 8, or 9.
    """
    if raw is None:
        return None
    digits = _NON_DIGITS_RE.sub("", raw)
    if not digits:
        return None

    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]

    if len(digits) != 10 or digits[0] not in _VALID_START_DIGITS:
        return None

    return f"+91{digits}"
