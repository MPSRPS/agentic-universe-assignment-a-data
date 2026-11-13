"""Phase 6 unit & integration tests for effective disposition rules."""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

import pytest

from solution.config import ENV_SECRET_KEY, IST
from solution.pipeline import main
from solution.reporting.ledger_facts import compute_effective_disposition


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


def test_compute_effective_disposition_precedence_and_tiebreak() -> None:
    t1 = datetime(2026, 10, 1, 10, 0, 0, tzinfo=IST)
    t2 = datetime(2026, 10, 1, 11, 0, 0, tzinfo=IST)
    t3 = datetime(2026, 10, 1, 12, 0, 0, tzinfo=IST)

    # No disposition events
    assert compute_effective_disposition([]) is None

    # Latest disposition.set wins when no override exists
    assert (
        compute_effective_disposition(
            [
                ("disposition.set", t1, "evt_1", "callback_requested"),
                ("disposition.set", t2, "evt_2", "payment_link_sent"),
            ]
        )
        == "payment_link_sent"
    )

    # Override beats disposition.set regardless of occurred_at comparison
    assert (
        compute_effective_disposition(
            [
                ("disposition.set", t3, "evt_3", "not_interested"),
                ("disposition.override", t1, "evt_1", "renewed"),
            ]
        )
        == "renewed"
    )

    # Latest override by occurred_at wins among multiple overrides
    assert (
        compute_effective_disposition(
            [
                ("disposition.set", t1, "evt_1", "callback_requested"),
                ("disposition.override", t2, "evt_2", "not_interested"),
                ("disposition.override", t3, "evt_3", "renewed"),
            ]
        )
        == "renewed"
    )

    # Deterministic tiebreak by event_id when timestamps match
    assert (
        compute_effective_disposition(
            [
                ("disposition.override", t2, "evt_002", "renewed"),
                ("disposition.override", t2, "evt_001", "not_interested"),
            ]
        )
        == "renewed"
    )


def test_ignored_unknown_disposition_excluded_from_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_SECRET_KEY, "phase6-disp-secret")
    db_file = tmp_path / "disp.db"
    drop = tmp_path / "drop_1"
    out_dir = tmp_path / "reports"

    _write_csv(
        drop / "leads" / "motor_renewal" / "batch_001.csv",
        ["customer_name", "phone", "policy_number"],
        [
            ["Aarav", "9876543210", "MOT/1"],
            ["Diya", "9876543211", "MOT/2"],
        ],
    )
    _write_jsonl(
        drop / "events" / "events_0001.jsonl",
        [
            # Lead 1 has valid set followed by an ignored unknown disposition -> valid set remains effective
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
                "event_type": "disposition.set",
                "workflow_id": "motor_renewal",
                "call_id": "c1",
                "occurred_at": "2026-10-01T10:02:00Z",
                "phone": "9876543210",
                "payload": {"disposition_code": "callback_requested"},
            }),
            json.dumps({
                "event_id": "e3",
                "event_type": "disposition.override",
                "workflow_id": "motor_renewal",
                "call_id": "c1",
                "occurred_at": "2026-10-01T11:00:00Z",
                "payload": {"disposition_code": "RENEWD"},
            }),
            # Lead 2 has only an unknown disposition -> no effective disposition
            json.dumps({
                "event_id": "e4",
                "event_type": "call.initiated",
                "workflow_id": "motor_renewal",
                "call_id": "c2",
                "occurred_at": "2026-10-01T10:00:00Z",
                "phone": "9876543211",
                "payload": {},
            }),
            json.dumps({
                "event_id": "e5",
                "event_type": "disposition.set",
                "workflow_id": "motor_renewal",
                "call_id": "c2",
                "occurred_at": "2026-10-01T10:02:00Z",
                "phone": "9876543211",
                "payload": {"disposition_code": "unknown"},
            }),
        ],
    )

    assert main(["--db", str(db_file), "ingest", str(drop)]) == 0
    assert main(["--db", str(db_file), "report", "--out", str(out_dir)]) == 0

    with (out_dir / "lead_report.csv").open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 2
    assert rows[0]["external_ref"] == "MOT/1"
    assert rows[0]["current_disposition"] == "callback_requested"
    assert rows[0]["bucket"] == "in_progress_dialed"
    assert rows[1]["external_ref"] == "MOT/2"
    assert rows[1]["current_disposition"] == ""
    assert rows[1]["bucket"] == "in_progress_dialed"
