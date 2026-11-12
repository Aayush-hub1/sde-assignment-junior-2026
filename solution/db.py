from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS ingested_files (
    file_path TEXT PRIMARY KEY,
    file_sha256 TEXT NOT NULL,
    file_kind TEXT NOT NULL,
    ingested_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingestion_counters (
    metric_name TEXT PRIMARY KEY,
    metric_value INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS leads (
    lead_id INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id TEXT NOT NULL,
    phone_hash TEXT NOT NULL,
    phone_masked TEXT NOT NULL,
    external_ref TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    attributes_json TEXT NOT NULL,
    UNIQUE(workflow_id, phone_hash)
);

CREATE INDEX IF NOT EXISTS idx_leads_workflow_phone ON leads(workflow_id, phone_hash);
CREATE INDEX IF NOT EXISTS idx_leads_workflow_ref ON leads(workflow_id, external_ref);

CREATE TABLE IF NOT EXISTS lead_rejections (
    rejection_id INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    row_number INTEGER NOT NULL,
    reason TEXT NOT NULL,
    UNIQUE(workflow_id, batch_id, row_number)
);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    call_id TEXT NOT NULL,
    occurred_at_utc TEXT NOT NULL,
    occurred_at_ist TEXT NOT NULL,
    ist_date TEXT NOT NULL,
    phone_hash TEXT,
    payload_json TEXT NOT NULL,
    is_unknown_disposition INTEGER NOT NULL DEFAULT 0,
    ingest_seq INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_workflow_phone ON events(workflow_id, phone_hash);
CREATE INDEX IF NOT EXISTS idx_events_call_id ON events(workflow_id, call_id);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
"""


def connect_db(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    return conn


def increment_counter(conn: sqlite3.Connection, metric_name: str, delta: int) -> None:
    if delta == 0:
        return
    conn.execute(
        """
        INSERT INTO ingestion_counters (metric_name, metric_value)
        VALUES (?, ?)
        ON CONFLICT(metric_name) DO UPDATE SET
            metric_value = ingestion_counters.metric_value + excluded.metric_value
        """,
        (metric_name, delta),
    )


def get_counters(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT metric_name, metric_value FROM ingestion_counters").fetchall()
    return {row["metric_name"]: int(row["metric_value"]) for row in rows}
