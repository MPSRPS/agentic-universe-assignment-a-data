# Solution: Interaction Ledger & Reporting Pipeline (Assignment A)

See [../SUBMISSION.md](../SUBMISSION.md) for full architecture notes, design decisions, and verification results.

## Quick Start

```bash
pip install -r requirements.txt
export AU_SECRET_KEY="your-persistent-secret-key"

python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data
python -m solution.pipeline --db ledger.db ingest data/drop_1
python -m solution.pipeline --db ledger.db ingest data/drop_2
python -m solution.pipeline --db ledger.db report --out reports/

python -m pytest solution/tests/ -v
```

## Module Layout

- `pipeline.py` — CLI entry point (`ingest`, `report`, `--db`, `--schemas`).
- `config.py` — Runtime configuration, `IST` offset, and persistent env key resolution.
- `db/` — SQLite DDL (`schema.py`) and connection/transaction helpers (`connection.py`).
- `models/` — Workflow JSON schema and `dispositions.csv` loader (`schema_loader.py`).
- `validation/` — Field/header validators (`phone.py`, `date.py`, `decimal.py`, `enum.py`, `header.py`).
- `security/` — HMAC-SHA256 phone hashing, Fernet encryption, masking, and payload scrubbing (`pii.py`).
- `ingestion/` — Drop discovery (`discover.py`), lead CSV ingestion (`leads.py`), JSONL event ingestion (`events.py`), and two-pass orphan reconciliation (`reconcile.py`).
- `reporting/` — Derived ledger facts (`ledger_facts.py`) and the five report writers (`batch_summary.py`, `daily_overview.py`, `lead_report.py`, `rejections.py`, `data_quality.py`).
- `tests/` — Pytest unit and integration suite.
