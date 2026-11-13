"""Phase 6 unit tests for lead bucket classification and priority."""
from __future__ import annotations

from datetime import datetime

from solution.config import IST
from solution.models.schema_loader import DispositionSpec
from solution.reporting.ledger_facts import CallFact, classify_lead_bucket


def _call(
    call_id: str,
    *,
    telephony_failed: bool = False,
    connected: bool = False,
    code: str | None = None,
    terminal: bool = False,
    success: bool = False,
) -> CallFact:
    spec = (
        DispositionSpec("motor_renewal", code, code, terminal, success)
        if code is not None
        else None
    )
    return CallFact(
        lead_id=1,
        workflow_id="motor_renewal",
        call_id=call_id,
        initiated_at_ist=datetime(2026, 10, 1, 10, 0, 0, tzinfo=IST),
        ist_date="2026-10-01",
        is_connected=connected,
        has_telephony_failed=telephony_failed,
        effective_disposition=code,
        disposition_spec=spec,
    )


def test_bucket_priority_all_four_buckets() -> None:
    # 1. No calls -> yet_to_reach_out
    assert classify_lead_bucket([]) == "yet_to_reach_out"

    # 2. Any terminal disposition -> completed (takes priority over telephony_failed on another call)
    assert (
        classify_lead_bucket(
            [
                _call("c1", telephony_failed=True),
                _call("c2", connected=True, code="renewed", terminal=True, success=True),
            ]
        )
        == "completed"
    )
    assert (
        classify_lead_bucket(
            [
                _call("c1", connected=True, code="not_interested", terminal=True, success=False),
            ]
        )
        == "completed"
    )

    # 3. Every call telephony_failed -> in_progress_telephony_failed
    assert (
        classify_lead_bucket(
            [
                _call("c1", telephony_failed=True),
                _call("c2", telephony_failed=True),
            ]
        )
        == "in_progress_telephony_failed"
    )

    # 4. Mixed or non-terminal -> in_progress_dialed
    assert (
        classify_lead_bucket(
            [
                _call("c1", telephony_failed=True),
                _call("c2", connected=False),  # rang, no answer
            ]
        )
        == "in_progress_dialed"
    )
    assert (
        classify_lead_bucket(
            [
                _call("c1", connected=True, code="callback_requested", terminal=False),
            ]
        )
        == "in_progress_dialed"
    )
    assert classify_lead_bucket([_call("c1", connected=True)]) == "in_progress_dialed"
