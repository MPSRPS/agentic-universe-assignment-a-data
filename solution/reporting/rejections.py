"""Generate rejections.csv from stored non-PII rejection records."""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

REJECTIONS_HEADER: tuple[str, ...] = (
    "workflow_id",
    "batch_id",
    "row_number",
    "reason",
)


def write_rejections(conn: sqlite3.Connection, out_dir: Path) -> Path:
    """Write rejections.csv sorted by (workflow_id, batch_id, row_number)."""
    rows = conn.execute(
        """
        SELECT workflow_id, batch_id, row_number, reason
        FROM rejections
        ORDER BY workflow_id, batch_id, row_number
        """
    ).fetchall()

    out_path = out_dir / "rejections.csv"
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(REJECTIONS_HEADER)
        for row in rows:
            writer.writerow(
                [
                    row["workflow_id"],
                    row["batch_id"],
                    int(row["row_number"]),
                    row["reason"],
                ]
            )
    return out_path
