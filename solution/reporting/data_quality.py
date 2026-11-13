"""Generate data_quality.json from stored quality metrics and rejections."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def write_data_quality(conn: sqlite3.Connection, out_dir: Path) -> Path:
    """Write data_quality.json matching the output contract."""
    metric_rows = conn.execute(
        "SELECT metric_name, metric_value FROM data_quality"
    ).fetchall()
    metrics = {row["metric_name"]: int(row["metric_value"]) for row in metric_rows}

    unresolved_orphans = int(
        conn.execute("SELECT COUNT(*) FROM orphan_events").fetchone()[0]
    )

    reason_rows = conn.execute(
        """
        SELECT reason, COUNT(*) AS cnt
        FROM rejections
        GROUP BY reason
        ORDER BY reason
        """
    ).fetchall()
    rejections_by_reason = {row["reason"]: int(row["cnt"]) for row in reason_rows}

    payload = {
        "lead_rows_total": metrics.get("lead_rows_total", 0),
        "lead_rows_rejected": metrics.get("lead_rows_rejected", 0),
        "rejections_by_reason": rejections_by_reason,
        "duplicate_lead_rows_merged": metrics.get("duplicate_lead_rows_merged", 0),
        "field_warnings": metrics.get("field_warnings", 0),
        "events_total": metrics.get("events_total", 0),
        "malformed_events": metrics.get("malformed_events", 0),
        "duplicate_events_dropped": metrics.get("duplicate_events_dropped", 0),
        "orphan_events": unresolved_orphans,
        "unknown_disposition_events": metrics.get("unknown_disposition_events", 0),
    }

    out_path = out_dir / "data_quality.json"
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return out_path
