"""Tests for Phase 3 PII security: hashing, Fernet encryption, key config, and scrubbing."""
from __future__ import annotations

import json
import sqlite3

import pytest
from cryptography.fernet import Fernet, InvalidToken

from solution.config import (
    ENV_FERNET_KEY,
    ENV_HMAC_KEY,
    ENV_SECRET_KEY,
    ConfigurationError,
    load_config,
)
from solution.models.schema_loader import SchemaRegistry
from solution.security.pii import PIIProtector
from solution.validation.phone import normalize_phone


def _clear_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_SECRET_KEY, raising=False)
    monkeypatch.delenv(ENV_HMAC_KEY, raising=False)
    monkeypatch.delenv(ENV_FERNET_KEY, raising=False)


def test_missing_and_invalid_key_configurations_fail_clearly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_key_env(monkeypatch)
    with pytest.raises(ConfigurationError, match="Missing required PII security keys"):
        load_config()

    monkeypatch.setenv(ENV_SECRET_KEY, "   ")
    with pytest.raises(ConfigurationError, match="Missing required PII security keys"):
        load_config()

    _clear_key_env(monkeypatch)
    monkeypatch.setenv(ENV_HMAC_KEY, "only-hmac-set")
    with pytest.raises(ConfigurationError, match="Incomplete key configuration"):
        load_config()

    _clear_key_env(monkeypatch)
    monkeypatch.setenv(ENV_FERNET_KEY, Fernet.generate_key().decode("utf-8"))
    with pytest.raises(ConfigurationError, match="Incomplete key configuration"):
        load_config()

    monkeypatch.setenv(ENV_HMAC_KEY, "valid-hmac")
    monkeypatch.setenv(ENV_FERNET_KEY, "not-a-valid-fernet-key")
    with pytest.raises(ConfigurationError, match="Invalid AU_FERNET_KEY"):
        load_config()


def test_key_persistence_across_runs_via_master_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_key_env(monkeypatch)
    monkeypatch.setenv(ENV_SECRET_KEY, "persistent-test-secret-123")

    cfg1 = load_config()
    cfg2 = load_config()
    assert cfg1.hmac_key == cfg2.hmac_key
    assert cfg1.fernet_key == cfg2.fernet_key
    assert cfg1.hmac_key != cfg1.fernet_key

    p1 = PIIProtector.from_config(cfg1)
    p2 = PIIProtector.from_config(cfg2)
    token = p1.encrypt_name("Aarav Sharma")
    assert p2.decrypt_name(token) == "Aarav Sharma"
    assert p1.hash_phone("+919876543210") == p2.hash_phone("+919876543210")


def test_key_persistence_via_explicit_hmac_and_fernet_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_key_env(monkeypatch)
    fernet_key = Fernet.generate_key().decode("utf-8")
    monkeypatch.setenv(ENV_HMAC_KEY, "explicit-hmac-secret")
    monkeypatch.setenv(ENV_FERNET_KEY, fernet_key)

    cfg = load_config()
    protector = PIIProtector.from_config(cfg)
    enc_last4 = protector.encrypt_last4("+919876543210")
    assert protector.mask_phone_from_encrypted_last4(enc_last4) == "+91******3210"


def test_deterministic_phone_matching_across_formats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_key_env(monkeypatch)
    monkeypatch.setenv(ENV_SECRET_KEY, "matching-test-secret")
    protector = PIIProtector.from_config(load_config())

    p_lead = normalize_phone("+91 98765 43210")
    p_event = normalize_phone("09876543210")
    p_other = normalize_phone("91-98765-43211")
    assert p_lead is not None and p_event is not None and p_other is not None

    assert protector.hash_phone(p_lead) == protector.hash_phone(p_event)
    assert protector.hash_phone(p_lead) != protector.hash_phone(p_other)
    assert "9876543210" not in protector.hash_phone(p_lead)
    assert "3210" not in protector.hash_phone(p_lead)

    # Different key produces a different hash.
    other_protector = PIIProtector.from_config(load_config(secret_key="different-secret"))
    assert protector.hash_phone(p_lead) != other_protector.hash_phone(p_lead)


def test_fernet_encryption_decryption_and_tamper_detection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_key_env(monkeypatch)
    monkeypatch.setenv(ENV_SECRET_KEY, "fernet-test-secret")
    protector = PIIProtector.from_config(load_config())

    name = "Ananya Iyer"
    t1 = protector.encrypt_name(name)
    t2 = protector.encrypt_name(name)
    assert t1 != t2  # Random IV per encryption
    assert name not in t1
    assert protector.decrypt_name(t1) == name
    assert protector.decrypt_name(t2) == name

    enc_last4 = protector.encrypt_last4("+919876543210")
    assert "3210" not in enc_last4
    assert protector.decrypt_last4(enc_last4) == "3210"
    assert protector.mask_phone_from_encrypted_last4(enc_last4) == "+91******3210"

    # Tampered token must fail authentication.
    tampered = t1[:-4] + ("AAAA" if not t1.endswith("AAAA") else "BBBB")
    with pytest.raises(InvalidToken):
        protector.decrypt_name(tampered)


def test_sanitize_event_payload_removes_pii_and_keeps_operational_fields(
    schema_registry: SchemaRegistry,
) -> None:
    pii_fields = schema_registry.all_pii_field_names
    payload = {
        "disposition_code": "renewed",
        "phone": "+919876543210",
        "Customer_Name": "Aarav Sharma",
        "nested": {"phone": "9876543210", "reason": "qa_review"},
        "items": [{"customer_name": "Secret", "duration_sec": 42}],
    }
    sanitized = PIIProtector.sanitize_event_payload(payload, pii_fields)
    assert sanitized == {
        "disposition_code": "renewed",
        "nested": {"reason": "qa_review"},
        "items": [{"duration_sec": 42}],
    }


def test_no_plaintext_pii_in_stored_db_records(
    db_conn: sqlite3.Connection,
    schema_registry: SchemaRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_key_env(monkeypatch)
    monkeypatch.setenv(ENV_SECRET_KEY, "db-storage-check-secret")
    protector = PIIProtector.from_config(load_config())

    raw_name = "Zoya Kulkarni"
    canonical_phone = normalize_phone("91-87654-32109")
    assert canonical_phone is not None

    phone_hash = protector.hash_phone(canonical_phone)
    phone_last4_enc = protector.encrypt_last4(canonical_phone)
    name_enc = protector.encrypt_name(raw_name)

    db_conn.execute(
        """
        INSERT INTO leads (
            workflow_id, phone_hash, phone_last4_encrypted, customer_name_encrypted,
            batch_id, external_ref, extra_fields_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "motor_renewal",
            phone_hash,
            phone_last4_enc,
            name_enc,
            "batch_001",
            "MOT/2026/100001",
            "{}",
            "2026-10-01T00:00:00Z",
            "2026-10-01T00:00:00Z",
        ),
    )
    lead_id = db_conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    sanitized_payload = json.dumps(
        PIIProtector.sanitize_event_payload(
            {"disposition_code": "renewed", "phone": canonical_phone, "customer_name": raw_name},
            schema_registry.all_pii_field_names,
        )
    )
    db_conn.execute(
        """
        INSERT INTO events (
            event_id, event_type, workflow_id, call_id, lead_id,
            occurred_at, payload_json, is_ignored, ingested_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "evt_00000001",
            "disposition.set",
            "motor_renewal",
            "call_00000001",
            lead_id,
            "2026-10-01T10:00:00Z",
            sanitized_payload,
            0,
            "2026-10-01T10:01:00Z",
        ),
    )
    db_conn.execute(
        """
        INSERT INTO orphan_events (
            event_id, event_type, workflow_id, call_id, phone_hash,
            occurred_at, payload_json, ingested_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "evt_00000002",
            "call.initiated",
            "motor_renewal",
            "call_00000002",
            protector.hash_phone("+919999988888"),
            "2026-10-01T10:00:00Z",
            json.dumps(
                PIIProtector.sanitize_event_payload(
                    {"phone": "+919999988888"},
                    schema_registry.all_pii_field_names,
                )
            ),
            "2026-10-01T10:01:00Z",
        ),
    )
    db_conn.commit()

    # Verify zero plaintext PII exists across all stored table rows.
    for table in ("leads", "events", "orphan_events"):
        rows = [dict(r) for r in db_conn.execute(f"SELECT * FROM {table}").fetchall()]
        serialized = json.dumps(rows)
        for sensitive in (
            raw_name,
            "Zoya",
            "Kulkarni",
            "8765432109",
            "2109",
            "9999988888",
            "8888",
        ):
            assert sensitive not in serialized, f"Found plaintext {sensitive!r} in {table}"
