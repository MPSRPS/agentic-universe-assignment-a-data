"""CLI entry point for the Interaction Ledger & Reporting Pipeline."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from solution.config import ConfigurationError, load_config
from solution.db.connection import get_db, transaction
from solution.ingestion.events import ingest_drop_events
from solution.ingestion.leads import (
    ingest_drop_leads,
    is_drop_ingested,
    mark_drop_ingested,
)
from solution.ingestion.reconcile import reconcile_orphans
from solution.models.schema_loader import load_schemas
from solution.reporting import generate_all_reports
from solution.security.pii import PIIProtector


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser for ingest and report commands."""
    parser = argparse.ArgumentParser(
        prog="pipeline",
        description="Agentic Universe Interaction Ledger & Reporting Pipeline",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Path to SQLite database file (default: ledger.db or AU_DB_PATH)",
    )
    parser.add_argument(
        "--schemas",
        default=None,
        help="Path to schemas directory (default: assignment-a-data/schemas)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser(
        "ingest",
        help="Ingest a data drop directory (leads and events)",
    )
    ingest_parser.add_argument(
        "drop_path",
        help="Path to the drop directory (e.g., data/drop_1)",
    )

    report_parser = subparsers.add_parser(
        "report",
        help="Generate the five output report files",
    )
    report_parser.add_argument(
        "--out",
        required=True,
        help="Output directory for generated reports",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI arguments and dispatch to the selected command."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = load_config(db_path=args.db, schemas_dir=args.schemas)
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    if args.command == "ingest":
        drop_path = Path(args.drop_path)
        if not drop_path.is_dir():
            parser.error(f"Drop directory does not exist: {drop_path}")
        registry = load_schemas(config.schemas_dir)
        protector = PIIProtector.from_config(config)
        with get_db(config.db_path) as conn:
            if is_drop_ingested(conn, drop_path):
                return 0
            with transaction(conn):
                ingest_drop_leads(conn, drop_path, registry, protector)
                ingest_drop_events(conn, drop_path, registry, protector)
                reconcile_orphans(conn, registry)
                mark_drop_ingested(conn, drop_path)
        return 0

    if args.command == "report":
        registry = load_schemas(config.schemas_dir)
        protector = PIIProtector.from_config(config)
        with get_db(config.db_path) as conn:
            generate_all_reports(conn, Path(args.out), registry, protector)
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
