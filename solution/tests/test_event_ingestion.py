"""Phase 5 tests for JSONL event ingestion, dedup, linking, unknown dispositions, and PII."""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

import pytest

from solution.config import ENV_SECRET_KEY, load_config
from solution.db.connection import open_connection
from solution.ingestion.discover import discover_event_files
from solution.ingestion.events import parse_event_timestamp_utc
from solution.pipeline import main
from solution.security.pii import PIIProtector


def _write_csv(path: Path, headers: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for row in rows:
            writer.writerow(row)


def _write_jsonl(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _quality_map(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT metric_name, metric_value FROM data_quality").fetchall()
    return {r["metric_name"]: r["metric_value"] for r in rows}


def test_discover_event_files_sorted_and_timestamp_parsing(tmp_path: Path) -> None:
    drop = tmp_path / "drop_1"
    for name in ("events_0003.jsonl", "events_0001.jsonl", "events_0002.jsonl"):
        p = drop / "events" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")

    assert [p.name for p in discover_event_files(drop)] == [
        "events_0001.jsonl",
        "events_0002.jsonl",
        "events_0003.jsonl",
    ]

    # UTC, offset (+05:30), and naive (IST -> +05:30) all normalize to UTC ISO-8601
    assert parse_event_timestamp_utc("2026-10-05T13:20:21Z") == "2026-10-05T13:20:21Z"
    assert parse_event_timestamp_utc("2026-10-01T10:40:19+05:30") == "2026-10-01T05:10:19Z"
    assert parse_event_timestamp_utc("2026-10-03 10:01:00") == "2026-10-03T04:31:00Z"
    assert parse_event_timestamp_utc("not-a-timestamp") is None


def test_event_ingestion_malformed_dedup_linking_unknown_disp_and_pii(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_SECRET_KEY, "phase5-event-secret")
    db_file = tmp_path / "events.db"

    drop1 = tmp_path / "drop_1"
    _write_csv(
        drop1 / "leads" / "motor_renewal" / "batch_001.csv",
        ["customer_name", "phone", "policy_number"],
        [["Aarav Sharma", "9876543210", "MOT/101"]],
    )
    _write_jsonl(
        drop1 / "events" / "events_0001.jsonl",
        [
            "",  # empty line -> skipped, not counted in events_total
            '{"event_id": "evt_01", "event_type": "call.initiated", "workflow_id": "motor_renewal", ',  # malformed JSON
            json.dumps({"event_type": "call.initiated", "workflow_id": "motor_renewal", "call_id": "c1", "occurred_at": "2026-10-01T10:00:00Z"}),  # missing event_id -> malformed
            # Shuffled: override appears BEFORE call.initiated in the same file
            json.dumps({
                "event_id": "evt_override_1",
                "event_type": "disposition.override",
                "workflow_id": "motor_renewal",
                "call_id": "call_1",
                "occurred_at": "2026-10-01 16:00:00",
                "payload": {"disposition_code": "renewed", "overridden_by": "sup_1", "reason": "qa", "phone": "+919876543210"},
            }),
            json.dumps({
                "event_id": "evt_init_1",
                "event_type": "call.initiated",
                "workflow_id": "motor_renewal",
                "call_id": "call_1",
                "occurred_at": "2026-10-01T10:00:00Z",
                "phone": "09876543210",
                "payload": {"customer_name": "Aarav Sharma"},
            }),
            json.dumps({
                "event_id": "evt_disp_bad",
                "event_type": "disposition.set",
                "workflow_id": "motor_renewal",
                "call_id": "call_1",
                "occurred_at": "2026-10-01T10:05:00Z",
                "phone": "+91 98765 43210",
                "payload": {"disposition_code": "RENEWD"},
            }),
            json.dumps({
                "event_id": "evt_disp_good",
                "event_type": "disposition.set",
                "workflow_id": "motor_renewal",
                "call_id": "call_1",
                "occurred_at": "2026-10-01T10:03:00Z",
                "phone": "91-98765-43210",
                "payload": {"disposition_code": "callback_requested"},
            }),
            # Duplicate within same drop
            json.dumps({
                "event_id": "evt_init_1",
                "event_type": "call.initiated",
                "workflow_id": "motor_renewal",
                "call_id": "call_1",
                "occurred_at": "2026-10-01T10:00:00Z",
                "phone": "9876543210",
                "payload": {},
            }),
            # Orphan with valid phone never uploaded + PII in payload
            json.dumps({
                "event_id": "evt_orphan_1",
                "event_type": "call.initiated",
                "workflow_id": "motor_renewal",
                "call_id": "call_999",
                "occurred_at": "2026-10-01T11:00:00Z",
                "phone": "9999988888",
                "payload": {"phone": "9999988888"},
            }),
            # Event with invalid phone -> cannot link -> orphan without storing bad phone
            json.dumps({
                "event_id": "evt_bad_phone",
                "event_type": "call.initiated",
                "workflow_id": "motor_renewal",
                "call_id": "call_888",
                "occurred_at": "2026-10-01T11:05:00Z",
                "phone": "022-2123-4567",
                "payload": {},
            }),
        ],
    )

    drop2 = tmp_path / "drop_2"
    _write_jsonl(
        drop2 / "events" / "events_1001.jsonl",
        [
            # Cross-drop duplicate of unknown disposition (must count as duplicate, NOT unknown_disposition again)
            json.dumps({
                "event_id": "evt_disp_bad",
                "event_type": "disposition.set",
                "workflow_id": "motor_renewal",
                "call_id": "call_1",
                "occurred_at": "2026-10-01T10:05:00Z",
                "phone": "9876543210",
                "payload": {"disposition_code": "RENEWD"},
            }),
            # Cross-drop duplicate of quarantined orphan
            json.dumps({
                "event_id": "evt_orphan_1",
                "event_type": "call.initiated",
                "workflow_id": "motor_renewal",
                "call_id": "call_999",
                "occurred_at": "2026-10-01T11:00:00Z",
                "phone": "9999988888",
                "payload": {},
            }),
        ],
    )

    assert main(["--db", str(db_file), "ingest", str(drop1)]) == 0
    assert main(["--db", str(db_file), "ingest", str(drop2)]) == 0

    conn = open_connection(db_file)
    try:
        dq = _quality_map(conn)
        assert dq["events_total"] == 11
        assert dq["malformed_events"] == 2
        assert dq["duplicate_events_dropped"] == 3
        assert dq["unknown_disposition_events"] == 1
        assert dq["orphan_events"] == 2

        events = conn.execute(
            "SELECT event_id, event_type, is_ignored, payload_json, occurred_at FROM events ORDER BY event_id"
        ).fetchall()
        assert {e["event_id"]: e["is_ignored"] for e in events} == {
            "evt_disp_bad": 1,
            "evt_disp_good": 0,
            "evt_init_1": 0,
            "evt_override_1": 0,
        }
        # Append-only: both disposition.set and disposition.override remain in ledger.
        # Verify override timestamp converted from naive IST 16:00:00 -> UTC 10:30:00Z.
        by_id = {e["event_id"]: e for e in events}
        assert by_id["evt_override_1"]["occurred_at"] == "2026-10-01T10:30:00Z"

        # Verify zero plaintext PII in events and orphan_events.
        for table in ("events", "orphan_events"):
            dumped = json.dumps([dict(r) for r in conn.execute(f"SELECT * FROM {table}").fetchall()])
            for sensitive in ("Aarav", "Sharma", "9876543210", "9999988888", "022-2123-4567"):
                assert sensitive not in dumped

        # Re-running already ingested drop1 changes nothing.
        before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        before_dq = _quality_map(conn)
    finally:
        conn.close()

    assert main(["--db", str(db_file), "ingest", str(drop1)]) == 0
    conn = open_connection(db_file)
    try:
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == before
        assert _quality_map(conn) == before_dq
    finally:
        conn.close()


def test_failed_event_ingest_rolls_back_and_retries_without_skipping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_SECRET_KEY, "phase5-rollback-secret")
    db_file = tmp_path / "rollback_events.db"
    drop1 = tmp_path / "drop_1"
    _write_csv(
        drop1 / "leads" / "motor_renewal" / "batch_001.csv",
        ["customer_name", "phone", "policy_number"],
        [["Aarav", "9876543210", "MOT/1"]],
    )
    _write_jsonl(
        drop1 / "events" / "events_0001.jsonl",
        [
            json.dumps({
                "event_id": "evt_1",
                "event_type": "call.initiated",
                "workflow_id": "motor_renewal",
                "call_id": "call_1",
                "occurred_at": "2026-10-01T10:00:00Z",
                "phone": "9876543210",
                "payload": {},
            }),
        ],
    )

    def _boom(*args, **kwargs):
        raise RuntimeError("reconcile crash")

    monkeypatch.setattr("solution.pipeline.reconcile_orphans", _boom)
    with pytest.raises(RuntimeError, match="reconcile crash"):
        main(["--db", str(db_file), "ingest", str(drop1)])

    conn = open_connection(db_file)
    try:
        assert conn.execute("SELECT COUNT(*) FROM drops_ingested").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
        assert _quality_map(conn)["events_total"] == 0
    finally:
        conn.close()

    monkeypatch.undo()
    monkeypatch.setenv(ENV_SECRET_KEY, "phase5-rollback-secret")
    assert main(["--db", str(db_file), "ingest", str(drop1)]) == 0

    conn = open_connection(db_file)
    try:
        assert conn.execute("SELECT COUNT(*) FROM drops_ingested").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        assert _quality_map(conn)["events_total"] == 1
    finally:
        conn.close()
