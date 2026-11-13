"""Date parsing and normalization."""
from __future__ import annotations

import re
from datetime import date

_MONTH_ABBR: dict[str, int] = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

_ISO_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_SLASH_DMY_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_DASH_DMY_RE = re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{4})$")
_MON_DMY_RE = re.compile(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4})$")


def parse_date(raw: str | None) -> date | None:
    """Parse a date string in YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY, or DD-Mon-YYYY.

    Dates are day-first (except YYYY-MM-DD). Month names are case-insensitive.
    Impossible dates (e.g. 31/02/2026) return None.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None

    year: int
    month: int
    day: int

    if match := _ISO_RE.match(text):
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
    elif match := _SLASH_DMY_RE.match(text):
        day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
    elif match := _DASH_DMY_RE.match(text):
        day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
    elif match := _MON_DMY_RE.match(text):
        day = int(match.group(1))
        month_str = match.group(2).lower()
        if month_str not in _MONTH_ABBR:
            return None
        month = _MONTH_ABBR[month_str]
        year = int(match.group(3))
    else:
        return None

    try:
        return date(year, month, day)
    except ValueError:
        return None


def normalize_date(raw: str | None) -> str | None:
    """Parse a date string and return canonical ISO-8601 'YYYY-MM-DD', or None if invalid."""
    parsed = parse_date(raw)
    return parsed.isoformat() if parsed is not None else None
