"""Phase 4 idempotency and transaction rollback tests for lead ingestion."""
from __future__ import annotations

import csv
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


def _snapshot_db(conn: sqlite3.Connection) -> dict[str, list[tuple]]:
    return {
        "leads": [
            tuple(r)
            for r in conn.execute(
                "SELECT workflow_id, phone_hash, batch_id, external_ref, extra_fields_json FROM leads ORDER BY lead_id"
            ).fetchall()
        ],
        "rejections": [
            tuple(r)
            for r in conn.execute(
                "SELECT workflow_id, batch_id, row_number, reason FROM rejections ORDER BY rejection_id"
            ).fetchall()
        ],
        "data_quality": [
            tuple(r)
            for r in conn.execute(
                "SELECT metric_name, metric_value FROM data_quality ORDER BY metric_name"
            ).fetchall()
        ],
        "drops_ingested": [
            tuple(r)
            for r in conn.execute(
                "SELECT drop_path FROM drops_ingested ORDER BY drop_path"
            ).fetchall()
        ],
    }


def test_reingesting_same_drop_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_SECRET_KEY, "idempotency-secret")
    db_file = tmp_path / "idem.db"
    drop1 = tmp_path / "drop_1"
    _write_csv(
        drop1 / "leads" / "motor_renewal" / "batch_001.csv",
        ["customer_name", "phone", "policy_number", "premium_amount"],
        [
            ["Aarav", "9876543210", "MOT/1", "1000"],
            ["Bad", "12345", "MOT/2", "500"],
            ["Aarav", "+91 98765 43210", "MOT/1", "bad_decimal"],
        ],
    )

    assert main(["--db", str(db_file), "ingest", str(drop1)]) == 0
    conn = open_connection(db_file)
    try:
        first_state = _snapshot_db(conn)
    finally:
        conn.close()

    # Re-run ingest on the same drop (even with non-canonical relative/dot path)
    assert main(["--db", str(db_file), "ingest", str(drop1 / ".")]) == 0
    conn = open_connection(db_file)
    try:
        second_state = _snapshot_db(conn)
    finally:
        conn.close()

    assert first_state == second_state


def test_failed_ingest_rolls_back_completely_and_allows_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_SECRET_KEY, "rollback-secret")
    db_file = tmp_path / "rollback.db"
    drop1 = tmp_path / "drop_1"
    _write_csv(
        drop1 / "leads" / "motor_renewal" / "batch_001.csv",
        ["customer_name", "phone", "policy_number"],
        [["Aarav", "9876543210", "MOT/1"]],
    )

    def _boom(*args, **kwargs):
        raise RuntimeError("disk failure")

    monkeypatch.setattr("solution.pipeline.mark_drop_ingested", _boom)
    with pytest.raises(RuntimeError, match="disk failure"):
        main(["--db", str(db_file), "ingest", str(drop1)])

    conn = open_connection(db_file)
    try:
        assert conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM drops_ingested").fetchone()[0] == 0
        assert (
            conn.execute(
                "SELECT metric_value FROM data_quality WHERE metric_name='lead_rows_total'"
            ).fetchone()[0]
            == 0
        )
    finally:
        conn.close()

    # Restore and retry cleanly
    monkeypatch.undo()
    monkeypatch.setenv(ENV_SECRET_KEY, "rollback-secret")
    assert main(["--db", str(db_file), "ingest", str(drop1)]) == 0
    conn = open_connection(db_file)
    try:
        assert conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM drops_ingested").fetchone()[0] == 1
    finally:
        conn.close()
