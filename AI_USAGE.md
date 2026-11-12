# AI Usage Disclosure

AI tools are allowed. We want to know **how** you used them, because using them well is a
skill. Honest answers are never held against you. Discovering undisclosed use is.

## Tools used
- Gemini

## What I used them for
- Breaking down the Assignment A specification (`README.md`, `GLOSSARY.md`, JSON schemas, and `dispositions.csv`) into an implementation checklist.
- Drafting the modular Python package under `solution/` (`validators.py`, `security.py`, `db.py`, `ingest.py`, `reporting.py`, `cli.py`) and initial `pytest` test cases in `tests/test_pipeline.py`.
- Structuring and formatting `SUBMISSION.md`.

## What I wrote or decided myself
- Chose SQLite in WAL mode with stdlib-only Python so the pipeline runs deterministically with zero external dependencies and fast execution.
- Designed the 5-table relational schema (`ingested_files`, `leads`, `lead_rejections`, `events`, `ingestion_counters`) using SHA-256 file checksums for idempotency and query-time linking so out-of-order drops (`drop_1` events arriving before `drop_2` leads) resolve automatically without re-ingesting earlier drops.
- Selected keyed HMAC-SHA256 (`phone_hash` and `customer_name` digest) plus `phone_masked` (`+91******XXXX`) so no plaintext PII is stored at rest while preserving deterministic joins.
- Decided to exclude unknown disposition codes from effective disposition calculation so invalid codes never mask valid outcomes, and to represent malformed event phones as `invalid:<hash>` so they accurately count as `orphan_events`.

## Where the AI was wrong or unhelpful
- During initial file generation, the AI's setup script wrote an unescaped literal newline inside `f.write("\n")` in `solution/reporting.py`, causing a `SyntaxError: unterminated string literal` on the first CLI run. I caught it from the Python traceback and fixed the string escape before re-running.
- The AI initially suggested running `pytest -v` directly, which failed with `ModuleNotFoundError: No module named 'solution'` until invoked via `python -m pytest -v` so the repository root was included on `sys.path`.

## How I verified AI-generated code
- Generated synthetic datasets via `assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data` and executed incremental ingestion (`drop_1`, `drop_2`, and duplicate re-ingest of `drop_1`) followed by `report --out reports/`.
- Verified all 5 output artifacts (`data_quality.json`, `batch_summary.csv`, `daily_overview.csv`, `lead_report.csv`, `rejections.csv`), including confirming the `0.5625` connectivity rate on `motor_renewal` (`2026-10-01`).
- Inspected the raw SQLite binary file in tests to verify zero plaintext phone numbers or customer names exist at rest, and ran `python -m pytest -v` to confirm all unit and integration tests pass.