"""Lead CSV parsing, validation, normalization, deduplication, and merging."""
from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from solution.ingestion.discover import LeadFile, discover_lead_files
from solution.models.schema_loader import FieldSchema, SchemaRegistry, WorkflowSchema
from solution.security.pii import PIIProtector
from solution.validation.date import normalize_date
from solution.validation.decimal import parse_decimal
from solution.validation.enum import validate_enum
from solution.validation.header import map_headers
from solution.validation.phone import normalize_phone


@dataclass(frozen=True)
class ValidatedLeadRow:
    """Result of validating and normalizing a single non-blank lead CSV row."""

    valid: bool
    rejection_reason: str | None
    fields: dict[str, str | None]
    warnings: int


@dataclass(frozen=True)
class LeadIngestionSummary:
    """Counts produced while ingesting lead files from a drop."""

    files_processed: int
    rows_total: int
    rows_rejected: int
    duplicates_merged: int
    field_warnings: int


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_drop_key(drop_path: Path) -> str:
    """Normalize a drop directory path to an absolute resolved string key."""
    return str(drop_path.resolve())


def is_drop_ingested(conn: sqlite3.Connection, drop_path: Path) -> bool:
    """Return True if this drop has already been ingested."""
    row = conn.execute(
        "SELECT 1 FROM drops_ingested WHERE drop_path = ?",
        (canonical_drop_key(drop_path),),
    ).fetchone()
    return row is not None


def mark_drop_ingested(
    conn: sqlite3.Connection,
    drop_path: Path,
    ingested_at: str | None = None,
) -> None:
    """Record a drop as ingested for drop-level idempotency."""
    conn.execute(
        "INSERT INTO drops_ingested (drop_path, ingested_at) VALUES (?, ?)",
        (canonical_drop_key(drop_path), ingested_at or utc_now_iso()),
    )


def increment_quality_metric(
    conn: sqlite3.Connection,
    metric_name: str,
    delta: int,
) -> None:
    """Increment a metric in data_quality by delta."""
    if delta == 0:
        return
    conn.execute(
        """
        INSERT INTO data_quality (metric_name, metric_value) VALUES (?, ?)
        ON CONFLICT(metric_name) DO UPDATE SET metric_value = metric_value + excluded.metric_value
        """,
        (metric_name, delta),
    )


def set_quality_metric(
    conn: sqlite3.Connection,
    metric_name: str,
    value: int,
) -> None:
    """Set an exact metric value in data_quality."""
    conn.execute(
        """
        INSERT INTO data_quality (metric_name, metric_value) VALUES (?, ?)
        ON CONFLICT(metric_name) DO UPDATE SET metric_value = excluded.metric_value
        """,
        (metric_name, value),
    )


def _parse_field_value(raw_trimmed: str, field: FieldSchema) -> str | None:
    """Validate and normalize a non-empty trimmed value according to field type."""
    ftype = field.field_type
    if ftype == "string":
        return raw_trimmed
    if ftype == "phone":
        return normalize_phone(raw_trimmed)
    if ftype == "date":
        return normalize_date(raw_trimmed)
    if ftype == "decimal":
        dec = parse_decimal(raw_trimmed)
        return f"{dec:.2f}" if dec is not None else None
    if ftype == "enum":
        return validate_enum(raw_trimmed, field.values)
    raise ValueError(f"Unsupported field type: {ftype}")


def validate_lead_row(
    raw_by_field: dict[str, str],
    schema: WorkflowSchema,
) -> ValidatedLeadRow:
    """Validate and normalize one non-blank row in schema field order.

    Returns immediately on the first rejection condition. Field warnings on
    optional invalid values are returned only when the row is not rejected.
    """
    parsed_fields: dict[str, str | None] = {}
    warnings = 0

    for field in schema.fields:
        raw_val = (raw_by_field.get(field.name) or "").strip()
        if not raw_val:
            if field.required:
                return ValidatedLeadRow(
                    valid=False,
                    rejection_reason=f"missing_required:{field.name}",
                    fields={},
                    warnings=0,
                )
            parsed_fields[field.name] = None
            continue

        normalized = _parse_field_value(raw_val, field)
        if normalized is None:
            if field.field_type == "phone":
                return ValidatedLeadRow(
                    valid=False,
                    rejection_reason="invalid_phone",
                    fields={},
                    warnings=0,
                )
            if field.required:
                return ValidatedLeadRow(
                    valid=False,
                    rejection_reason=f"invalid_{field.field_type}",
                    fields={},
                    warnings=0,
                )
            parsed_fields[field.name] = None
            warnings += 1
        else:
            parsed_fields[field.name] = normalized

    return ValidatedLeadRow(
        valid=True,
        rejection_reason=None,
        fields=parsed_fields,
        warnings=warnings,
    )


def _extract_raw_by_field(
    headers: list[str],
    row_cells: list[str],
    header_map: dict[str, str],
) -> dict[str, str]:
    """Map a CSV row's cells to canonical schema field names."""
    raw_by_field: dict[str, str] = {}
    for idx, raw_header in enumerate(headers):
        field_name = header_map.get(raw_header)
        if field_name is None:
            continue
        cell_val = row_cells[idx] if idx < len(row_cells) else ""
        if field_name not in raw_by_field or not raw_by_field[field_name].strip():
            raw_by_field[field_name] = cell_val
    return raw_by_field


def _build_extra_fields(
    parsed_fields: dict[str, str | None],
    schema: WorkflowSchema,
) -> dict[str, str | None]:
    """Extract non-PII, non-reference schema fields."""
    return {
        field.name: parsed_fields.get(field.name)
        for field in schema.fields
        if not field.pii and not field.reference
    }


def _apply_defaults(
    extra_fields: dict[str, str | None],
    schema: WorkflowSchema,
) -> dict[str, str | None]:
    """Apply schema defaults to any extra fields that are still None."""
    result = dict(extra_fields)
    for field in schema.fields:
        if not field.pii and not field.reference:
            if result.get(field.name) is None and field.default is not None:
                result[field.name] = field.default
    return result


def _upsert_lead(
    conn: sqlite3.Connection,
    workflow_id: str,
    batch_id: str,
    schema: WorkflowSchema,
    parsed_fields: dict[str, str | None],
    protector: PIIProtector,
    now_iso: str,
) -> bool:
    """Insert a new lead or merge into an existing lead. Returns True if merged duplicate."""
    canonical_phone = parsed_fields["phone"]
    customer_name = parsed_fields["customer_name"]
    external_ref = parsed_fields[schema.reference_field]
    assert canonical_phone is not None
    assert customer_name is not None
    assert external_ref is not None

    phone_hash = protector.hash_phone(canonical_phone)
    phone_last4_enc = protector.encrypt_last4(canonical_phone)
    name_enc = protector.encrypt_name(customer_name)
    incoming_extra = _build_extra_fields(parsed_fields, schema)

    existing = conn.execute(
        """
        SELECT lead_id, extra_fields_json
        FROM leads
        WHERE workflow_id = ? AND phone_hash = ?
        """,
        (workflow_id, phone_hash),
    ).fetchone()

    if existing is None:
        final_extra = _apply_defaults(incoming_extra, schema)
        conn.execute(
            """
            INSERT INTO leads (
                workflow_id, phone_hash, phone_last4_encrypted, customer_name_encrypted,
                batch_id, external_ref, extra_fields_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workflow_id,
                phone_hash,
                phone_last4_enc,
                name_enc,
                batch_id,
                external_ref,
                json.dumps(final_extra, sort_keys=True),
                now_iso,
                now_iso,
            ),
        )
        return False

    existing_extra = json.loads(existing["extra_fields_json"])
    merged_extra = dict(existing_extra)
    for key, val in incoming_extra.items():
        if val is not None:
            merged_extra[key] = val
    merged_extra = _apply_defaults(merged_extra, schema)

    conn.execute(
        """
        UPDATE leads
        SET phone_last4_encrypted = ?,
            customer_name_encrypted = ?,
            external_ref = ?,
            extra_fields_json = ?,
            updated_at = ?
        WHERE lead_id = ?
        """,
        (
            phone_last4_enc,
            name_enc,
            external_ref,
            json.dumps(merged_extra, sort_keys=True),
            now_iso,
            existing["lead_id"],
        ),
    )
    return True


def ingest_lead_file(
    conn: sqlite3.Connection,
    lead_file: LeadFile,
    schema: WorkflowSchema,
    protector: PIIProtector,
    now_iso: str,
) -> tuple[int, int, int, int]:
    """Ingest a single lead CSV file and return (total, rejected, merged, warnings)."""
    rows_total = 0
    rows_rejected = 0
    duplicates_merged = 0
    field_warnings = 0

    with lead_file.path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        headers = next(reader, None)
        if not headers:
            return (0, 0, 0, 0)

        header_map = map_headers(headers, schema)
        row_number = 0

        for cells in reader:
            if not cells or all(cell.strip() == "" for cell in cells):
                continue

            row_number += 1
            rows_total += 1

            raw_by_field = _extract_raw_by_field(headers, cells, header_map)
            result = validate_lead_row(raw_by_field, schema)

            if not result.valid:
                rows_rejected += 1
                assert result.rejection_reason is not None
                conn.execute(
                    """
                    INSERT INTO rejections (workflow_id, batch_id, row_number, reason)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        lead_file.workflow_id,
                        lead_file.batch_id,
                        row_number,
                        result.rejection_reason,
                    ),
                )
                continue

            field_warnings += result.warnings
            is_duplicate = _upsert_lead(
                conn=conn,
                workflow_id=lead_file.workflow_id,
                batch_id=lead_file.batch_id,
                schema=schema,
                parsed_fields=result.fields,
                protector=protector,
                now_iso=now_iso,
            )
            if is_duplicate:
                duplicates_merged += 1

    return (rows_total, rows_rejected, duplicates_merged, field_warnings)


def ingest_drop_leads(
    conn: sqlite3.Connection,
    drop_path: Path,
    registry: SchemaRegistry,
    protector: PIIProtector,
) -> LeadIngestionSummary:
    """Ingest all lead CSV files in a drop in deterministic order."""
    lead_files = discover_lead_files(drop_path, registry)
    now_iso = utc_now_iso()

    total_rows = 0
    total_rejected = 0
    total_merged = 0
    total_warnings = 0

    for lead_file in lead_files:
        schema = registry.get_workflow(lead_file.workflow_id)
        rows, rejected, merged, warnings = ingest_lead_file(
            conn=conn,
            lead_file=lead_file,
            schema=schema,
            protector=protector,
            now_iso=now_iso,
        )
        total_rows += rows
        total_rejected += rejected
        total_merged += merged
        total_warnings += warnings

    increment_quality_metric(conn, "lead_rows_total", total_rows)
    increment_quality_metric(conn, "lead_rows_rejected", total_rejected)
    increment_quality_metric(conn, "duplicate_lead_rows_merged", total_merged)
    increment_quality_metric(conn, "field_warnings", total_warnings)

    return LeadIngestionSummary(
        files_processed=len(lead_files),
        rows_total=total_rows,
        rows_rejected=total_rejected,
        duplicates_merged=total_merged,
        field_warnings=total_warnings,
    )
