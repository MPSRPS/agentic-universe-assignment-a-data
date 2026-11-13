"""Generate batch_summary.csv from derived lead facts."""
from __future__ import annotations

import csv
from pathlib import Path

from solution.reporting.ledger_facts import LeadFact

BATCH_SUMMARY_HEADER: tuple[str, ...] = (
    "workflow_id",
    "batch_id",
    "total_leads",
    "yet_to_reach_out",
    "in_progress_telephony_failed",
    "in_progress_dialed",
    "completed",
)


def write_batch_summary(lead_facts: list[LeadFact], out_dir: Path) -> Path:
    """Write batch_summary.csv sorted by (workflow_id, batch_id)."""
    buckets_by_batch: dict[tuple[str, str], dict[str, int]] = {}

    for lead in lead_facts:
        key = (lead.workflow_id, lead.batch_id)
        counts = buckets_by_batch.setdefault(
            key,
            {
                "total_leads": 0,
                "yet_to_reach_out": 0,
                "in_progress_telephony_failed": 0,
                "in_progress_dialed": 0,
                "completed": 0,
            },
        )
        counts["total_leads"] += 1
        counts[lead.bucket] += 1

    out_path = out_dir / "batch_summary.csv"
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(BATCH_SUMMARY_HEADER)
        for workflow_id, batch_id in sorted(buckets_by_batch.keys()):
            c = buckets_by_batch[(workflow_id, batch_id)]
            writer.writerow(
                [
                    workflow_id,
                    batch_id,
                    c["total_leads"],
                    c["yet_to_reach_out"],
                    c["in_progress_telephony_failed"],
                    c["in_progress_dialed"],
                    c["completed"],
                ]
            )
    return out_path
