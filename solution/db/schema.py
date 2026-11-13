"""Database schema DDL and initialization."""
from __future__ import annotations

import sqlite3

DATA_QUALITY_METRICS: tuple[str, ...] = (
    "lead_rows_total",
    "lead_rows_rejected",
    "duplicate_lead_rows_merged",
    "field_warnings",
    "events_total",
    "malformed_events",
    "duplicate_events_dropped",
    "orphan_events",
    "unknown_disposition_events",
)

DDL_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS drops_ingested (
        drop_path   TEXT PRIMARY KEY,
        ingested_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS leads (
        lead_id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        workflow_id             TEXT NOT NULL,
        phone_hash              TEXT NOT NULL,
        phone_last4_encrypted   TEXT NOT NULL,
        customer_name_encrypted TEXT NOT NULL,
        batch_id                TEXT NOT NULL,
        external_ref            TEXT NOT NULL,
        extra_fields_json       TEXT NOT NULL DEFAULT '{}',
        created_at              TEXT NOT NULL,
        updated_at              TEXT NOT NULL,
        UNIQUE(workflow_id, phone_hash)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_leads_workflow_phone
        ON leads (workflow_id, phone_hash)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_leads_workflow_ref
        ON leads (workflow_id, external_ref)
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        event_row_id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id     TEXT NOT NULL UNIQUE,
        event_type   TEXT NOT NULL,
        workflow_id  TEXT NOT NULL,
        call_id      TEXT NOT NULL,
        lead_id      INTEGER NOT NULL REFERENCES leads(lead_id),
        occurred_at  TEXT NOT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}',
        is_ignored   INTEGER NOT NULL DEFAULT 0,
        ingested_at  TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_events_lead_id
        ON events (lead_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_events_call_id
        ON events (call_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_events_workflow_call
        ON events (workflow_id, call_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS orphan_events (
        orphan_id    INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id     TEXT NOT NULL UNIQUE,
        event_type   TEXT NOT NULL,
        workflow_id  TEXT NOT NULL,
        call_id      TEXT NOT NULL,
        phone_hash   TEXT,
        occurred_at  TEXT NOT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}',
        ingested_at  TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_orphan_phone
        ON orphan_events (workflow_id, phone_hash)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_orphan_call
        ON orphan_events (workflow_id, call_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS rejections (
        rejection_id INTEGER PRIMARY KEY AUTOINCREMENT,
        workflow_id  TEXT NOT NULL,
        batch_id     TEXT NOT NULL,
        row_number   INTEGER NOT NULL,
        reason       TEXT NOT NULL,
        UNIQUE(workflow_id, batch_id, row_number)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_rejections_sort
        ON rejections (workflow_id, batch_id, row_number)
    """,
    """
    CREATE TABLE IF NOT EXISTS data_quality (
        metric_name  TEXT PRIMARY KEY,
        metric_value INTEGER NOT NULL DEFAULT 0
    )
    """,
)


def init_db(conn: sqlite3.Connection) -> None:
    """Create all tables, indexes, and initial data quality counters if missing."""
    for stmt in DDL_STATEMENTS:
        conn.execute(stmt)
    for metric in DATA_QUALITY_METRICS:
        conn.execute(
            "INSERT OR IGNORE INTO data_quality (metric_name, metric_value) VALUES (?, 0)",
            (metric,),
        )
    conn.commit()
