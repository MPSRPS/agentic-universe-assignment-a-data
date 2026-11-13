"""Phase 6 integration tests for all five report outputs and edge cases."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from solution.config import ENV_SECRET_KEY
from solution.pipeline import main
from solution.reporting.batch_summary import BATCH_SUMMARY_HEADER
from solution.reporting.daily_overview import DAILY_OVERVIEW_HEADER
from solution.reporting.lead_report import LEAD_REPORT_HEADER
from solution.reporting.rejections import REJECTIONS_HEADER


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


def _read_csv_raw(path: Path) -> tuple[list[str], list[list[str]]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    return rows[0], rows[1:]


def test_empty_database_produces_valid_empty_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_SECRET_KEY, "phase6-empty-secret")
    db_file = tmp_path / "empty.db"
    out_dir = tmp_path / "reports"

    assert main(["--db", str(db_file), "report", "--out", str(out_dir)]) == 0

    assert _read_csv_raw(out_dir / "batch_summary.csv") == (list(BATCH_SUMMARY_HEADER), [])
    assert _read_csv_raw(out_dir / "daily_overview.csv") == (list(DAILY_OVERVIEW_HEADER), [])
    assert _read_csv_raw(out_dir / "lead_report.csv") == (list(LEAD_REPORT_HEADER), [])
    assert _read_csv_raw(out_dir / "rejections.csv") == (list(REJECTIONS_HEADER), [])

    dq = json.loads((out_dir / "data_quality.json").read_text(encoding="utf-8"))
    assert dq == {
        "lead_rows_total": 0,
        "lead_rows_rejected": 0,
        "rejections_by_reason": {},
        "duplicate_lead_rows_merged": 0,
        "field_warnings": 0,
        "events_total": 0,
        "malformed_events": 0,
        "duplicate_events_dropped": 0,
        "orphan_events": 0,
        "unknown_disposition_events": 0,
    }


def test_all_five_reports_contract_ist_dates_batch_retention_and_no_pii(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_SECRET_KEY, "phase6-full-secret")
    db_file = tmp_path / "full.db"
    drop1 = tmp_path / "drop_1"
    drop2 = tmp_path / "drop_2"
    out_dir = tmp_path / "reports"

    # Drop 1:
    # - MOT/001 (9876543210): batch_001 -> multiple calls, Call 1 has disposition, Call 2 has NO disposition
    # - MOT/002 (9876543211): batch_001 -> all telephony_failed
    # - MOT/003 (9876543212): batch_002 -> yet_to_reach_out (no calls; also gets an orphan call.connected without call.initiated)
    # - Rejected row in batch_001
    _write_csv(
        drop1 / "leads" / "motor_renewal" / "batch_001.csv",
        ["customer_name", "phone", "policy_number"],
        [
            ["Aarav Secret", "9876543210", "MOT/001"],
            ["Bad Name", "5123456789", "MOT/999"],
            ["Diya Secret", "9876543211", "MOT/002"],
        ],
    )
    _write_csv(
        drop1 / "leads" / "motor_renewal" / "batch_002.csv",
        ["customer_name", "phone", "policy_number"],
        [
            ["Ishaan Secret", "9876543212", "MOT/003"],
        ],
    )
    # Drop 1 events:
    # - MOT/001 Call 1: initiated at 2026-10-01T19:00:00Z (= 2026-10-02 00:30:00+05:30 IST!), connected, disposition callback_requested -> overridden to renewed
    # - MOT/001 Call 2: initiated at 2026-10-02T10:00:00Z (= 2026-10-02 15:30:00+05:30 IST!), connected, NO disposition (tests current_disposition comes from Call 1, and distinct success_leads=1 on 2026-10-02!)
    # - MOT/002 Call 3: initiated 2026-10-01T10:00:00Z (= 2026-10-01 IST), telephony_failed
    # - MOT/003 Call 4: call.connected ONLY (no call.initiated) -> NOT counted as a call!
    # - Pure orphan event (never arrives)
    # - Event arriving before drop_2 lead ORD-001
    _write_jsonl(
        drop1 / "events" / "events_0001.jsonl",
        [
            json.dumps({
                "event_id": "e1",
                "event_type": "call.initiated",
                "workflow_id": "motor_renewal",
                "call_id": "c1",
                "occurred_at": "2026-10-01T19:00:00Z",
                "phone": "9876543210",
                "payload": {},
            }),
            json.dumps({
                "event_id": "e2",
                "event_type": "call.connected",
                "workflow_id": "motor_renewal",
                "call_id": "c1",
                "occurred_at": "2026-10-01T19:00:10Z",
                "phone": "9876543210",
                "payload": {},
            }),
            json.dumps({
                "event_id": "e3",
                "event_type": "disposition.set",
                "workflow_id": "motor_renewal",
                "call_id": "c1",
                "occurred_at": "2026-10-01T19:01:00Z",
                "phone": "9876543210",
                "payload": {"disposition_code": "callback_requested"},
            }),
            json.dumps({
                "event_id": "e4",
                "event_type": "disposition.override",
                "workflow_id": "motor_renewal",
                "call_id": "c1",
                "occurred_at": "2026-10-02T05:00:00Z",
                "payload": {"disposition_code": "renewed"},
            }),
            json.dumps({
                "event_id": "e5",
                "event_type": "call.initiated",
                "workflow_id": "motor_renewal",
                "call_id": "c2",
                "occurred_at": "2026-10-02T10:00:00Z",
                "phone": "9876543210",
                "payload": {},
            }),
            json.dumps({
                "event_id": "e6",
                "event_type": "call.connected",
                "workflow_id": "motor_renewal",
                "call_id": "c2",
                "occurred_at": "2026-10-02T10:00:15Z",
                "phone": "9876543210",
                "payload": {},
            }),
            json.dumps({
                "event_id": "e7",
                "event_type": "call.initiated",
                "workflow_id": "motor_renewal",
                "call_id": "c3",
                "occurred_at": "2026-10-01T10:00:00Z",
                "phone": "9876543211",
                "payload": {},
            }),
            json.dumps({
                "event_id": "e8",
                "event_type": "call.telephony_failed",
                "workflow_id": "motor_renewal",
                "call_id": "c3",
                "occurred_at": "2026-10-01T10:00:05Z",
                "phone": "9876543211",
                "payload": {"reason": "sip_503"},
            }),
            json.dumps({
                "event_id": "e9",
                "event_type": "call.connected",
                "workflow_id": "motor_renewal",
                "call_id": "c4_no_init",
                "occurred_at": "2026-10-01T12:00:00Z",
                "phone": "9876543212",
                "payload": {},
            }),
            json.dumps({
                "event_id": "e_orphan_forever",
                "event_type": "call.initiated",
                "workflow_id": "motor_renewal",
                "call_id": "c_never",
                "occurred_at": "2026-10-01T12:30:00Z",
                "phone": "9999900000",
                "payload": {},
            }),
            json.dumps({
                "event_id": "e_pf_init",
                "event_type": "call.initiated",
                "workflow_id": "policy_feedback",
                "call_id": "c_pf_1",
                "occurred_at": "2026-10-03 09:00:00",  # naive = IST 2026-10-03
                "phone": "9876543213",
                "payload": {},
            }),
            json.dumps({
                "event_id": "e_pf_conn",
                "event_type": "call.connected",
                "workflow_id": "policy_feedback",
                "call_id": "c_pf_1",
                "occurred_at": "2026-10-03 09:00:10",
                "phone": "9876543213",
                "payload": {},
            }),
            json.dumps({
                "event_id": "e_pf_disp",
                "event_type": "disposition.set",
                "workflow_id": "policy_feedback",
                "call_id": "c_pf_1",
                "occurred_at": "2026-10-03 09:02:00",
                "phone": "9876543213",
                "payload": {"disposition_code": "feedback_captured"},
            }),
        ],
    )

    # Drop 2:
    # - Duplicate of MOT/001 in batch_003 (must retain batch_001!)
    # - New policy_feedback lead ORD-001 in batch_003 (reconciles e_pf_*!)
    _write_csv(
        drop2 / "leads" / "motor_renewal" / "batch_003.csv",
        ["customer_name", "phone", "policy_number"],
        [["Aarav Updated", "09876543210", "MOT/001"]],
    )
    _write_csv(
        drop2 / "leads" / "policy_feedback" / "batch_003.csv",
        ["customer_name", "phone", "order_id"],
        [["Meera Secret", "9876543213", "ORD-001"]],
    )

    assert main(["--db", str(db_file), "ingest", str(drop1)]) == 0
    assert main(["--db", str(db_file), "ingest", str(drop2)]) == 0
    assert main(["--db", str(db_file), "report", "--out", str(out_dir)]) == 0

    # 1. batch_summary.csv
    bs_header, bs_rows = _read_csv_raw(out_dir / "batch_summary.csv")
    assert bs_header == list(BATCH_SUMMARY_HEADER)
    assert bs_rows == [
        ["motor_renewal", "batch_001", "2", "0", "1", "0", "1"],
        ["motor_renewal", "batch_002", "1", "1", "0", "0", "0"],
        ["policy_feedback", "batch_003", "1", "0", "0", "0", "1"],
    ]

    # 2. daily_overview.csv (IST dates; orphan c_never excluded; distinct success_leads)
    do_header, do_rows = _read_csv_raw(out_dir / "daily_overview.csv")
    assert do_header == list(DAILY_OVERVIEW_HEADER)
    assert do_rows == [
        ["motor_renewal", "2026-10-01", "1", "0", "0.0000", "0"],
        ["motor_renewal", "2026-10-02", "2", "2", "1.0000", "1"],
        ["policy_feedback", "2026-10-03", "1", "1", "1.0000", "1"],
    ]

    # 3. lead_report.csv
    lr_header, lr_rows = _read_csv_raw(out_dir / "lead_report.csv")
    assert lr_header == list(LEAD_REPORT_HEADER)
    assert lr_rows == [
        [
            "motor_renewal",
            "MOT/001",
            "batch_001",
            "+91******3210",
            "completed",
            "2",
            "2",
            "2026-10-02T15:30:00+05:30",
            "renewed",
            "true",
        ],
        [
            "motor_renewal",
            "MOT/002",
            "batch_001",
            "+91******3211",
            "in_progress_telephony_failed",
            "1",
            "0",
            "2026-10-01T15:30:00+05:30",
            "",
            "false",
        ],
        [
            "motor_renewal",
            "MOT/003",
            "batch_002",
            "+91******3212",
            "yet_to_reach_out",
            "0",
            "0",
            "",
            "",
            "false",
        ],
        [
            "policy_feedback",
            "ORD-001",
            "batch_003",
            "+91******3213",
            "completed",
            "1",
            "1",
            "2026-10-03T09:00:00+05:30",
            "feedback_captured",
            "true",
        ],
    ]

    # 4. rejections.csv
    rj_header, rj_rows = _read_csv_raw(out_dir / "rejections.csv")
    assert rj_header == list(REJECTIONS_HEADER)
    assert rj_rows == [
        ["motor_renewal", "batch_001", "2", "invalid_phone"],
    ]

    # 5. data_quality.json (orphan_events = 1, only e_orphan_forever remains)
    dq = json.loads((out_dir / "data_quality.json").read_text(encoding="utf-8"))
    assert dq == {
        "lead_rows_total": 6,
        "lead_rows_rejected": 1,
        "rejections_by_reason": {"invalid_phone": 1},
        "duplicate_lead_rows_merged": 1,
        "field_warnings": 0,
        "events_total": 13,
        "malformed_events": 0,
        "duplicate_events_dropped": 0,
        "orphan_events": 1,
        "unknown_disposition_events": 0,
    }

    # Verify zero PII leaked into any generated report file
    for report_file in out_dir.iterdir():
        content = report_file.read_text(encoding="utf-8")
        for sensitive in (
            "Aarav",
            "Diya",
            "Ishaan",
            "Meera",
            "Bad Name",
            "Secret",
            "9876543210",
            "9876543211",
            "9876543212",
            "9876543213",
            "5123456789",
            "9999900000",
        ):
            assert sensitive not in content, f"PII {sensitive!r} leaked in {report_file.name}"
