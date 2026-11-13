"""Generate daily_overview.csv from derived call and lead facts."""
from __future__ import annotations

import csv
from pathlib import Path

from solution.reporting.ledger_facts import LeadFact

DAILY_OVERVIEW_HEADER: tuple[str, ...] = (
    "workflow_id",
    "date",
    "total_calls",
    "connected_calls",
    "connectivity_rate",
    "success_leads",
)


def write_daily_overview(lead_facts: list[LeadFact], out_dir: Path) -> Path:
    """Write daily_overview.csv sorted by (workflow_id, date)."""
    stats_by_day: dict[tuple[str, str], dict[str, object]] = {}

    for lead in lead_facts:
        for call in lead.calls:
            key = (call.workflow_id, call.ist_date)
            entry = stats_by_day.setdefault(
                key,
                {
                    "total_calls": 0,
                    "connected_calls": 0,
                    "success_leads": set(),
                },
            )
            entry["total_calls"] = int(entry["total_calls"]) + 1
            if call.is_connected:
                entry["connected_calls"] = int(entry["connected_calls"]) + 1
            if call.disposition_spec is not None and call.disposition_spec.is_success:
                success_set = entry["success_leads"]
                assert isinstance(success_set, set)
                success_set.add(lead.lead_id)

    out_path = out_dir / "daily_overview.csv"
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(DAILY_OVERVIEW_HEADER)
        for workflow_id, ist_date in sorted(stats_by_day.keys()):
            entry = stats_by_day[(workflow_id, ist_date)]
            total_calls = int(entry["total_calls"])
            connected_calls = int(entry["connected_calls"])
            connectivity_rate = f"{connected_calls / total_calls:.4f}"
            success_set = entry["success_leads"]
            assert isinstance(success_set, set)
            writer.writerow(
                [
                    workflow_id,
                    ist_date,
                    total_calls,
                    connected_calls,
                    connectivity_rate,
                    len(success_set),
                ]
            )
    return out_path
