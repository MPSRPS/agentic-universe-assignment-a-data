# AI Usage Disclosure

## Tools used
- Antigravity AI coding assistant (architecture analysis performed with Claude Opus 4.6 Thinking; phased implementation, debugging, and verification performed with Gemini 3.8 Flash High).
- ChatGPT (used alongside for drafting prompts, discussing approach suggestions, and clarifying technical concepts like phone normalization, PII handling, and data reconciliation).

## What I used them for
- Inspecting repository schemas, `assignment-a-data/README.md`, and `tools/generate_data.py` to structure the system architecture and database design.
- Implementing the `solution/` modules phase by phase (`config.py`, `db/`, `models/`, `validation/`, `security/`, `ingestion/`, `reporting/`, `pipeline.py`).
- Writing unit, integration, security, idempotency, and reporting tests under `solution/tests/`.
- Running end-to-end verification runs against generated datasets (`--seed 42` and `--seed 99`).

## What I wrote or decided myself
- Enforced strict phase-by-phase implementation gates, reviewing each module before moving to the next.
- Decided on zero plaintext PII storage: insisted on removing `raw_json` from `orphan_events`, encrypting `phone_last4` with Fernet (`phone_last4_encrypted`) alongside `customer_name`, and scrubbing PII keys from `payload_json` before saving.
- Required persistent environment-based key management with explicit failure (`ConfigurationError`) when keys are missing rather than falling back to default or temporary keys.
- Required unknown disposition codes to be counted in metrics, marked ignored, and excluded from effective disposition and lead bucket calculations.
- Reviewed the implementation against the assignment requirements and used test results and manual checks to decide whether further corrections were needed.

## Where the AI was wrong or unhelpful
1. **Insecure PII & key-management shortcuts in the initial draft:** The initial architecture and Phase 1 code included a `raw_json` column on `orphan_events` (which would have stored raw event phones in plaintext), stored `phone_last4` in plaintext on `leads`, and used a hardcoded dev secret fallback when `AU_SECRET_KEY` was unset. During review before Phase 3, I rejected these shortcuts and had them replaced with `phone_last4_encrypted`, `raw_json` removal, recursive payload PII scrubbing, and strict `ConfigurationError` enforcement.
2. **Premature phase execution during implementation:** Twice during implementation, the assistant started writing code for the next phase before receiving explicit review and approval; both edits were reverted before proceeding.
3. **Environment and shell command compatibility:** AI-generated verification commands initially assumed a Linux/Bash shell (using `export` or `&&` syntax), which failed in Windows PowerShell. I had to correct these to use proper PowerShell commands and environment variable syntax.

## How I verified AI-generated code
- **Automated test suite:** Ran `python -m pytest solution/tests/ -v` after each phase, verifying that all 133 tests passed cleanly.
- **Manual verification:** Inspected the SQLite database and its tables directly, checked the generated CSV and JSON reports, reviewed report headers and sorting, and checked that plaintext customer names and full phone numbers were not stored in database records or exposed in generated reports.
- **Idempotency checks:** Re-ran an already ingested drop and checked that records, counters, and generated outputs were not duplicated or changed.
- **Requirement validation:** Compared the implementation and observed outputs against the assignment requirements, including data-quality accounting equations, orphan event reconciliation, disposition handling, and report generation on fresh datasets.
