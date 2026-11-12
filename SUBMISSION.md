# Submission

## Candidate
- **Name:** Aayush Kumar
- **Assignment chosen:** A (Data)
- **Time spent (honest estimate):** 4.5h

## How to run
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Generate synthetic drops
python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data

# Ingest drops incrementally and generate the 5 reports
python -m solution.cli ingest data/drop_1
python -m solution.cli ingest data/drop_2
python -m solution.cli report --out reports/

# Run automated unit and integration tests
python -m pytest -v


CSV Leads ──► Header & Schema Validator ──► HMAC-SHA256 PII Hasher ──► SQLite (leads / lead_rejections)
                                                                
  JSONL Events ─► IST Parser & Event Dedupe ─► SQLite (events ledger) ───────────┴──► Query-Time Linker ──► 5 Reports