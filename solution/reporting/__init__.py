"""Report generation package for the five output contract files."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from solution.models.schema_loader import SchemaRegistry
from solution.reporting.batch_summary import write_batch_summary
from solution.reporting.daily_overview import write_daily_overview
from solution.reporting.data_quality import write_data_quality
from solution.reporting.lead_report import write_lead_report
from solution.reporting.ledger_facts import build_lead_facts
from solution.reporting.rejections import write_rejections
from solution.security.pii import PIIProtector


def generate_all_reports(
    conn: sqlite3.Connection,
    out_dir: str | Path,
    registry: SchemaRegistry,
    protector: PIIProtector,
) -> dict[str, Path]:
    """Compute derived facts from the ledger and write all five report files."""
    target_dir = Path(out_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    lead_facts = build_lead_facts(conn, registry, protector)

    return {
        "batch_summary": write_batch_summary(lead_facts, target_dir),
        "daily_overview": write_daily_overview(lead_facts, target_dir),
        "lead_report": write_lead_report(lead_facts, target_dir),
        "rejections": write_rejections(conn, target_dir),
        "data_quality": write_data_quality(conn, target_dir),
    }


__all__ = ["generate_all_reports"]
