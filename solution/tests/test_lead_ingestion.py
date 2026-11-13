"""Phase 4 integration tests for lead CSV discovery, validation, merging, and PII safety."""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

import pytest

from solution.config import ENV_SECRET_KEY, load_config
from solution.db.connection import open_connection
from solution.db.schema import init_db
from solution.ingestion.discover import discover_lead_files
from solution.ingestion.leads import ingest_drop_leads
from solution.models.schema_loader import SchemaRegistry
from solution.pipeline import main
from solution.security.pii import PIIProtector


def _write_csv(path: Path, headers: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for row in rows:
            writer.writerow(row)


def _make_protector(monkeypatch: pytest.MonkeyPatch) -> PIIProtector:
    monkeypatch.setenv(ENV_SECRET_KEY, "phase4-test-secret")
    return PIIProtector.from_config(load_config())


def _quality_map(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT metric_name, metric_value FROM data_quality").fetchall()
    return {r["metric_name"]: r["metric_value"] for r in rows}


def test_discover_lead_files_deterministic_order(
    tmp_path: Path, schema_registry: SchemaRegistry
) -> None:
    drop = tmp_path / "drop_1"
    for rel in [
        "leads/policy_feedback/batch_002.csv",
        "leads/motor_renewal/batch_002.csv",
        "leads/motor_renewal/batch_001.csv",
        "leads/policy_feedback/batch_001.csv",
    ]:
        p = drop / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("h\n", encoding="utf-8")

    discovered = discover_lead_files(drop, schema_registry)
    assert [(lf.workflow_id, lf.batch_id) for lf in discovered] == [
        ("motor_renewal", "batch_001"),
        ("motor_renewal", "batch_002"),
        ("policy_feedback", "batch_001"),
        ("policy_feedback", "batch_002"),
    ]


def test_blank_rows_skipped_and_rejection_schema_order(
    tmp_path: Path,
    db_conn: sqlite3.Connection,
    schema_registry: SchemaRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protector = _make_protector(monkeypatch)
    drop = tmp_path / "drop_1"
    _write_csv(
        drop / "leads" / "motor_renewal" / "batch_001.csv",
        [
            "customer_name",
            "phone",
            "policy_number",
            "policy_expiry",
            "premium_amount",
            "preferred_language",
        ],
        [
            # Blank row -> skipped and NOT counted
            ["", "   ", "", "", "", ""],
            # Row 1: missing name AND bad phone + bad optional date -> first failure: missing_required:customer_name, 0 warnings
            ["  ", "12345", "MOT/1", "31/02/2026", "", ""],
            # Row 2: missing phone -> missing_required:phone
            ["Aarav Sharma", "", "MOT/2", "", "", ""],
            # Row 3: invalid phone -> invalid_phone
            ["Priya Nair", "5123456789", "MOT/3", "", "", ""],
            # Row 4: missing policy_number -> missing_required:policy_number
            ["Rohan Das", "9876543210", "   ", "", "", ""],
            # Blank row in middle -> skipped
            [" ", "", "", "", "", ""],
            # Row 5: valid with 3 invalid optional fields -> accepted, 3 field warnings, default lang='en'
            ["Meera Iyer", "09876543210", "MOT/5", "31/02/2026", "twelve thousand", "Hindi"],
        ],
    )

    summary = ingest_drop_leads(db_conn, drop, schema_registry, protector)
    assert summary.files_processed == 1
    assert summary.rows_total == 5
    assert summary.rows_rejected == 4
    assert summary.duplicates_merged == 0
    assert summary.field_warnings == 3

    rejs = db_conn.execute(
        "SELECT workflow_id, batch_id, row_number, reason FROM rejections ORDER BY row_number"
    ).fetchall()
    assert [(r["row_number"], r["reason"]) for r in rejs] == [
        (1, "missing_required:customer_name"),
        (2, "missing_required:phone"),
        (3, "invalid_phone"),
        (4, "missing_required:policy_number"),
    ]

    lead = db_conn.execute("SELECT * FROM leads").fetchone()
    assert lead is not None
    assert lead["batch_id"] == "batch_001"
    assert lead["external_ref"] == "MOT/5"
    assert protector.decrypt_name(lead["customer_name_encrypted"]) == "Meera Iyer"
    assert protector.mask_phone_from_encrypted_last4(lead["phone_last4_encrypted"]) == "+91******3210"
    assert json.loads(lead["extra_fields_json"]) == {
        "policy_expiry": None,
        "preferred_language": "en",
        "premium_amount": None,
    }


def test_duplicate_merging_preserves_first_batch_and_applies_defaults_after_merge(
    tmp_path: Path,
    db_conn: sqlite3.Connection,
    schema_registry: SchemaRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protector = _make_protector(monkeypatch)

    drop1 = tmp_path / "drop_1"
    _write_csv(
        drop1 / "leads" / "motor_renewal" / "batch_001.csv",
        ["customer_name", "phone", "policy_number", "policy_expiry", "premium_amount", "preferred_language"],
        [
            # Lead A: language missing, premium set
            ["Aarav", "9876543210", "MOT/100", "2026-11-15", "₹10,000", ""],
            # Lead B: language explicitly 'ta', expiry set
            ["Kavya", "9123456789", "MOT/200", "15/12/2026", "5000", "TA"],
        ],
    )
    # Same phone in policy_feedback -> separate lead, NOT a duplicate
    _write_csv(
        drop1 / "leads" / "policy_feedback" / "batch_001.csv",
        ["customer_name", "phone", "order_id", "product", "order_date", "preferred_language"],
        [
            ["Aarav", "+91 98765 43210", "ORD-1", "Phone", "01-Oct-2026", ""],
        ],
    )

    drop2 = tmp_path / "drop_2"
    # Aliased headers + extra ignored column
    _write_csv(
        drop2 / "leads" / "motor_renewal" / "batch_003.csv",
        ["Language", "Insured Name", "Policy No", "Mobile No", "Expiry Date", "Premium", "agent_notes"],
        [
            # Lead A updated: language 'mr' overwrites missing, premium null does NOT overwrite 10000.00
            ["mr", "Aarav Sharma", "MOT/100", "+91 98765 43210", "", "", "VIP"],
            # Lead B updated: invalid language -> None (1 warning), must NOT overwrite 'ta'!
            ["Tamil", "Kavya Nair", "MOT/200", "09123456789", "", "INR 7500.5", ""],
        ],
    )

    s1 = ingest_drop_leads(db_conn, drop1, schema_registry, protector)
    s2 = ingest_drop_leads(db_conn, drop2, schema_registry, protector)

    assert s1.rows_total == 3 and s1.duplicates_merged == 0
    assert s2.rows_total == 2 and s2.duplicates_merged == 2 and s2.field_warnings == 1

    leads = db_conn.execute(
        "SELECT * FROM leads ORDER BY workflow_id, external_ref"
    ).fetchall()
    assert len(leads) == 3

    lead_a, lead_b, lead_pf = leads
    assert lead_a["batch_id"] == "batch_001"
    assert protector.decrypt_name(lead_a["customer_name_encrypted"]) == "Aarav Sharma"
    assert json.loads(lead_a["extra_fields_json"]) == {
        "policy_expiry": "2026-11-15",
        "preferred_language": "mr",
        "premium_amount": "10000.00",
    }

    assert lead_b["batch_id"] == "batch_001"
    assert protector.decrypt_name(lead_b["customer_name_encrypted"]) == "Kavya Nair"
    assert json.loads(lead_b["extra_fields_json"]) == {
        "policy_expiry": "2026-12-15",
        "preferred_language": "ta",
        "premium_amount": "7500.50",
    }

    assert lead_pf["workflow_id"] == "policy_feedback"
    assert json.loads(lead_pf["extra_fields_json"])["preferred_language"] == "en"

    metrics = _quality_map(db_conn)
    assert metrics["lead_rows_total"] == 5
    assert metrics["duplicate_lead_rows_merged"] == 2
    assert metrics["field_warnings"] == 1


def test_no_plaintext_pii_in_db_after_lead_ingestion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_SECRET_KEY, "phase4-pii-secret")
    db_file = tmp_path / "pii_check.db"
    drop = tmp_path / "drop_1"
    _write_csv(
        drop / "leads" / "motor_renewal" / "batch_001.csv",
        ["customer_name", "phone", "policy_number"],
        [
            ["Vikramaditya Rao", "91-98765-43210", "MOT/999"],
            ["Secret Rejected", "5123456789", "MOT/998"],
        ],
    )

    assert main(["--db", str(db_file), "ingest", str(drop)]) == 0

    conn = open_connection(db_file)
    try:
        for table in ("leads", "rejections"):
            rows = [dict(r) for r in conn.execute(f"SELECT * FROM {table}").fetchall()]
            dumped = json.dumps(rows)
            for pii in ("Vikramaditya", "Rao", "9876543210", "3210", "Secret Rejected", "5123456789"):
                assert pii not in dumped
    finally:
        conn.close()
