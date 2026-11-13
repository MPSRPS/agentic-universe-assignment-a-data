"""Phase 5 tests for cross-drop orphan event reconciliation."""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

import pytest

from solution.config import ENV_SECRET_KEY
from solution.db.connection import open_connection
from solution.pipeline import main


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


def test_events_arriving_before_lead_reconcile_including_overrides_and_unknown_disp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_SECRET_KEY, "phase5-reconcile-secret")
    db_file = tmp_path / "reconcile.db"

    # Drop 1 contains ONLY events:
    # - call_early for lead arriving later in drop_2 (including an override BEFORE call.initiated)
    # - an unknown disposition event for that same future lead
    # - a pure orphan whose lead never arrives
    drop1 = tmp_path / "drop_1"
    _write_jsonl(
        drop1 / "events" / "events_0001.jsonl",
        [
            json.dumps({
                "event_id": "evt_early_override",
                "event_type": "disposition.override",
                "workflow_id": "motor_renewal",
                "call_id": "call_early",
                "occurred_at": "2026-10-02T12:00:00Z",
                "payload": {"disposition_code": "renewed", "overridden_by": "sup_2", "reason": "qa"},
            }),
            json.dumps({
                "event_id": "evt_early_unknown_disp",
                "event_type": "disposition.set",
                "workflow_id": "motor_renewal",
                "call_id": "call_early",
                "occurred_at": "2026-10-01T10:05:00Z",
                "phone": "+91 98765 00001",
                "payload": {"disposition_code": "call_back"},
            }),
            json.dumps({
                "event_id": "evt_early_init",
                "event_type": "call.initiated",
                "workflow_id": "motor_renewal",
                "call_id": "call_early",
                "occurred_at": "2026-10-01T10:00:00Z",
                "phone": "9876500001",
                "payload": {},
            }),
            json.dumps({
                "event_id": "evt_pure_orphan",
                "event_type": "call.initiated",
                "workflow_id": "motor_renewal",
                "call_id": "call_never",
                "occurred_at": "2026-10-01T11:00:00Z",
                "phone": "9876599999",
                "payload": {},
            }),
        ],
    )

    assert main(["--db", str(db_file), "ingest", str(drop1)]) == 0
    conn = open_connection(db_file)
    try:
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM orphan_events").fetchone()[0] == 4
        dq1 = _quality_map(conn)
        assert dq1["orphan_events"] == 4
        # Unknown disposition on quarantined orphan is not counted until linked
        assert dq1["unknown_disposition_events"] == 0
    finally:
        conn.close()

    # Drop 2 delivers the missing lead for 9876500001.
    drop2 = tmp_path / "drop_2"
    _write_csv(
        drop2 / "leads" / "motor_renewal" / "batch_003.csv",
        ["customer_name", "phone", "policy_number"],
        [["Diya Patel", "09876500001", "MOT/777"]],
    )

    assert main(["--db", str(db_file), "ingest", str(drop2)]) == 0
    conn = open_connection(db_file)
    try:
        events = conn.execute(
            "SELECT event_id, lead_id, is_ignored FROM events ORDER BY event_id"
        ).fetchall()
        assert [(e["event_id"], e["is_ignored"]) for e in events] == [
            ("evt_early_init", 0),
            ("evt_early_override", 0),
            ("evt_early_unknown_disp", 1),
        ]
        # All 3 linked to the single newly-arrived lead
        assert len({e["lead_id"] for e in events}) == 1

        remaining = conn.execute("SELECT event_id FROM orphan_events").fetchall()
        assert [r["event_id"] for r in remaining] == ["evt_pure_orphan"]

        dq2 = _quality_map(conn)
        assert dq2["orphan_events"] == 1
        assert dq2["unknown_disposition_events"] == 1
    finally:
        conn.close()


def test_sequential_drop1_then_drop2_matches_combined_ingest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_SECRET_KEY, "phase5-equivalence-secret")

    drop1 = tmp_path / "seq" / "drop_1"
    drop2 = tmp_path / "seq" / "drop_2"
    combined = tmp_path / "combined"

    _write_csv(
        drop1 / "leads" / "motor_renewal" / "batch_001.csv",
        ["customer_name", "phone", "policy_number"],
        [["Aarav", "9876543210", "MOT/1"]],
    )
    _write_csv(
        drop2 / "leads" / "motor_renewal" / "batch_003.csv",
        ["customer_name", "phone", "policy_number"],
        [["Diya", "9876543211", "MOT/2"]],
    )
    ev1 = [
        json.dumps({
            "event_id": "e1",
            "event_type": "call.initiated",
            "workflow_id": "motor_renewal",
            "call_id": "c1",
            "occurred_at": "2026-10-01T10:00:00Z",
            "phone": "9876543210",
            "payload": {},
        }),
        json.dumps({
            "event_id": "e2",
            "event_type": "call.initiated",
            "workflow_id": "motor_renewal",
            "call_id": "c2",
            "occurred_at": "2026-10-01T11:00:00Z",
            "phone": "9876543211",
            "payload": {},
        }),
    ]
    ev2 = [
        json.dumps({
            "event_id": "e3",
            "event_type": "disposition.override",
            "workflow_id": "motor_renewal",
            "call_id": "c2",
            "occurred_at": "2026-10-08T10:00:00Z",
            "payload": {"disposition_code": "renewed"},
        }),
        ev1[0],  # duplicate across drops
    ]
    _write_jsonl(drop1 / "events" / "events_0001.jsonl", ev1)
    _write_jsonl(drop2 / "events" / "events_1001.jsonl", ev2)

    # Combined single drop containing all batch/event files in filename order
    _write_csv(
        combined / "leads" / "motor_renewal" / "batch_001.csv",
        ["customer_name", "phone", "policy_number"],
        [["Aarav", "9876543210", "MOT/1"]],
    )
    _write_csv(
        combined / "leads" / "motor_renewal" / "batch_003.csv",
        ["customer_name", "phone", "policy_number"],
        [["Diya", "9876543211", "MOT/2"]],
    )
    _write_jsonl(combined / "events" / "events_0001.jsonl", ev1)
    _write_jsonl(combined / "events" / "events_1001.jsonl", ev2)

    db_seq = tmp_path / "seq.db"
    db_comb = tmp_path / "comb.db"

    assert main(["--db", str(db_seq), "ingest", str(drop1)]) == 0
    assert main(["--db", str(db_seq), "ingest", str(drop2)]) == 0
    assert main(["--db", str(db_comb), "ingest", str(combined)]) == 0

    c_seq = open_connection(db_seq)
    c_comb = open_connection(db_comb)
    try:
        ev_sql = "SELECT event_id, event_type, workflow_id, call_id, occurred_at, payload_json, is_ignored FROM events ORDER BY event_id"
        assert [tuple(r) for r in c_seq.execute(ev_sql).fetchall()] == [
            tuple(r) for r in c_comb.execute(ev_sql).fetchall()
        ]
        assert _quality_map(c_seq) == _quality_map(c_comb)
    finally:
        c_seq.close()
        c_comb.close()
