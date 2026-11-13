# Manual Run & Verification Guide — Assignment A (Interaction Ledger & Reporting Pipeline)

Welcome! This guide walks you step by step through **how this project works**, **how to run it manually on Windows**, **how to visually inspect the SQLite database**, **how to read the five output reports**, and **how to verify that the backend is producing the right numbers**.

> **Important Note About Observed vs. Future Outputs & Scale:**
> 1. Right now, your repository already contains the generated dataset under `data/` (`data/drop_1` and `data/drop_2`), **no `ledger.db` file or `reports/` folder exists in your project root yet**.
> 2. All specific row counts, sample values, and timings in this guide reflect the verified run of the **standard small-scale dataset (`--scale small`, ~2,000 leads, ~8,700 events, seed 42)** and the current codebase (`133 passed` in `pytest`). Large-scale testing (`--scale large`, ~140k leads, ~600k events) was not benchmarked.
> 3. Once you run the commands yourself, your `ledger.db` and `reports/` will be created in your project folder.

---

## 1. Plain-English Overview: What This Project Does

Imagine an insurance/consumer-brand calling team (Agentic Universe) running automated voice campaigns:
1. **Motor insurance renewal reminders** (`motor_renewal`)
2. **Post-purchase device-protection feedback** (`policy_feedback`)

Every day, two kinds of files arrive in a **drop** folder (for example, `data/drop_1` on Day 1 and `data/drop_2` on Day 2):
- **Lead CSV files** (`leads/<workflow_id>/batch_*.csv`): spreadsheets listing customers to call (name, mobile number, policy/order number, expiry/order date, etc.).
- **Call event JSONL files** (`events/events_*.jsonl`): line-by-line call logs emitted by the calling system (`call.initiated`, `call.telephony_failed`, `call.connected`, `call.ended`, `disposition.set`, and human supervisor `disposition.override`).

### What happens step by step

1. **Configuration & Schemas (`solution/config.py`, `solution/models/schema_loader.py`):**
   Reads the rules from `assignment-a-data/schemas/` (`motor_renewal.json`, `policy_feedback.json`, `dispositions.csv`) and loads your encryption secret from environment variables (`AU_SECRET_KEY` or `AU_HMAC_KEY` + `AU_FERNET_KEY`).
2. **Validation & Cleaning (`solution/validation/`):**
   - Matches messy CSV column names (like `"Insured Name"`, `"Mobile No"`, `"Expiry Date"`) using aliases (`header.py`).
   - Normalizes Indian mobile numbers from formats like `9932302346`, `08851268612`, `(+91) 9247939334`, or `+91 79328 96340` into `+91XXXXXXXXXX` (`phone.py`).
   - Normalizes dates (`DD/MM/YYYY`, `DD-MM-YYYY`, `DD-Mon-YYYY`, `YYYY-MM-DD`) into `YYYY-MM-DD` (`date.py`).
   - Cleans currency amounts (`"₹27,554"`, `"INR 25301.4"`) into `27554.00` (`decimal.py`).
   - Normalizes language codes (`"HI"` -> `"hi"`) (`enum.py`).
3. **PII Protection (`solution/security/pii.py`):**
   - Plaintext customer names and full phone numbers are **never** stored in the database.
   - Each canonical phone (`+91XXXXXXXXXX`) is turned into a deterministic **HMAC-SHA256 hash** (`phone_hash`) so duplicates and call events still match accurately without storing the real number.
   - `customer_name` and the phone's last 4 digits (`phone_last4`) are encrypted using **Fernet** (`AES-128-CBC + HMAC-SHA256`).
   - PII keys (like `phone` and `customer_name`) are stripped out of event JSON payloads before storing them.
4. **Lead Ingestion (`solution/ingestion/leads.py`):**
   - Skips completely blank CSV rows, numbers non-blank rows starting at `1`, rejects bad rows into the `rejections` table, and merges duplicate leads (matching `(workflow_id, phone_hash)`) while keeping the lead's first-seen `batch_id` permanently.
5. **Event Ingestion & Orphan Reconciliation (`solution/ingestion/events.py`, `solution/ingestion/reconcile.py`):**
   - Appends valid linked events into the `events` table (an append-only interaction ledger).
   - Drops duplicate `event_id` lines and ignores unknown disposition codes (`is_ignored = 1`).
   - If a call event arrives **before** its customer lead was uploaded (e.g., an event in `drop_1` whose lead only arrives in `drop_2`), it is temporarily saved in `orphan_events` and automatically promoted into `events` when `drop_2` is ingested!
6. **Database Storage (`solution/db/schema.py`, `solution/db/connection.py`):**
   Everything is saved inside a single SQLite database file (`ledger.db` by default) across **6 tables**: `drops_ingested`, `leads`, `events`, `orphan_events`, `rejections`, and `data_quality`.
7. **Report Generation (`solution/reporting/`):**
   Reads the append-only ledger, converts UTC timestamps to **IST (`+05:30`)**, computes call outcomes, overrides, and lead buckets, and exports the 5 contract reports (`batch_summary.csv`, `daily_overview.csv`, `lead_report.csv`, `rejections.csv`, `data_quality.json`).

### Practical Real Example from Your Existing Data

- **Duplicate lead merging (`MOT/2026/100846`):**
  - In `data/drop_1/leads/motor_renewal/batch_001.csv`, **Aarav Das** appears with phone `8851268612`, policy `MOT/2026/100846`, expiry `20/12/2026`, premium `12766.44`, language `mr`.
  - Later in `data/drop_2/leads/motor_renewal/batch_003.csv` (which uses renamed headers `"Insured Name"`, `"Mobile No"`, `"Policy No"`, plus an extra ignored column `"agent_notes"`), the same person appears with phone `08851268612` and premium `13033`.
  - Both phones normalize to `+918851268612` and hash to the exact same `phone_hash`. The pipeline **merges** them into one lead (`lead_id = 78`), permanently keeps `batch_id = batch_001`, updates `premium_amount` to `13033.00`, and counts `1` merged duplicate.
- **Cross-drop orphan reconciliation (`evt_00001323`):**
  - Line 2 of `data/drop_1/events/events_0001.jsonl` is event `evt_00001323` (`call.telephony_failed` for phone `91-77187-50036`). That phone is **not** in `drop_1` leads, so after `drop_1` it sits in `orphan_events`.
  - When `drop_2` is ingested, that customer's lead arrives in `batch_003.csv`, and `reconcile_orphans()` automatically moves `evt_00001323` from `orphan_events` into `events` (`lead_id = 1626`).

---

## 2. How to Run the Project on Windows (VS Code Terminal / PowerShell)

All commands below assume you are using **PowerShell** inside VS Code Terminal (`Ctrl + ~` in VS Code).

### Step 1: Open the project root directory

**Always check where you are first.** You must be inside the repository root (`sde-assignment-junior-2026/`), **not** inside `solution/` or `data/`:

```powershell
# Navigate to your cloned repository folder
cd path\to\sde-assignment-junior-2026
Get-Location
```

Expected output:
```text
Path
----
...\sde-assignment-junior-2026
```

### Step 2: Check your Python version

The assignment specification requires **Python 3.11+**. Check your installed version:

```powershell
python --version
```

*(Note: The codebase strictly follows Python 3.11+ standard library patterns and type annotations. It is also compatible with Python 3.10+ environments with modern typing support.)*

### Step 3: Virtual environment (`.venv`) and dependencies

If you do not have a virtual environment set up yet, you can create a `.venv` or install requirements in your active Python 3.11+ environment:

You have two choices:
- **Option A (Quickest — use your current Python):** Just verify packages are installed:
  ```powershell
  python -m pip install -r requirements.txt
  ```
- **Option B (Recommended clean isolation — create `.venv`):**
  ```powershell
  python -m venv .venv
  .\.venv\Scripts\Activate.ps1
  python -m pip install -r requirements.txt
  ```
  > **If PowerShell blocks `Activate.ps1` with an execution policy error**, run this once in your terminal and then activate again:
  > ```powershell
  > Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
  > .\.venv\Scripts\Activate.ps1
  > ```
  When active, your prompt starts with `(.venv) PS ...\sde-assignment-junior-2026>`.
  *(If using Command Prompt `cmd.exe` instead of PowerShell, activation is `.venv\Scripts\activate.bat`.)*

### Step 4: Generate and set the required encryption key (Once per database)

`solution/config.py` requires either:
- **`AU_SECRET_KEY`** (recommended — automatically derives both the HMAC key and Fernet key), **OR**
- **Both `AU_HMAC_KEY` and `AU_FERNET_KEY`** together.

> [!IMPORTANT]
> **Key Lifecycle: Generate once, reuse across all drops and reports**
> You must generate `AU_SECRET_KEY` **once** when setting up a database, and **keep the exact same key** for:
> 1. Ingesting Drop 1 (`ingest data/drop_1`)
> 2. Ingesting Drop 2 (`ingest data/drop_2`)
> 3. Generating Reports (`report --out reports/`)
> 4. Any subsequent verification queries or re-runs against `ledger.db`
>
> **Why is this critical?**
> - **Phone matching across drops:** The HMAC key creates deterministic hashes of phone numbers (`phone_hash`). If the key changes between Drop 1 and Drop 2, the same customer will produce a different hash and cross-drop deduplication and event linking will fail.
> - **Fernet decryption at report time:** `customer_name` and `phone_last4` are encrypted using Fernet. If you run `report` with a different key than the one used during `ingest`, Fernet cannot decrypt the tokens and will fail with `cryptography.fernet.InvalidToken`.

Generate a cryptographically secure 32-byte URL-safe secret directly using Python's standard library `secrets` module, and store it into the PowerShell session environment variable without printing it to the terminal screen:

```powershell
$env:AU_SECRET_KEY = python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Verify that the key is set and non-empty **without** displaying the secret:
```powershell
[bool]$env:AU_SECRET_KEY
```
*(Prints `True`. You can also verify length via `$env:AU_SECRET_KEY.Length` without exposing the secret.)*

### Step 5: Verify your existing generated data folder (do NOT regenerate it)

Your repository already has `data/` containing:
- `data\drop_1\leads\motor_renewal\batch_001.csv` (416 non-blank rows, 2 blank rows)
- `data\drop_1\leads\motor_renewal\batch_002.csv` (350 non-blank rows, 3 blank rows)
- `data\drop_1\leads\policy_feedback\batch_001.csv` (377 non-blank rows, 0 blank rows)
- `data\drop_1\leads\policy_feedback\batch_002.csv` (372 non-blank rows, 1 blank row)
- `data\drop_1\events\events_0001.jsonl` (4,247 non-empty lines)
- `data\drop_2\leads\motor_renewal\batch_003.csv` (266 non-blank rows, 4 blank rows)
- `data\drop_2\leads\policy_feedback\batch_003.csv` (276 non-blank rows, 0 blank rows)
- `data\drop_2\events\events_1001.jsonl` (4,498 non-empty lines)

Verify it exists without modifying anything:
```powershell
Get-ChildItem -Path data -Recurse -File | Select-Object FullName, Length
```

### Step 6: Ingest Drop 1

From the repository root:

```powershell
python -m solution.pipeline --db ledger.db ingest data/drop_1
$LASTEXITCODE
```

### Step 7: Ingest Drop 2

From the repository root (in the same terminal with the same `$env:AU_SECRET_KEY`):

```powershell
python -m solution.pipeline --db ledger.db ingest data/drop_2
$LASTEXITCODE
```

### Step 8: Generate the five reports

From the repository root:

```powershell
python -m solution.pipeline --db ledger.db report --out reports/
$LASTEXITCODE
```

### Step 9: Run automated tests

From the repository root:

```powershell
python -m pytest solution/tests/ -v
```

---

## 3. What Happens After Every Command

Notice a key CLI design detail in `solution/pipeline.py`: **on success, `ingest` and `report` do not print noisy logs to the terminal — they return exit code `0` silently.** Errors print clearly to `stderr` and return a non-zero exit code (such as `2`). Checking `$LASTEXITCODE` (in PowerShell) or checking the created files confirms success.

| Command (run from repository root) | What it actually does | Expected terminal output | Files created or updated & where | How to confirm success |
|---|---|---|---|---|
| `python -m pip install -r requirements.txt` | Installs `fastapi`, `uvicorn`, `httpx`, `pytest`, `cryptography` | `Requirement already satisfied...` or `Successfully installed...` | Updates Python packages in `.venv` or global Python | `python -c "import cryptography, pytest; print('OK')"` prints `OK` |
| `$env:AU_SECRET_KEY = python -c "import secrets; print(secrets.token_urlsafe(32))"` | Generates a random 32-byte key and stores it in PowerShell session memory | *(No output — prompt returns immediately)* | None on disk | `[bool]$env:AU_SECRET_KEY` prints `True` (safe check without printing the secret) |
| `python -m solution.pipeline --db ledger.db ingest data/drop_1` | Initializes all 6 SQLite tables (`init_db`), loads schemas from `assignment-a-data/schemas/`, ingests Drop 1 CSVs + JSONL, reconciles orphans, records `drop_1` in `drops_ingested`, commits atomically | *(Silent on success)*; `$LASTEXITCODE` is **`0`** | Creates `./ledger.db` in repository root (and temporary `-wal`/`-shm` files while SQLite writes) | Check `Test-Path ledger.db` is `True` and `$LASTEXITCODE -eq 0` |
| `python -m solution.pipeline --db ledger.db ingest data/drop_2` | Skips `drop_1` if re-run, ingests Drop 2 CSVs (`batch_003.csv` with alias headers) + `events_1001.jsonl`, promotes Drop 1 orphans whose leads arrived in Drop 2, updates `data_quality`, records `drop_2` | *(Silent on success)*; `$LASTEXITCODE` is **`0`** | Updates `./ledger.db` | `$LASTEXITCODE -eq 0`; run a `data_quality` query or generate reports |
| `python -m solution.pipeline --db ledger.db report --out reports/` | Reads `leads`, `events`, `orphan_events`, `rejections`, `data_quality`; decrypts `phone_last4_encrypted` for masking; converts UTC timestamps to IST; creates output directory if missing; writes 5 files | *(Silent on success)*; `$LASTEXITCODE` is **`0`** | Creates `reports/` folder with:<br>- `batch_summary.csv`<br>- `daily_overview.csv`<br>- `lead_report.csv`<br>- `rejections.csv`<br>- `data_quality.json` | Run `Get-ChildItem reports/` and verify all 5 files exist with non-zero sizes |
| `python -m pytest solution/tests/ -v` | Discovers and runs all unit and integration tests under `solution/tests/` using isolated temp directories (`tmp_path`) | Lists each test `PASSED [  %]` ending with `133 passed in ~3.5s` | Updates `.pytest_cache/` only (does **not** touch `ledger.db` or `data/`) | Final line shows `133 passed` and exit code `0` |

---

## 4. Understanding the Five Output Reports

All 5 files are written inside the `reports/` folder in your project root.

### How to open them on Windows
- **Best for exact inspection (recommended):** Click any `.csv` or `.json` file directly in **VS Code Explorer** (`reports/` -> click file). VS Code displays raw UTF-8 text without corrupting timestamps, dates, or `true`/`false` booleans.
- **Quick preview in PowerShell:**
  ```powershell
  Get-Content reports\batch_summary.csv
  Get-Content reports\data_quality.json
  Get-Content reports\lead_report.csv -TotalCount 10
  ```
- **If using Microsoft Excel:** Do **not** just double-click CSVs if you want to inspect exact formatting — Excel auto-reformats ISO dates (`2026-10-01`), strips timestamps, and turns `true`/`false` into `TRUE`/`FALSE`. Use **Data -> From Text/CSV -> File Origin: 65001: Unicode (UTF-8)** if you open in Excel.

---

### Report 1: `reports/batch_summary.csv`
- **What it contains:** Lead funnel counts grouped by `(workflow_id, batch_id)`, sorted by `workflow_id, batch_id`.
- **Columns:**
  - `workflow_id`: `motor_renewal` or `policy_feedback`
  - `batch_id`: CSV filename stem where the lead was **first seen** (`batch_001`, `batch_002`, `batch_003`)
  - `total_leads`: Unique accepted leads assigned permanently to that batch
  - `yet_to_reach_out`: Leads with `0` linked `call.initiated` events
  - `in_progress_telephony_failed`: Leads with ≥ 1 call, no terminal disposition, and **every** call has a `call.telephony_failed` event
  - `in_progress_dialed`: Leads with ≥ 1 call, no terminal disposition, and at least one call that did not telephony-fail
  - `completed`: Leads with at least one call whose effective disposition has `is_terminal=true`
- **Observed example on your `data/` after Drop 1 + Drop 2:**
  ```csv
  workflow_id,batch_id,total_leads,yet_to_reach_out,in_progress_telephony_failed,in_progress_dialed,completed
  motor_renewal,batch_001,389,106,7,105,171
  motor_renewal,batch_002,326,86,5,102,133
  motor_renewal,batch_003,236,68,5,60,103
  policy_feedback,batch_001,356,93,7,102,154
  policy_feedback,batch_002,344,99,9,98,138
  policy_feedback,batch_003,241,60,5,59,117
  ```
- **What to check:** In every row, `yet_to_reach_out + in_progress_telephony_failed + in_progress_dialed + completed == total_leads`. Sum of `total_leads` across all rows = `389+326+236+356+344+241 = 1,892` (exact match to `COUNT(*) FROM leads`).

---

### Report 2: `reports/daily_overview.csv`
- **What it contains:** One row per `(workflow_id, date)` in **IST (`+05:30`)** that had at least one linked `call.initiated`, sorted by `workflow_id, date`.
- **Columns:**
  - `workflow_id`: Workflow name
  - `date`: IST date (`YYYY-MM-DD`) of `call.initiated`
  - `total_calls`: Number of linked `call.initiated` events on that IST date
  - `connected_calls`: Subset of those calls that also have a linked `call.connected` event
  - `connectivity_rate`: `connected_calls / total_calls`, always formatted to **4 decimal places** (`0.5625`)
  - `success_leads`: **Distinct leads** that had at least one call initiated on that date whose effective disposition has `is_success=true` (`renewed` or `feedback_captured`)
- **Observed sample from your `data/` after Drop 1 + Drop 2:**
  ```csv
  workflow_id,date,total_calls,connected_calls,connectivity_rate,success_leads
  motor_renewal,2026-10-01,80,45,0.5625,8
  motor_renewal,2026-10-02,76,43,0.5658,9
  motor_renewal,2026-10-03,113,61,0.5398,13
  ```
- **What to check:** `45 / 80 = 0.5625`, `43 / 76 = 0.565789... -> 0.5658`. Connectivity rate must always be between `0.0000` and `1.0000` with 4 decimals, and `success_leads <= connected_calls`.

---

### Report 3: `reports/lead_report.csv`
- **What it contains:** One row per unique lead (`1,892` data rows + 1 header = `1,893` lines on your `data/`), sorted by `workflow_id, external_ref`.
- **Columns:**
  - `workflow_id`, `external_ref` (`policy_number` or `order_id`), `batch_id`
  - `phone_masked`: Always `+91******` + last 4 digits (e.g., `+91******1819`)
  - `bucket`: One of the 4 buckets (`yet_to_reach_out`, `completed`, `in_progress_telephony_failed`, `in_progress_dialed`)
  - `total_calls`, `connected_calls`
  - `last_call_at`: Latest `call.initiated` timestamp in IST (`+05:30`), or empty string if `total_calls == 0`
  - `current_disposition`: Effective disposition of the **most recent call (by initiated time) that has one**, or empty string if none
  - `ever_success`: `true` or `false`
- **Observed sample from your `data/` after Drop 1 + Drop 2:**
  ```csv
  workflow_id,external_ref,batch_id,phone_masked,bucket,total_calls,connected_calls,last_call_at,current_disposition,ever_success
  motor_renewal,MOT/2026/100000,batch_003,+91******1819,completed,4,1,2026-10-14T17:55:24+05:30,not_interested,false
  motor_renewal,MOT/2026/100002,batch_001,+91******5940,completed,1,1,2026-10-04T17:10:45+05:30,renewed,true
  motor_renewal,MOT/2026/100003,batch_001,+91******0341,yet_to_reach_out,0,0,,,false
  motor_renewal,MOT/2026/100005,batch_001,+91******6413,in_progress_dialed,4,2,2026-10-13T08:27:51+05:30,payment_link_sent,false
  motor_renewal,MOT/2026/100009,batch_002,+91******2880,in_progress_telephony_failed,2,0,2026-10-06T09:29:10+05:30,,false
  ```
- **What to check:** No full phone numbers or customer names appear anywhere; `yet_to_reach_out` rows have `total_calls=0, connected_calls=0` and empty `last_call_at` and `current_disposition`.

---

### Report 4: `reports/rejections.csv`
- **What it contains:** Every invalid non-blank CSV lead row (`108` rows on your `data/`), sorted by `workflow_id, batch_id, row_number`. **Contains NO PII.**
- **Columns:** `workflow_id`, `batch_id`, `row_number` (1-based non-blank data row index excluding header), `reason`.
- **Observed sample from your `data/`:**
  ```csv
  workflow_id,batch_id,row_number,reason
  motor_renewal,batch_001,54,missing_required:policy_number
  motor_renewal,batch_001,193,invalid_phone
  motor_renewal,batch_001,299,missing_required:phone
  ```
- **What to check:** Row count equals `lead_rows_rejected` in `data_quality.json` (`108`). No customer names or phone numbers appear.

---

### Report 5: `reports/data_quality.json`
- **What it contains:** Pipeline-wide audit counters across all ingested drops.
- **Observed output on your `data/` after Drop 1 ONLY:**
  ```json
  {
    "lead_rows_total": 1515,
    "lead_rows_rejected": 71,
    "rejections_by_reason": {
      "invalid_phone": 24,
      "missing_required:customer_name": 20,
      "missing_required:order_id": 8,
      "missing_required:phone": 10,
      "missing_required:policy_number": 9
    },
    "duplicate_lead_rows_merged": 29,
    "field_warnings": 83,
    "events_total": 4247,
    "malformed_events": 4,
    "duplicate_events_dropped": 83,
    "orphan_events": 1123,
    "unknown_disposition_events": 10
  }
  ```
- **Observed output on your `data/` after Drop 1 + Drop 2:**
  ```json
  {
    "lead_rows_total": 2057,
    "lead_rows_rejected": 108,
    "rejections_by_reason": {
      "invalid_phone": 42,
      "missing_required:customer_name": 25,
      "missing_required:order_id": 13,
      "missing_required:phone": 14,
      "missing_required:policy_number": 14
    },
    "duplicate_lead_rows_merged": 57,
    "field_warnings": 122,
    "events_total": 8745,
    "malformed_events": 8,
    "duplicate_events_dropped": 237,
    "orphan_events": 462,
    "unknown_disposition_events": 18
  }
  ```
- **Two math invariants you should always check:**
  1. **Leads accounting:** `unique_leads (1,892) + lead_rows_rejected (108) + duplicate_lead_rows_merged (57) = 2,057 == lead_rows_total`.
  2. **Events accounting:** `events in ledger (8,038) + orphan_events (462) + malformed_events (8) + duplicate_events_dropped (237) = 8,745 == events_total`.

---

## 5. How to Open & Visually Inspect Your SQLite Database

### Where is your `.db` file located?
- **Currently:** No `.db` file exists in the repository root yet because the database is created on demand when you run `ingest` or `report`.
- **After you run `ingest`:** When you pass `--db ledger.db` (or omit `--db`, since `DEFAULT_DB_PATH` in `solution/config.py` is `ledger.db`), your database file is saved right here:
  **`ledger.db`** (in the repository root)
- **Note on `-wal` and `-shm` files:** Because `solution/db/connection.py` enables SQLite **WAL mode** (`PRAGMA journal_mode = WAL`), you may sometimes see `ledger.db-wal` and `ledger.db-shm` next to `ledger.db`. Do **not** delete those sidecar files while `ledger.db` is open or right after a write — they hold recent WAL data until SQLite checkpoints them back into `ledger.db`!

### Do you HAVE to use CMD, or can you use a visual GUI?
**You do NOT have to use CMD if you prefer a visual GUI!** Right now, neither `DB Browser for SQLite` nor `sqlite3.exe` is installed on your machine in the default Program Files folders. You have **two easy options**:
- **Option A (Visual GUI — DB Browser for SQLite):** Free Windows app where you click through tables, filter columns, and run SQL in a visual window (no command-line SQL needed).
- **Option B (Built-in Python in VS Code Terminal / CMD / PowerShell — zero extra downloads):** Python already ships with SQLite built in (`import sqlite3`), so you can run one-line queries right in VS Code Terminal without installing any extra app.

---

### Option A: Visual Inspection Step-by-Step with DB Browser for SQLite

1. **Install DB Browser for SQLite (one time):**
   Download **"DB Browser for SQLite - Standard installer for 64-bit Windows"** from `https://sqlitebrowser.org/dl/` and run the installer.
2. **Open your database:**
   - Launch **DB Browser for SQLite**.
   - Click **Open Database** (top-left toolbar).
   - Browse to your cloned repository folder (`sde-assignment-junior-2026/`).
   - Select **`ledger.db`** and click **Open**.
3. **View all 6 tables (`Database Structure` tab):**
   Click the **`Database Structure`** tab at the top. Expand **Tables (6)** to see:
   - **`drops_ingested`** — Columns: `drop_path` (PK), `ingested_at`. Records each ingested drop path so re-runs skip it safely.
   - **`leads`** — Columns: `lead_id` (PK), `workflow_id`, `phone_hash`, `phone_last4_encrypted`, `customer_name_encrypted`, `batch_id`, `external_ref`, `extra_fields_json`, `created_at`, `updated_at`. One row per unique `(workflow_id, phone_hash)`. Notice `customer_name_encrypted` and `phone_last4_encrypted` show `gAAAAAB...` encrypted strings!
   - **`events`** — Columns: `event_row_id` (PK), `event_id` (UNIQUE), `event_type`, `workflow_id`, `call_id`, `lead_id` (FK -> `leads.lead_id`), `occurred_at` (UTC), `payload_json`, `is_ignored` (`0` or `1`), `ingested_at`. Your append-only call interaction ledger.
   - **`orphan_events`** — Columns: `orphan_id` (PK), `event_id` (UNIQUE), `event_type`, `workflow_id`, `call_id`, `phone_hash`, `occurred_at`, `payload_json`, `ingested_at`. Holds events that could not yet link to a lead (notice there is **no `raw_json` column** and no plaintext phone!).
   - **`rejections`** — Columns: `rejection_id` (PK), `workflow_id`, `batch_id`, `row_number`, `reason`. Non-PII log of rejected CSV rows.
   - **`data_quality`** — Columns: `metric_name` (PK), `metric_value`. Running counters for `data_quality.json`.
   *(You will also see `sqlite_sequence`, an internal SQLite system table used for `AUTOINCREMENT` IDs — that is normal.)*
4. **Browse table rows visually (`Browse Data` tab):**
   - Click the **`Browse Data`** tab.
   - Use the **Table:** dropdown (top-left of the grid) to switch between `leads`, `events`, `orphan_events`, `rejections`, `data_quality`, and `drops_ingested`.
   - Type in the small **Filter** box under any column header (for example, under `external_ref` type `MOT/2026/100846`, or under `is_ignored` type `1`).
5. **Run SQL queries visually (`Execute SQL` tab):**
   - Click the **`Execute SQL`** tab.
   - Paste any SQL query from the section below into the top white editor box.
   - Click the **blue Play/Triangle button** (or press **`F5`** / **`Ctrl + Enter`**) to see results in the bottom grid.
6. **How to refresh the view after ingesting Drop 2 (or running another command):**
   - **Important:** Before running `ingest` in VS Code Terminal, if DB Browser has uncommitted edits (it shouldn't if you only read), don't edit cells. Even better, click **Close Database** before running `ingest`, or after running `ingest` in PowerShell, click the **Refresh** circular arrow icon in **`Browse Data`**, or **File -> Close Database** then **File -> Open Recent -> ledger.db** so DB Browser re-reads both `ledger.db` and any `-wal` updates cleanly.

---

### Option B: Quick SQLite Queries in VS Code Terminal / PowerShell (No extra software needed)

You can run any SQL query using Python's built-in `sqlite3` right inside VS Code Terminal (make sure you are in the repository root):

```powershell
python -c "import sqlite3; c=sqlite3.connect('ledger.db'); print(c.execute('SELECT metric_name, metric_value FROM data_quality ORDER BY metric_name').fetchall())"
```

---

### Verified SQL Queries (Copy-Paste into DB Browser `Execute SQL` tab or Python)

These exact table and column names match `solution/db/schema.py`:

```sql
-- 1. Check row counts across all 6 application tables
SELECT 'drops_ingested' AS table_name, COUNT(*) AS rows FROM drops_ingested
UNION ALL SELECT 'leads', COUNT(*) FROM leads
UNION ALL SELECT 'events', COUNT(*) FROM events
UNION ALL SELECT 'orphan_events', COUNT(*) FROM orphan_events
UNION ALL SELECT 'rejections', COUNT(*) FROM rejections
UNION ALL SELECT 'data_quality', COUNT(*) FROM data_quality;

-- 2. View ingested drops
SELECT drop_path, ingested_at FROM drops_ingested;

-- 3. View all data_quality metrics
SELECT metric_name, metric_value FROM data_quality ORDER BY metric_name;

-- 4. Count leads per workflow and initial batch
SELECT workflow_id, batch_id, COUNT(*) AS lead_count
FROM leads
GROUP BY workflow_id, batch_id
ORDER BY workflow_id, batch_id;

-- 5. Count events by type and ignored status
SELECT event_type, is_ignored, COUNT(*) AS cnt
FROM events
GROUP BY event_type, is_ignored
ORDER BY event_type, is_ignored;

-- 6. Inspect unknown disposition events (flagged is_ignored = 1)
SELECT event_id, workflow_id, call_id, payload_json, is_ignored
FROM events
WHERE is_ignored = 1
LIMIT 20;

-- 7. Inspect rejections grouped by reason
SELECT reason, COUNT(*) AS cnt
FROM rejections
GROUP BY reason
ORDER BY reason;

-- 8. Verify NO plaintext PII in leads (look at phone_hash and Fernet tokens)
SELECT lead_id, workflow_id, batch_id, external_ref,
       substr(phone_hash, 1, 16) || '...' AS phone_hash_prefix,
       substr(phone_last4_encrypted, 1, 20) || '...' AS phone_last4_token,
       substr(customer_name_encrypted, 1, 20) || '...' AS customer_name_token,
       extra_fields_json
FROM leads
LIMIT 10;

-- 9. Verify NO plaintext 'phone' or 'customer_name' keys leaked into events/orphan_events JSON
SELECT COUNT(*) AS suspicious_events_rows
FROM events
WHERE payload_json LIKE '%"phone"%' OR payload_json LIKE '%"customer_name"%';

SELECT COUNT(*) AS suspicious_orphan_rows
FROM orphan_events
WHERE payload_json LIKE '%"phone"%' OR payload_json LIKE '%"customer_name"%';

-- 10. Trace the disposition override example (call_00000376)
SELECT event_id, event_type, occurred_at, payload_json, is_ignored
FROM events
WHERE call_id = 'call_00000376'
ORDER BY occurred_at, event_id;
```

---

## 6. Manual Correctness Verification Checklist

Use this checklist after ingesting `drop_1` -> `drop_2` -> `report`:

| # | Area | How to check manually | What **CORRECT** looks like | What **INCORRECT** looks like |
|---|---|---|---|---|
| 1 | **Lead ingestion** | Compare `data_quality.json` `lead_rows_total` against CSV lines (`416+350+377+372+266+276`) | `lead_rows_total == 2057`, and `leads (1892) + rejected (108) + merged (57) == 2057` | Blank rows counted, or `leads + rejected + merged != lead_rows_total` |
| 2 | **Invalid rows & rejections** | Open `data/drop_1/leads/motor_renewal/batch_001.csv` non-blank data row 54 & 193 vs `reports/rejections.csv` | Row 54 (Varun Patel, blank `policy_number`) -> `missing_required:policy_number`; Row 193 (`+1 415 555 0134`) -> `invalid_phone` | Header counted as row 1, blank rows shift row numbers, or customer name/phone appears in `rejections.csv` |
| 3 | **Duplicate lead merging** | Look up `MOT/2026/100846` in `lead_report.csv` and `leads` table | `batch_id` is still **`batch_001`** (not `batch_003`!), `premium_amount` in `extra_fields_json` is **`13033.00`** (updated from `batch_003`), `duplicate_lead_rows_merged == 57` | Two rows created for the same person, or `batch_id` overwritten to `batch_003` |
| 4 | **Phone normalization** | Check `MOT/2026/100846` (`8851268612` in `batch_001`, `08851268612` in `batch_003`) & `events_0001.jsonl` (`91-77187-50036`) | Both lead formats match the same `phone_hash`; `evt_00001323` links to its lead in `batch_003` | Leading `0` or `91-` treated as a different person or rejected |
| 5 | **PII encryption** | Inspect `leads`, `events`, `orphan_events`, and all 5 reports | `customer_name_encrypted` & `phone_last4_encrypted` start with `gAAAAAB...`; `phone_hash` is 64-char hex; `lead_report.csv` shows `+91******XXXX`; SQL query #9 returns `0` | Any readable customer name or 10-digit mobile number visible in SQLite or CSV/JSON reports |
| 6 | **Event ingestion** | Check `data_quality.json` event equation | `events (8038) + orphan_events (462) + malformed (8) + duplicates (237) == 8745 == events_total` | Sum does not equal `8745` (`4247 + 4498`) |
| 7 | **Duplicate event handling** | Check `duplicate_events_dropped` and `events.event_id` uniqueness | `duplicate_events_dropped == 237`, `COUNT(DISTINCT event_id) == COUNT(*)` in `events` | Duplicate `event_id` creates double calls or double `disposition.set` |
| 8 | **Orphan event reconciliation** | Check `orphan_events` after Drop 1 vs after Drop 2, and query `evt_00001323` | `orphan_events` drops from **`1123`** (after Drop 1) to **`462`** (after Drop 2); `evt_00001323` is in `events` (0 rows in `orphan_events`) | `orphan_events` stays at `1123`, or `evt_00001323` never leaves `orphan_events` |
| 9 | **Disposition overrides** | Run SQL query #10 for `call_00000376` and look up `MOT/2026/100297` in `lead_report.csv` | Both `disposition.set` (`language_barrier`) and `disposition.override` (`already_renewed_elsewhere`) remain in `events`; `lead_report.csv` shows `current_disposition=already_renewed_elsewhere` | Original `disposition.set` was deleted/overwritten, or `current_disposition` still says `language_barrier` |
| 10 | **Lead buckets** | Inspect `lead_report.csv` counts and examples | Counts on `data/`: `completed=816`, `in_progress_dialed=526`, `yet_to_reach_out=512`, `in_progress_telephony_failed=38` (total `1892`). `MOT/2026/100009` (`2` calls, `0` connected, no disp) is `in_progress_telephony_failed` | Unknown dispositions (`call_back`, `RENEWD`) put leads into `completed`, or leads with mixed failed+connected calls go into `telephony_failed` |
| 11 | **Daily connectivity rate** | Check `daily_overview.csv` row `motor_renewal,2026-10-01` | `80` total calls, `45` connected, `45 / 80 = 0.5625` (exactly 4 decimal places) | Rounded to 2 decimals (`0.56`), wrong timezone (UTC instead of IST), or connected > total |
| 12 | **Success leads** | Check `daily_overview.csv` `success_leads` vs `lead_report.csv` `ever_success` | `motor_renewal,2026-10-01` has `success_leads=8`; `lead_report.csv` has `399` `true` and `1493` `false` (`ever_success`) | A single lead called twice with `renewed` on the same day counted twice in `success_leads` |
| 13 | **Headers & sorting** | Inspect line 1 and ordering of each CSV | Exact column names from spec; sorted by: `(workflow_id, batch_id)`, `(workflow_id, date)`, `(workflow_id, external_ref)`, `(workflow_id, batch_id, row_number)` | Extra index column (`0,1,2...`), wrong sort order, or `True`/`False` instead of `true`/`false` |
| 14 | **Drop-level idempotency** | Re-run Drop 1 ingest (see Section 7 below) | Zero row changes, zero metric increases, byte-identical reports | Any counter doubles or rows duplicate |

---

## 7. Idempotency Testing (Re-Ingesting the Same Drop)

Idempotency means running `ingest` again on an already-ingested drop **must change nothing**.

Run these exact commands from the repository root (using the same `$env:AU_SECRET_KEY`):

```powershell
# 1. Record current DB counts before re-ingest
python -c "import sqlite3; c=sqlite3.connect('ledger.db'); print([(t, c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]) for t in ('leads','events','orphan_events','rejections')])"

# 2. Re-ingest Drop 1 (already ingested!)
python -m solution.pipeline --db ledger.db ingest data/drop_1
$LASTEXITCODE

# 3. Record DB counts after re-ingest (must be identical!)
python -c "import sqlite3; c=sqlite3.connect('ledger.db'); print([(t, c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]) for t in ('leads','events','orphan_events','rejections')])"

# 4. Generate reports into a fresh comparison folder
python -m solution.pipeline --db ledger.db report --out reports_recheck/

# 5. Compare every report file hash before vs after
foreach ($f in 'batch_summary.csv','daily_overview.csv','lead_report.csv','rejections.csv','data_quality.json') {
    $h1 = (Get-FileHash "reports\$f" -Algorithm SHA256).Hash
    $h2 = (Get-FileHash "reports_recheck\$f" -Algorithm SHA256).Hash
    Write-Host "$f identical=$($h1 -eq $h2)"
}
```

### What you should observe
- **Lead count:** Stays `1892` (does NOT double).
- **Event count:** Stays `8038`.
- **Duplicate counters:** `duplicate_lead_rows_merged` stays `57` and `duplicate_events_dropped` stays `237` (because `drops_ingested` sees `drop_1` was already ingested and exits `0` immediately without re-processing!).
- **Orphan count:** Stays `462`.
- **Generated reports:** All 5 lines print **`identical=True`**. (You can safely delete `reports_recheck/` afterward with `Remove-Item -Recurse -Force reports_recheck`.)

---

## 8. Running the Automated Test Suite (`pytest`)

From the repository root (notice `pytest` sets its own temporary test keys inside the tests, so it works even if `$env:AU_SECRET_KEY` isn't set):

```powershell
python -m pytest solution/tests/ -v
```

- **`PASSED`**: The test executed and every assertion succeeded. *(Actual observed run on this repo: **`133 passed in 3.48s`**.)*
- **`FAILED`**: The test ran, and an assertion failed (a bug or mismatch in logic — `pytest` prints the expected vs. actual values and file/line number).
- **`ERROR`**: The test couldn't even run properly (e.g., broken import, missing dependency like `cryptography`, or fixture setup failure).

> **Benign Windows Python 3.10 note:** After `133 passed in 3.48s`, you may occasionally see an `Exception ignored in: <function Console.__del__ ... pyreadline3 ... OSError: [WinError -1073741816]>` during process teardown. That comes from an installed third-party `pyreadline3` package shutting down on Windows and does **not** mean a test failed (exit code is still `0`).

### What to do if a test fails
1. Stop on the first failure with a shorter traceback:
   ```powershell
   python -m pytest solution/tests/ -x --tb=short
   ```
2. Re-run just that one test file (for example):
   ```powershell
   python -m pytest solution/tests/test_reports.py -vv
   ```
3. **Important perspective:** Passing all 133 unit/integration tests is necessary, **not** automatic proof that an unseen dataset or every report nuance is bug-free — always combine `pytest` with the manual data & report checks in Section 6.

---

## 9. Common Beginner Troubleshooting (Windows + Actual Code)

| Symptom / Error | Cause | Exact Fix |
|---|---|---|
| `python : The term 'python' is not recognized...` | Python not added to Windows `PATH`, or needs the `py` launcher | Try `py --version`. If it works, use `py` instead of `python` (or reinstall Python with **"Add python.exe to PATH"** checked). |
| `.venv\Scripts\Activate.ps1 cannot be loaded because running scripts is disabled` | Default Windows PowerShell execution policy | Run `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`, then run `.\.venv\Scripts\Activate.ps1`. |
| `No module named 'cryptography'` or `No module named 'pytest'` | Dependencies not installed in active Python / `.venv` | Check `where.exe python`, activate `.venv` if using one, and run `python -m pip install -r requirements.txt`. |
| `Configuration error: Missing required PII security keys. Set AU_SECRET_KEY...` (exit code `2`) | `$env:AU_SECRET_KEY` wasn't set in the **current** terminal window (or you created a `.env` file, which Python does **not** auto-load) | Load your persisted key (from `.env` or User env var), or for a brand-new database generate one via `$env:AU_SECRET_KEY = python -c "import secrets; print(secrets.token_urlsafe(32))"`. |
| `cryptography.fernet.InvalidToken` during `report` or second `ingest` | You changed `AU_SECRET_KEY` between runs on the same `ledger.db` | Switch back to the exact original `AU_SECRET_KEY` used when `ledger.db` was created; or if lost on a local test DB, delete `ledger.db*` and re-ingest cleanly with a persistent key. |
| `No module named solution` or `FileNotFoundError: Schemas directory not found` | Running from inside `solution/` or wrong folder | Navigate to the repository root (`cd path\to\sde-assignment-junior-2026`) and verify `Test-Path assignment-a-data\schemas` is `True`. |
| `sqlite3.OperationalError: database is locked` | Another program (like DB Browser with an open write lock) or another pipeline run is holding `ledger.db` | Close DB Browser / other Python processes, or click **Close Database** in DB Browser before running `ingest`. |
| `pipeline: error: Drop directory does not exist: ...` | Wrong path passed to `ingest` (e.g., typo like `drop1` instead of `data/drop_1`) | Check folder spelling with `Get-ChildItem data`. Use `data/drop_1` and `data/drop_2`. |
| `reports/` folder is empty or missing | You ran `ingest` (which only updates `ledger.db`) and forgot the `report` command | Run `python -m solution.pipeline --db ledger.db report --out reports/`. |
| SQLite database cannot be opened / looks empty in DB Browser | Either `ingest` hasn't run yet (`ledger.db` doesn't exist), or DB Browser opened a `0 KB` file you created by mistake, or stale WAL view | Verify `(Get-Item ledger.db).Length` is > 0 KB after `ingest`, close DB Browser, re-open `ledger.db`, and use **Refresh** after later drops. |

---

## 10. My First Successful Run (Complete Beginner Walkthrough)

Follow these exact steps from start to finish in **VS Code Terminal (PowerShell)**:

1. **Go to the project root and confirm your data is present:**
   ```powershell
   # In repository root
   Test-Path data\drop_1\leads\motor_renewal\batch_001.csv
   ```
   *(Should print `True`.)*

2. **Check Python and install dependencies (if not already installed):**
   ```powershell
   python --version
   python -m pip install -r requirements.txt
   ```

3. **Generate and set a cryptographically random secret key in this terminal session (never print the secret):**
   ```powershell
   $env:AU_SECRET_KEY = python -c "import secrets; print(secrets.token_urlsafe(32))"
   [bool]$env:AU_SECRET_KEY
   ```
   *(Prints `True`. Keep this terminal open — or persist the key safely in `.env`/Windows User env vars as explained below — until you finish Drop 1, Drop 2, and reports.)*

4. **Run automated tests first to verify your environment works:**
   ```powershell
   python -m pytest solution/tests/ -v
   ```
   *(Expect `133 passed` at the bottom.)*

5. **Ingest Drop 1:**
   ```powershell
   python -m solution.pipeline --db ledger.db ingest data/drop_1
   $LASTEXITCODE
   ```
   *(Expect `0` and a newly created `ledger.db` in the project root.)*

6. **Ingest Drop 2:**
   ```powershell
   python -m solution.pipeline --db ledger.db ingest data/drop_2
   $LASTEXITCODE
   ```
   *(Expect `0`.)*

7. **Generate all five reports:**
   ```powershell
   python -m solution.pipeline --db ledger.db report --out reports/
   Get-ChildItem reports/
   ```
   *(Expect all 5 files listed: `batch_summary.csv`, `daily_overview.csv`, `data_quality.json`, `lead_report.csv`, `rejections.csv`.)*

8. **Check your quality metrics and batch summary right away:**
   ```powershell
   Get-Content reports\data_quality.json
   Get-Content reports\batch_summary.csv
   ```
   Confirm `lead_rows_total` is `2057`, `events_total` is `8745`, and `orphan_events` is `462`.

9. **Inspect SQLite visually or via Python:**
   - **Visual:** Open **DB Browser for SQLite** -> **Open Database** -> `ledger.db` (in repository root) -> browse `leads` (confirm encrypted `gAAAAAB...` names/phones) and `events`.
   - **Terminal (zero extra apps):**
     ```powershell
     python -c "import sqlite3; c=sqlite3.connect('ledger.db'); print([(t, c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]) for t in ('drops_ingested','leads','events','orphan_events','rejections','data_quality')])"
     ```
     *(Expect `[('drops_ingested', 2), ('leads', 1892), ('events', 8038), ('orphan_events', 462), ('rejections', 108), ('data_quality', 9)]`.)*

10. **Verify idempotency (re-ingest Drop 1 and verify nothing changes):**
    ```powershell
    python -m solution.pipeline --db ledger.db ingest data/drop_1
    python -m solution.pipeline --db ledger.db report --out reports_recheck/
    Compare-Object (Get-Content reports\data_quality.json) (Get-Content reports_recheck\data_quality.json)
    Remove-Item -Recurse -Force reports_recheck
    ```
    *(Expect **no output** from `Compare-Object`, proving both files are identical.)*
