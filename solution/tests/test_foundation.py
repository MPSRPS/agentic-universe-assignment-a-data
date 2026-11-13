"""Phase 1 foundation tests: DB schema, transactions, schema loader, and CLI."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from solution.config import (
    DEFAULT_SCHEMAS_DIR,
    ENV_FERNET_KEY,
    ENV_HMAC_KEY,
    ENV_SECRET_KEY,
    IST,
    ConfigurationError,
    load_config,
)
from solution.db.connection import transaction
from solution.db.schema import DATA_QUALITY_METRICS
from solution.models.schema_loader import SchemaRegistry
from solution.pipeline import main


def test_db_tables_and_columns_initialized(db_conn: sqlite3.Connection) -> None:
    tables = {
        row["name"]
        for row in db_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {
        "drops_ingested",
        "leads",
        "events",
        "orphan_events",
        "rejections",
        "data_quality",
    }.issubset(tables)

    orphan_cols = {
        row["name"]
        for row in db_conn.execute("PRAGMA table_info(orphan_events)").fetchall()
    }
    assert "raw_json" not in orphan_cols

    event_cols = {
        row["name"]
        for row in db_conn.execute("PRAGMA table_info(events)").fetchall()
    }
    assert "is_ignored" in event_cols


def test_data_quality_metrics_initialized_to_zero(db_conn: sqlite3.Connection) -> None:
    rows = db_conn.execute("SELECT metric_name, metric_value FROM data_quality").fetchall()
    metrics = {row["metric_name"]: row["metric_value"] for row in rows}
    assert set(metrics.keys()) == set(DATA_QUALITY_METRICS)
    assert all(val == 0 for val in metrics.values())


def test_leads_unique_per_workflow_and_phone_hash(db_conn: sqlite3.Connection) -> None:
    lead_cols = {
        row["name"]
        for row in db_conn.execute("PRAGMA table_info(leads)").fetchall()
    }
    assert "phone_last4_encrypted" in lead_cols
    assert "phone_last4" not in lead_cols

    db_conn.execute(
        """
        INSERT INTO leads (
            workflow_id, phone_hash, phone_last4_encrypted, customer_name_encrypted,
            batch_id, external_ref, extra_fields_json, created_at, updated_at
        ) VALUES ('motor_renewal', 'hash1', 'enc_last4', 'enc', 'batch_001', 'MOT/1', '{}', 't', 't')
        """
    )
    # Same phone_hash in a different workflow must succeed.
    db_conn.execute(
        """
        INSERT INTO leads (
            workflow_id, phone_hash, phone_last4_encrypted, customer_name_encrypted,
            batch_id, external_ref, extra_fields_json, created_at, updated_at
        ) VALUES ('policy_feedback', 'hash1', 'enc_last4', 'enc', 'batch_001', 'ORD-1', '{}', 't', 't')
        """
    )
    # Same (workflow_id, phone_hash) must fail.
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            """
            INSERT INTO leads (
                workflow_id, phone_hash, phone_last4_encrypted, customer_name_encrypted,
                batch_id, external_ref, extra_fields_json, created_at, updated_at
            ) VALUES ('motor_renewal', 'hash1', 'enc_last4', 'enc', 'batch_002', 'MOT/2', '{}', 't', 't')
            """
        )


def test_events_foreign_key_enforced(db_conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            """
            INSERT INTO events (
                event_id, event_type, workflow_id, call_id, lead_id,
                occurred_at, payload_json, is_ignored, ingested_at
            ) VALUES ('evt_1', 'call.initiated', 'motor_renewal', 'call_1', 9999, 't', '{}', 0, 't')
            """
        )


def test_transaction_rolls_back_on_error(db_conn: sqlite3.Connection) -> None:
    with pytest.raises(RuntimeError):
        with transaction(db_conn):
            db_conn.execute(
                "INSERT INTO drops_ingested (drop_path, ingested_at) VALUES ('/tmp/d1', 't')"
            )
            raise RuntimeError("simulated failure")

    count = db_conn.execute("SELECT COUNT(*) FROM drops_ingested").fetchone()[0]
    assert count == 0


def test_load_schemas_and_dispositions(schema_registry: SchemaRegistry) -> None:
    assert set(schema_registry.workflows.keys()) == {"motor_renewal", "policy_feedback"}

    mr = schema_registry.get_workflow("motor_renewal")
    assert mr.reference_field == "policy_number"
    assert mr.pii_fields == frozenset({"customer_name", "phone"})
    assert [f.name for f in mr.fields] == [
        "customer_name",
        "phone",
        "policy_number",
        "policy_expiry",
        "premium_amount",
        "preferred_language",
    ]
    assert mr.fields[-1].default == "en"

    pf = schema_registry.get_workflow("policy_feedback")
    assert pf.reference_field == "order_id"
    assert pf.pii_fields == frozenset({"customer_name", "phone"})

    renewed = schema_registry.get_disposition("motor_renewal", "renewed")
    assert renewed is not None
    assert renewed.is_terminal is True
    assert renewed.is_success is True

    callback = schema_registry.get_disposition("motor_renewal", "callback_requested")
    assert callback is not None
    assert callback.is_terminal is False
    assert callback.is_success is False

    assert schema_registry.get_disposition("motor_renewal", "RENEWD") is None
    assert schema_registry.get_disposition("motor_renewal", "unknown") is None


def test_config_defaults_and_ist_offset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_HMAC_KEY, raising=False)
    monkeypatch.delenv(ENV_FERNET_KEY, raising=False)
    monkeypatch.setenv(ENV_SECRET_KEY, "test-master-secret")

    cfg = load_config()
    assert cfg.schemas_dir == DEFAULT_SCHEMAS_DIR
    assert IST.utcoffset(None).total_seconds() == 5.5 * 3600


def test_cli_ingest_and_report_smoke(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_HMAC_KEY, raising=False)
    monkeypatch.delenv(ENV_FERNET_KEY, raising=False)
    monkeypatch.setenv(ENV_SECRET_KEY, "test-master-secret")

    db_file = tmp_path / "cli.db"
    drop_dir = tmp_path / "drop_1"
    drop_dir.mkdir()
    out_dir = tmp_path / "reports"

    assert main(["--db", str(db_file), "ingest", str(drop_dir)]) == 0
    assert db_file.is_file()
    assert main(["--db", str(db_file), "report", "--out", str(out_dir)]) == 0


def test_cli_fails_clearly_when_keys_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(ENV_SECRET_KEY, raising=False)
    monkeypatch.delenv(ENV_HMAC_KEY, raising=False)
    monkeypatch.delenv(ENV_FERNET_KEY, raising=False)

    drop_dir = tmp_path / "drop_1"
    drop_dir.mkdir()

    with pytest.raises(ConfigurationError):
        load_config()

    exit_code = main(["--db", str(tmp_path / "cli.db"), "ingest", str(drop_dir)])
    assert exit_code == 2
    assert "Configuration error" in capsys.readouterr().err
