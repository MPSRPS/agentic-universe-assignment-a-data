"""Event JSONL parsing, deduplication, lead linking, and ledger insertion."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from solution.config import IST
from solution.ingestion.discover import discover_event_files
from solution.ingestion.leads import increment_quality_metric, utc_now_iso
from solution.models.schema_loader import SchemaRegistry
from solution.security.pii import PIIProtector
from solution.validation.phone import normalize_phone


@dataclass(frozen=True)
class EventIngestionSummary:
    """Counts produced while ingesting event JSONL files from a drop."""

    files_processed: int
    events_total: int
    malformed_events: int
    duplicate_events_dropped: int
    linked_events: int
    quarantined_orphans: int
    unknown_disposition_events: int


def parse_event_timestamp_utc(raw_ts: Any) -> str | None:
    """Parse UTC ('...Z'), offset ('...+05:30'), or naive (IST) timestamp to UTC ISO-8601."""
    if not isinstance(raw_ts, str):
        return None
    text = raw_ts.strip()
    if not text:
        return None

    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)

    utc_dt = dt.astimezone(timezone.utc)
    if utc_dt.microsecond:
        return utc_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def is_unknown_disposition(
    event_type: str,
    workflow_id: str,
    payload: dict[str, Any],
    registry: SchemaRegistry,
) -> bool:
    """Return True if a disposition.* event has a code absent from dispositions.csv."""
    if not event_type.startswith("disposition."):
        return False
    code = payload.get("disposition_code")
    if not isinstance(code, str) or not code.strip():
        return True
    return registry.get_disposition(workflow_id, code.strip()) is None


def is_event_seen(conn: sqlite3.Connection, event_id: str) -> bool:
    """Return True if event_id already exists in events or orphan_events."""
    row = conn.execute(
        """
        SELECT 1 FROM events WHERE event_id = ?
        UNION ALL
        SELECT 1 FROM orphan_events WHERE event_id = ?
        LIMIT 1
        """,
        (event_id, event_id),
    ).fetchone()
    return row is not None


def resolve_lead_id_by_phone(
    conn: sqlite3.Connection,
    workflow_id: str,
    phone_hash: str,
) -> int | None:
    """Look up lead_id by (workflow_id, phone_hash)."""
    row = conn.execute(
        "SELECT lead_id FROM leads WHERE workflow_id = ? AND phone_hash = ?",
        (workflow_id, phone_hash),
    ).fetchone()
    return int(row["lead_id"]) if row is not None else None


def resolve_lead_id_by_call(
    conn: sqlite3.Connection,
    workflow_id: str,
    call_id: str,
) -> int | None:
    """Look up lead_id from already-linked events sharing (workflow_id, call_id)."""
    row = conn.execute(
        "SELECT lead_id FROM events WHERE workflow_id = ? AND call_id = ? LIMIT 1",
        (workflow_id, call_id),
    ).fetchone()
    return int(row["lead_id"]) if row is not None else None


def insert_linked_event(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    event_type: str,
    workflow_id: str,
    call_id: str,
    lead_id: int,
    occurred_at: str,
    payload_json: str,
    is_ignored: int,
    ingested_at: str,
) -> None:
    """Append a linked event to the append-only events ledger."""
    conn.execute(
        """
        INSERT INTO events (
            event_id, event_type, workflow_id, call_id, lead_id,
            occurred_at, payload_json, is_ignored, ingested_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            event_type,
            workflow_id,
            call_id,
            lead_id,
            occurred_at,
            payload_json,
            is_ignored,
            ingested_at,
        ),
    )


def _quarantine_orphan(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    event_type: str,
    workflow_id: str,
    call_id: str,
    phone_hash: str | None,
    occurred_at: str,
    payload_json: str,
    ingested_at: str,
) -> None:
    """Store an unlinked event in orphan_events without plaintext PII."""
    conn.execute(
        """
        INSERT INTO orphan_events (
            event_id, event_type, workflow_id, call_id, phone_hash,
            occurred_at, payload_json, ingested_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            event_type,
            workflow_id,
            call_id,
            phone_hash,
            occurred_at,
            payload_json,
            ingested_at,
        ),
    )


def _parse_event_record(line: str) -> tuple[str, str, str, str, str, dict[str, Any], str | None] | None:
    """Parse and validate structural fields of a JSONL event line.

    Returns (event_id, event_type, workflow_id, call_id, occurred_at_utc, payload, raw_phone)
    or None if the line is malformed.
    """
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(obj, dict):
        return None

    event_id = obj.get("event_id")
    event_type = obj.get("event_type")
    workflow_id = obj.get("workflow_id")
    call_id = obj.get("call_id")
    occurred_at_utc = parse_event_timestamp_utc(obj.get("occurred_at"))

    if not all(isinstance(v, str) and v.strip() for v in (event_id, event_type, workflow_id, call_id)):
        return None
    if occurred_at_utc is None:
        return None

    payload = obj.get("payload")
    if payload is None:
        payload = {}
    elif not isinstance(payload, dict):
        return None

    raw_phone = obj.get("phone")
    phone_str = raw_phone if isinstance(raw_phone, str) else None

    return (
        event_id.strip(),
        event_type.strip(),
        workflow_id.strip(),
        call_id.strip(),
        occurred_at_utc,
        payload,
        phone_str,
    )


def ingest_event_file(
    conn: sqlite3.Connection,
    event_file: Path,
    registry: SchemaRegistry,
    protector: PIIProtector,
    now_iso: str,
) -> tuple[int, int, int, int, int, int]:
    """Ingest one JSONL file.

    Returns (total, malformed, duplicates, linked, orphans, unknown_dispositions).
    """
    events_total = 0
    malformed = 0
    duplicates = 0
    linked = 0
    orphans = 0
    unknown_dispositions = 0

    with event_file.open("r", encoding="utf-8-sig") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue

            events_total += 1
            parsed = _parse_event_record(line)
            if parsed is None:
                malformed += 1
                continue

            event_id, event_type, workflow_id, call_id, occurred_at_utc, payload, raw_phone = parsed

            if is_event_seen(conn, event_id):
                duplicates += 1
                continue

            sanitized_payload = PIIProtector.sanitize_event_payload(
                payload,
                registry.all_pii_field_names,
            )
            payload_json = json.dumps(sanitized_payload, sort_keys=True)

            phone_hash: str | None = None
            lead_id: int | None = None

            if event_type == "disposition.override":
                lead_id = resolve_lead_id_by_call(conn, workflow_id, call_id)
            else:
                canonical_phone = normalize_phone(raw_phone)
                if canonical_phone is not None:
                    phone_hash = protector.hash_phone(canonical_phone)
                    lead_id = resolve_lead_id_by_phone(conn, workflow_id, phone_hash)

            if lead_id is None:
                _quarantine_orphan(
                    conn,
                    event_id=event_id,
                    event_type=event_type,
                    workflow_id=workflow_id,
                    call_id=call_id,
                    phone_hash=phone_hash,
                    occurred_at=occurred_at_utc,
                    payload_json=payload_json,
                    ingested_at=now_iso,
                )
                orphans += 1
                continue

            unknown = is_unknown_disposition(event_type, workflow_id, sanitized_payload, registry)
            if unknown:
                unknown_dispositions += 1

            insert_linked_event(
                conn,
                event_id=event_id,
                event_type=event_type,
                workflow_id=workflow_id,
                call_id=call_id,
                lead_id=lead_id,
                occurred_at=occurred_at_utc,
                payload_json=payload_json,
                is_ignored=1 if unknown else 0,
                ingested_at=now_iso,
            )
            linked += 1

    return (events_total, malformed, duplicates, linked, orphans, unknown_dispositions)


def ingest_drop_events(
    conn: sqlite3.Connection,
    drop_path: Path,
    registry: SchemaRegistry,
    protector: PIIProtector,
) -> EventIngestionSummary:
    """Ingest all event JSONL files in a drop in deterministic filename order."""
    event_files = discover_event_files(drop_path)
    now_iso = utc_now_iso()

    total_events = 0
    total_malformed = 0
    total_duplicates = 0
    total_linked = 0
    total_orphans = 0
    total_unknown = 0

    for event_file in event_files:
        ev_total, mal, dup, lnk, orph, unk = ingest_event_file(
            conn=conn,
            event_file=event_file,
            registry=registry,
            protector=protector,
            now_iso=now_iso,
        )
        total_events += ev_total
        total_malformed += mal
        total_duplicates += dup
        total_linked += lnk
        total_orphans += orph
        total_unknown += unk

    increment_quality_metric(conn, "events_total", total_events)
    increment_quality_metric(conn, "malformed_events", total_malformed)
    increment_quality_metric(conn, "duplicate_events_dropped", total_duplicates)
    increment_quality_metric(conn, "unknown_disposition_events", total_unknown)

    return EventIngestionSummary(
        files_processed=len(event_files),
        events_total=total_events,
        malformed_events=total_malformed,
        duplicate_events_dropped=total_duplicates,
        linked_events=total_linked,
        quarantined_orphans=total_orphans,
        unknown_disposition_events=total_unknown,
    )
