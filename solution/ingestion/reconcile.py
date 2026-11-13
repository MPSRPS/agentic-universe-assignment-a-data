"""Orphan event reconciliation against newly arrived leads and calls."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from solution.ingestion.events import (
    insert_linked_event,
    is_unknown_disposition,
    resolve_lead_id_by_call,
    resolve_lead_id_by_phone,
)
from solution.ingestion.leads import increment_quality_metric, set_quality_metric
from solution.models.schema_loader import SchemaRegistry


@dataclass(frozen=True)
class ReconciliationSummary:
    """Summary of an orphan reconciliation pass."""

    reconciled_by_phone: int
    reconciled_by_call: int
    unknown_dispositions_found: int
    remaining_orphans: int


def _promote_orphan(
    conn: sqlite3.Connection,
    orphan_row: sqlite3.Row,
    lead_id: int,
    registry: SchemaRegistry,
) -> bool:
    """Move one resolved orphan from orphan_events into events.

    Returns True if the promoted event had an unknown disposition code.
    """
    payload = json.loads(orphan_row["payload_json"])
    unknown = is_unknown_disposition(
        orphan_row["event_type"],
        orphan_row["workflow_id"],
        payload,
        registry,
    )

    insert_linked_event(
        conn,
        event_id=orphan_row["event_id"],
        event_type=orphan_row["event_type"],
        workflow_id=orphan_row["workflow_id"],
        call_id=orphan_row["call_id"],
        lead_id=lead_id,
        occurred_at=orphan_row["occurred_at"],
        payload_json=orphan_row["payload_json"],
        is_ignored=1 if unknown else 0,
        ingested_at=orphan_row["ingested_at"],
    )
    conn.execute("DELETE FROM orphan_events WHERE orphan_id = ?", (orphan_row["orphan_id"],))
    return unknown


def reconcile_orphans(
    conn: sqlite3.Connection,
    registry: SchemaRegistry,
) -> ReconciliationSummary:
    """Reconcile quarantined orphan events against all known leads and calls.

    Pass 1 resolves orphans carrying a phone_hash via (workflow_id, phone_hash).
    Pass 2 resolves phone-less disposition.override orphans via (workflow_id, call_id).
    Updates data_quality.orphan_events to the exact remaining unresolved count.
    """
    reconciled_phone = 0
    reconciled_call = 0
    unknown_count = 0

    # Pass 1: link events that carry a phone_hash.
    phone_orphans = conn.execute(
        """
        SELECT orphan_id, event_id, event_type, workflow_id, call_id,
               phone_hash, occurred_at, payload_json, ingested_at
        FROM orphan_events
        WHERE phone_hash IS NOT NULL
        ORDER BY orphan_id
        """
    ).fetchall()

    for row in phone_orphans:
        lead_id = resolve_lead_id_by_phone(conn, row["workflow_id"], row["phone_hash"])
        if lead_id is not None:
            if _promote_orphan(conn, row, lead_id, registry):
                unknown_count += 1
            reconciled_phone += 1

    # Pass 2: link disposition.override events via call_id (now that Pass 1 events are in ledger).
    call_orphans = conn.execute(
        """
        SELECT orphan_id, event_id, event_type, workflow_id, call_id,
               phone_hash, occurred_at, payload_json, ingested_at
        FROM orphan_events
        WHERE phone_hash IS NULL AND event_type = 'disposition.override'
        ORDER BY orphan_id
        """
    ).fetchall()

    for row in call_orphans:
        lead_id = resolve_lead_id_by_call(conn, row["workflow_id"], row["call_id"])
        if lead_id is not None:
            if _promote_orphan(conn, row, lead_id, registry):
                unknown_count += 1
            reconciled_call += 1

    increment_quality_metric(conn, "unknown_disposition_events", unknown_count)

    remaining = conn.execute("SELECT COUNT(*) FROM orphan_events").fetchone()[0]
    set_quality_metric(conn, "orphan_events", int(remaining))

    return ReconciliationSummary(
        reconciled_by_phone=reconciled_phone,
        reconciled_by_call=reconciled_call,
        unknown_dispositions_found=unknown_count,
        remaining_orphans=int(remaining),
    )
