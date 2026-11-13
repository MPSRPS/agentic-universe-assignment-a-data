"""Generate lead_report.csv from derived lead facts."""
from __future__ import annotations

import csv
from pathlib import Path

from solution.reporting.ledger_facts import LeadFact

LEAD_REPORT_HEADER: tuple[str, ...] = (
    "workflow_id",
    "external_ref",
    "batch_id",
    "phone_masked",
    "bucket",
    "total_calls",
    "connected_calls",
    "last_call_at",
    "current_disposition",
    "ever_success",
)


def write_lead_report(lead_facts: list[LeadFact], out_dir: Path) -> Path:
    """Write lead_report.csv sorted by (workflow_id, external_ref)."""
    ordered = sorted(lead_facts, key=lambda l: (l.workflow_id, l.external_ref))
    out_path = out_dir / "lead_report.csv"
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(LEAD_REPORT_HEADER)
        for lead in ordered:
            last_call_str = (
                lead.last_call_at_ist.isoformat(timespec="seconds")
                if lead.last_call_at_ist is not None
                else ""
            )
            writer.writerow(
                [
                    lead.workflow_id,
                    lead.external_ref,
                    lead.batch_id,
                    lead.phone_masked,
                    lead.bucket,
                    lead.total_calls,
                    lead.connected_calls,
                    last_call_str,
                    lead.current_disposition or "",
                    "true" if lead.ever_success else "false",
                ]
            )
    return out_path
