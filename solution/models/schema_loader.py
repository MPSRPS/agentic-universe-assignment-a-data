"""Load workflow schemas and disposition catalogues."""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class FieldSchema:
    """Definition of a single field in a workflow lead schema."""

    name: str
    field_type: str
    required: bool
    pii: bool = False
    reference: bool = False
    aliases: tuple[str, ...] = ()
    values: tuple[str, ...] = ()
    default: str | None = None


@dataclass(frozen=True)
class WorkflowSchema:
    """Lead schema for a single workflow."""

    workflow_id: str
    description: str
    fields: tuple[FieldSchema, ...]
    reference_field: str
    pii_fields: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class DispositionSpec:
    """Outcome specification from dispositions.csv."""

    workflow_id: str
    code: str
    label: str
    is_terminal: bool
    is_success: bool


@dataclass(frozen=True)
class SchemaRegistry:
    """All loaded workflow schemas and dispositions."""

    workflows: dict[str, WorkflowSchema]
    dispositions: dict[str, dict[str, DispositionSpec]]
    all_pii_field_names: frozenset[str]

    def get_workflow(self, workflow_id: str) -> WorkflowSchema:
        """Return the schema for a workflow or raise KeyError."""
        if workflow_id not in self.workflows:
            raise KeyError(f"Unknown workflow_id: {workflow_id}")
        return self.workflows[workflow_id]

    def get_disposition(self, workflow_id: str, code: str) -> DispositionSpec | None:
        """Return the DispositionSpec for (workflow_id, code), or None if unknown."""
        return self.dispositions.get(workflow_id, {}).get(code)


def _parse_bool(raw: str) -> bool:
    return raw.strip().lower() == "true"


def load_workflow_schema(path: Path) -> WorkflowSchema:
    """Load a single workflow JSON schema file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    fields: list[FieldSchema] = []
    reference_field: str | None = None
    pii_fields: set[str] = set()

    for item in data["fields"]:
        fs = FieldSchema(
            name=item["name"],
            field_type=item["type"],
            required=bool(item.get("required", False)),
            pii=bool(item.get("pii", False)),
            reference=bool(item.get("reference", False)),
            aliases=tuple(item.get("aliases", [])),
            values=tuple(item.get("values", [])),
            default=item.get("default"),
        )
        fields.append(fs)
        if fs.reference:
            reference_field = fs.name
        if fs.pii:
            pii_fields.add(fs.name)

    if reference_field is None:
        raise ValueError(f"Workflow schema {path} is missing a 'reference: true' field")

    return WorkflowSchema(
        workflow_id=data["workflow_id"],
        description=data.get("description", ""),
        fields=tuple(fields),
        reference_field=reference_field,
        pii_fields=frozenset(pii_fields),
    )


def load_dispositions(path: Path) -> dict[str, dict[str, DispositionSpec]]:
    """Load dispositions.csv keyed by workflow_id -> disposition_code."""
    catalogue: dict[str, dict[str, DispositionSpec]] = {}
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            wf = row["workflow_id"].strip()
            code = row["code"].strip()
            spec = DispositionSpec(
                workflow_id=wf,
                code=code,
                label=row["label"].strip(),
                is_terminal=_parse_bool(row["is_terminal"]),
                is_success=_parse_bool(row["is_success"]),
            )
            catalogue.setdefault(wf, {})[code] = spec
    return catalogue


def load_schemas(schemas_dir: str | Path) -> SchemaRegistry:
    """Load all workflow *.json schemas and dispositions.csv from schemas_dir."""
    directory = Path(schemas_dir)
    if not directory.is_dir():
        raise FileNotFoundError(f"Schemas directory not found: {directory}")

    workflows: dict[str, WorkflowSchema] = {}
    all_pii: set[str] = set()

    for json_path in sorted(directory.glob("*.json")):
        wf_schema = load_workflow_schema(json_path)
        workflows[wf_schema.workflow_id] = wf_schema
        all_pii.update(wf_schema.pii_fields)

    dispositions_path = directory / "dispositions.csv"
    if not dispositions_path.is_file():
        raise FileNotFoundError(f"dispositions.csv not found in {directory}")
    dispositions = load_dispositions(dispositions_path)

    return SchemaRegistry(
        workflows=workflows,
        dispositions=dispositions,
        all_pii_field_names=frozenset(all_pii),
    )
