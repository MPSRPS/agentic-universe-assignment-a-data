"""File discovery and deterministic ordering for data drops."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from solution.models.schema_loader import SchemaRegistry


@dataclass(frozen=True)
class LeadFile:
    """Discovered lead CSV file with its workflow and batch identifiers."""

    workflow_id: str
    batch_id: str
    path: Path


def discover_lead_files(drop_path: Path, registry: SchemaRegistry) -> list[LeadFile]:
    """Discover lead CSV files in deterministic workflow and filename order.

    Expects <drop_path>/leads/<workflow_id>/<batch>.csv.
    Returns an empty list if <drop_path>/leads does not exist.
    """
    leads_root = drop_path / "leads"
    if not leads_root.is_dir():
        return []

    discovered: list[LeadFile] = []
    workflow_dirs = sorted(
        (p for p in leads_root.iterdir() if p.is_dir()),
        key=lambda p: p.name,
    )
    for wf_dir in workflow_dirs:
        registry.get_workflow(wf_dir.name)
        csv_files = sorted(
            (p for p in wf_dir.iterdir() if p.is_file() and p.suffix.lower() == ".csv"),
            key=lambda p: p.name,
        )
        for csv_path in csv_files:
            discovered.append(
                LeadFile(
                    workflow_id=wf_dir.name,
                    batch_id=csv_path.stem,
                    path=csv_path,
                )
            )
    return discovered


def discover_event_files(drop_path: Path) -> list[Path]:
    """Discover event JSONL files in deterministic filename order.

    Expects <drop_path>/events/*.jsonl.
    Returns an empty list if <drop_path>/events does not exist.
    """
    events_root = drop_path / "events"
    if not events_root.is_dir():
        return []

    return sorted(
        (p for p in events_root.iterdir() if p.is_file() and p.suffix.lower() == ".jsonl"),
        key=lambda p: p.name,
    )
