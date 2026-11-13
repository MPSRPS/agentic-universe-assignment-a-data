"""CSV header normalization and schema alias mapping."""
from __future__ import annotations

import re
from collections.abc import Sequence

from solution.models.schema_loader import WorkflowSchema

_SEP_RUN_RE = re.compile(r"[_\s]+")


def normalize_header(header: str) -> str:
    """Normalize a header by lower-casing, trimming, and collapsing '_' or whitespace."""
    return _SEP_RUN_RE.sub(" ", header.strip().lower()).strip()


def map_headers(
    csv_headers: Sequence[str],
    schema: WorkflowSchema,
) -> dict[str, str]:
    """Map raw CSV header names to canonical schema field names.

    Matches normalized CSV headers against each field's normalized name and aliases.
    Unrecognized CSV columns are omitted (ignored).
    """
    lookup: dict[str, str] = {}
    for field in schema.fields:
        lookup[normalize_header(field.name)] = field.name
        for alias in field.aliases:
            lookup[normalize_header(alias)] = field.name

    mapping: dict[str, str] = {}
    for raw_col in csv_headers:
        if raw_col is None:
            continue
        norm = normalize_header(raw_col)
        if norm in lookup:
            mapping[raw_col] = lookup[norm]
    return mapping
