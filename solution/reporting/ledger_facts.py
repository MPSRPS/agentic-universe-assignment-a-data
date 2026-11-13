"""Shared derived-fact computation from the append-only interaction ledger."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from solution.config import IST
from solution.models.schema_loader import DispositionSpec, SchemaRegistry
from solution.security.pii import PIIProtector


@dataclass(frozen=True)
class CallFact:
    """Derived facts for a single linked call (requires a call.initiated event)."""

    lead_id: int
    workflow_id: str
    call_id: str
    initiated_at_ist: datetime
    ist_date: str
    is_connected: bool
    has_telephony_failed: bool
    effective_disposition: str | None
    disposition_spec: DispositionSpec | None


@dataclass(frozen=True)
class LeadFact:
    """Derived facts and masked PII for a single lead."""

    lead_id: int
    workflow_id: str
    external_ref: str
    batch_id: str
    phone_masked: str
    bucket: str
    total_calls: int
    connected_calls: int
    last_call_at_ist: datetime | None
    current_disposition: str | None
    ever_success: bool
    calls: tuple[CallFact, ...]


def parse_utc_to_ist(utc_iso: str) -> datetime:
    """Parse a stored UTC ISO-8601 timestamp into an aware IST datetime."""
    text = utc_iso.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text).astimezone(IST)


def compute_effective_disposition(
    disposition_events: list[tuple[str, datetime, str, str]],
) -> str | None:
    """Compute effective disposition for one call from valid disposition events.

    Each item is (event_type, occurred_at, event_id, disposition_code).
    Latest 'disposition.override' by occurred_at wins; if none, latest
    'disposition.set' wins; otherwise None. Ties on occurred_at break
    deterministically by event_id.
    """
    overrides = [e for e in disposition_events if e[0] == "disposition.override"]
    if overrides:
        latest = max(overrides, key=lambda e: (e[1], e[2]))
        return latest[3]

    sets = [e for e in disposition_events if e[0] == "disposition.set"]
    if sets:
        latest = max(sets, key=lambda e: (e[1], e[2]))
        return latest[3]

    return None


def classify_lead_bucket(calls: list[CallFact]) -> str:
    """Determine a lead's bucket from its linked calls in priority order."""
    if not calls:
        return "yet_to_reach_out"
    if any(c.disposition_spec is not None and c.disposition_spec.is_terminal for c in calls):
        return "completed"
    if all(c.has_telephony_failed for c in calls):
        return "in_progress_telephony_failed"
    return "in_progress_dialed"


def build_lead_facts(
    conn: sqlite3.Connection,
    registry: SchemaRegistry,
    protector: PIIProtector,
) -> list[LeadFact]:
    """Compute CallFact and LeadFact records for every lead from the ledger."""
    lead_rows = conn.execute(
        """
        SELECT lead_id, workflow_id, external_ref, batch_id, phone_last4_encrypted
        FROM leads
        ORDER BY workflow_id, external_ref
        """
    ).fetchall()

    initiated_by_call: dict[tuple[int, str], tuple[str, datetime]] = {}
    connected_calls: set[tuple[int, str]] = set()
    telephony_failed_calls: set[tuple[int, str]] = set()
    dispositions_by_call: dict[tuple[int, str], list[tuple[str, datetime, str, str]]] = {}

    event_rows = conn.execute(
        """
        SELECT event_id, lead_id, workflow_id, call_id, event_type,
               occurred_at, payload_json, is_ignored
        FROM events
        ORDER BY event_row_id
        """
    ).fetchall()

    for ev in event_rows:
        key = (int(ev["lead_id"]), ev["call_id"])
        etype = ev["event_type"]
        occurred_ist = parse_utc_to_ist(ev["occurred_at"])

        if etype == "call.initiated":
            prev = initiated_by_call.get(key)
            if prev is None or occurred_ist < prev[1]:
                initiated_by_call[key] = (ev["workflow_id"], occurred_ist)
        elif etype == "call.connected":
            connected_calls.add(key)
        elif etype == "call.telephony_failed":
            telephony_failed_calls.add(key)
        elif etype in ("disposition.set", "disposition.override"):
            if int(ev["is_ignored"]) != 0:
                continue
            payload = json.loads(ev["payload_json"])
            code = payload.get("disposition_code")
            if not isinstance(code, str):
                continue
            code = code.strip()
            if registry.get_disposition(ev["workflow_id"], code) is None:
                continue
            dispositions_by_call.setdefault(key, []).append(
                (etype, occurred_ist, ev["event_id"], code)
            )

    calls_by_lead: dict[int, list[CallFact]] = {}
    for (lead_id, call_id), (workflow_id, init_ist) in initiated_by_call.items():
        eff_code = compute_effective_disposition(
            dispositions_by_call.get((lead_id, call_id), [])
        )
        disp_spec = (
            registry.get_disposition(workflow_id, eff_code)
            if eff_code is not None
            else None
        )
        call_fact = CallFact(
            lead_id=lead_id,
            workflow_id=workflow_id,
            call_id=call_id,
            initiated_at_ist=init_ist,
            ist_date=init_ist.date().isoformat(),
            is_connected=(lead_id, call_id) in connected_calls,
            has_telephony_failed=(lead_id, call_id) in telephony_failed_calls,
            effective_disposition=eff_code,
            disposition_spec=disp_spec,
        )
        calls_by_lead.setdefault(lead_id, []).append(call_fact)

    lead_facts: list[LeadFact] = []
    for row in lead_rows:
        lead_id = int(row["lead_id"])
        calls = sorted(
            calls_by_lead.get(lead_id, []),
            key=lambda c: (c.initiated_at_ist, c.call_id),
        )
        bucket = classify_lead_bucket(calls)
        total_calls = len(calls)
        conn_count = sum(1 for c in calls if c.is_connected)
        last_call_at = calls[-1].initiated_at_ist if calls else None

        calls_with_disp = [c for c in calls if c.effective_disposition is not None]
        current_disp = (
            calls_with_disp[-1].effective_disposition if calls_with_disp else None
        )
        ever_success = any(
            c.disposition_spec is not None and c.disposition_spec.is_success
            for c in calls
        )
        phone_masked = protector.mask_phone_from_encrypted_last4(
            row["phone_last4_encrypted"]
        )

        lead_facts.append(
            LeadFact(
                lead_id=lead_id,
                workflow_id=row["workflow_id"],
                external_ref=row["external_ref"],
                batch_id=row["batch_id"],
                phone_masked=phone_masked,
                bucket=bucket,
                total_calls=total_calls,
                connected_calls=conn_count,
                last_call_at_ist=last_call_at,
                current_disposition=current_disp,
                ever_success=ever_success,
                calls=tuple(calls),
            )
        )

    return lead_facts
