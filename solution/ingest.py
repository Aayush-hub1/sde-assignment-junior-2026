from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from solution.db import increment_counter
from solution.security import hash_pii, mask_phone
from solution.validators import (
    normalize_header,
    normalize_phone,
    parse_timestamp_ist,
    validate_field_value,
)


def compute_file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def is_file_already_ingested(conn: sqlite3.Connection, file_key: str, sha256: str) -> bool:
    row = conn.execute(
        "SELECT file_sha256 FROM ingested_files WHERE file_path = ?",
        (file_key,),
    ).fetchone()
    return row is not None and row["file_sha256"] == sha256


def mark_file_ingested(conn: sqlite3.Connection, file_key: str, sha256: str, kind: str) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO ingested_files (file_path, file_sha256, file_kind, ingested_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(file_path) DO UPDATE SET
            file_sha256 = excluded.file_sha256,
            ingested_at = excluded.ingested_at
        """,
        (file_key, sha256, kind, now_iso),
    )


def _build_header_map(raw_headers: list[str], fields: list[dict[str, Any]]) -> dict[int, str]:
    alias_to_field: dict[str, str] = {}
    for field in fields:
        fname = field["name"]
        alias_to_field[normalize_header(fname)] = fname
        for alias in field.get("aliases", []):
            alias_to_field[normalize_header(alias)] = fname

    col_idx_to_field: dict[int, str] = {}
    for idx, raw_h in enumerate(raw_headers):
        norm_h = normalize_header(raw_h)
        if norm_h in alias_to_field:
            col_idx_to_field[idx] = alias_to_field[norm_h]
    return col_idx_to_field


def ingest_lead_file(
    conn: sqlite3.Connection,
    csv_path: Path,
    workflow_schema: dict[str, Any],
) -> None:
    file_key = str(csv_path.resolve())
    sha256 = compute_file_sha256(csv_path)
    if is_file_already_ingested(conn, file_key, sha256):
        return

    workflow_id = workflow_schema["workflow_id"]
    batch_id = csv_path.stem
    fields = workflow_schema["fields"]
    ref_field = next(f["name"] for f in fields if f.get("reference"))
    pii_fields = {f["name"] for f in fields if f.get("pii")}
    defaults = {f["name"]: f["default"] for f in fields if "default" in f}

    rows_total = 0
    rows_rejected = 0
    duplicates_merged = 0
    field_warnings = 0

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        try:
            raw_headers = next(reader)
        except StopIteration:
            mark_file_ingested(conn, file_key, sha256, "leads")
            return

        col_map = _build_header_map(raw_headers, fields)

        for raw_row in reader:
            # Skip completely blank rows (not counted)
            if not raw_row or all(cell.strip() == "" for cell in raw_row):
                continue

            rows_total += 1
            row_number = rows_total

            # Extract trimmed cell values mapped to schema field names
            raw_values: dict[str, str] = {}
            for idx, cell in enumerate(raw_row):
                if idx in col_map:
                    fname = col_map[idx]
                    raw_values[fname] = cell.strip()

            # Walk fields in exact schema order
            rejection_reason: str | None = None
            row_warnings = 0
            parsed_values: dict[str, Any] = {}

            for field in fields:
                fname = field["name"]
                required = bool(field.get("required", False))
                val_str = raw_values.get(fname, "")

                if val_str == "":
                    if required:
                        rejection_reason = f"missing_required:{fname}"
                        break
                    parsed_values[fname] = None
                    continue

                is_valid, norm_val = validate_field_value(val_str, field)
                if not is_valid:
                    if field["type"] == "phone":
                        rejection_reason = "invalid_phone"
                        break
                    if required:
                        rejection_reason = f"missing_required:{fname}"
                        break
                    # Optional field present but invalid -> store null + count 1 field warning
                    parsed_values[fname] = None
                    row_warnings += 1
                else:
                    parsed_values[fname] = norm_val

            if rejection_reason is not None:
                rows_rejected += 1
                conn.execute(
                    """
                    INSERT OR IGNORE INTO lead_rejections (workflow_id, batch_id, row_number, reason)
                    VALUES (?, ?, ?, ?)
                    """,
                    (workflow_id, batch_id, row_number, rejection_reason),
                )
                continue

            # Valid lead row
            field_warnings += row_warnings
            canonical_phone: str = parsed_values["phone"]
            phone_hash = hash_pii(canonical_phone, namespace="phone")
            phone_masked = mask_phone(canonical_phone)

            # Prepare non-plaintext attributes dict
            incoming_attrs: dict[str, Any] = {}
            for field in fields:
                fname = field["name"]
                if fname == "phone":
                    continue
                val = parsed_values.get(fname)
                if val is None:
                    incoming_attrs[fname] = None
                elif fname in pii_fields:
                    incoming_attrs[fname] = hash_pii(str(val), namespace=fname)
                else:
                    incoming_attrs[fname] = val

            existing = conn.execute(
                """
                SELECT lead_id, external_ref, attributes_json
                FROM leads
                WHERE workflow_id = ? AND phone_hash = ?
                """,
                (workflow_id, phone_hash),
            ).fetchone()

            if existing is None:
                # First valid row creates the lead; apply defaults to remaining nulls
                final_attrs = dict(incoming_attrs)
                for d_field, d_val in defaults.items():
                    if final_attrs.get(d_field) is None:
                        final_attrs[d_field] = d_val

                external_ref = str(final_attrs[ref_field])
                conn.execute(
                    """
                    INSERT INTO leads (
                        workflow_id, phone_hash, phone_masked, external_ref, batch_id, attributes_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        workflow_id,
                        phone_hash,
                        phone_masked,
                        external_ref,
                        batch_id,
                        json.dumps(final_attrs, sort_keys=True),
                    ),
                )
            else:
                # Duplicate identity: overwrite only fields where incoming value is non-null
                duplicates_merged += 1
                merged_attrs = json.loads(existing["attributes_json"])
                for k, v in incoming_attrs.items():
                    if v is not None:
                        merged_attrs[k] = v
                for d_field, d_val in defaults.items():
                    if merged_attrs.get(d_field) is None:
                        merged_attrs[d_field] = d_val

                external_ref = str(merged_attrs[ref_field])
                conn.execute(
                    """
                    UPDATE leads
                    SET external_ref = ?, attributes_json = ?
                    WHERE lead_id = ?
                    """,
                    (external_ref, json.dumps(merged_attrs, sort_keys=True), existing["lead_id"]),
                )

    increment_counter(conn, "lead_rows_total", rows_total)
    increment_counter(conn, "lead_rows_rejected", rows_rejected)
    increment_counter(conn, "duplicate_lead_rows_merged", duplicates_merged)
    increment_counter(conn, "field_warnings", field_warnings)
    mark_file_ingested(conn, file_key, sha256, "leads")


def ingest_event_file(
    conn: sqlite3.Connection,
    jsonl_path: Path,
    dispositions: dict[tuple[str, str], dict[str, Any]],
) -> None:
    file_key = str(jsonl_path.resolve())
    sha256 = compute_file_sha256(jsonl_path)
    if is_file_already_ingested(conn, file_key, sha256):
        return

    events_total = 0
    malformed_events = 0
    duplicate_events_dropped = 0
    unknown_disposition_events = 0

    max_seq_row = conn.execute("SELECT COALESCE(MAX(ingest_seq), 0) AS m FROM events").fetchone()
    seq = int(max_seq_row["m"])

    with jsonl_path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue

            events_total += 1
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                malformed_events += 1
                continue

            if not isinstance(obj, dict):
                malformed_events += 1
                continue

            event_id = obj.get("event_id")
            if not event_id or not isinstance(event_id, str) or not event_id.strip():
                malformed_events += 1
                continue
            event_id = event_id.strip()

            event_type = str(obj.get("event_type", "")).strip()
            workflow_id = str(obj.get("workflow_id", "")).strip()
            call_id = str(obj.get("call_id", "")).strip()
            dt_ist = parse_timestamp_ist(obj.get("occurred_at"))

            if not event_type or not workflow_id or not call_id or dt_ist is None:
                malformed_events += 1
                continue

            # Check duplicate event_id
            exists = conn.execute(
                "SELECT 1 FROM events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if exists is not None:
                duplicate_events_dropped += 1
                continue

            # Normalize phone if present (never store raw phone)
            raw_phone = obj.get("phone")
            phone_hash: str | None = None
            if raw_phone is not None and str(raw_phone).strip() != "":
                canon = normalize_phone(str(raw_phone))
                if canon is not None:
                    phone_hash = hash_pii(canon, namespace="phone")
                else:
                    # Unnormalizable phone string -> cannot match any valid lead (will be orphan)
                    phone_hash = f"invalid:{hash_pii(str(raw_phone).strip(), namespace='invalid_phone')}"

            payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}

            # Check unknown disposition for disposition.* events
            is_unknown_disp = 0
            if event_type.startswith("disposition."):
                code = str(payload.get("disposition_code", "")).strip()
                if (workflow_id, code) not in dispositions:
                    is_unknown_disp = 1
                    unknown_disposition_events += 1

            seq += 1
            occurred_at_utc = dt_ist.astimezone(timezone.utc).isoformat()
            occurred_at_ist = dt_ist.isoformat()
            ist_date = dt_ist.strftime("%Y-%m-%d")

            conn.execute(
                """
                INSERT INTO events (
                    event_id, event_type, workflow_id, call_id,
                    occurred_at_utc, occurred_at_ist, ist_date,
                    phone_hash, payload_json, is_unknown_disposition, ingest_seq
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    event_type,
                    workflow_id,
                    call_id,
                    occurred_at_utc,
                    occurred_at_ist,
                    ist_date,
                    phone_hash,
                    json.dumps(payload, sort_keys=True),
                    is_unknown_disp,
                    seq,
                ),
            )

    increment_counter(conn, "events_total", events_total)
    increment_counter(conn, "malformed_events", malformed_events)
    increment_counter(conn, "duplicate_events_dropped", duplicate_events_dropped)
    increment_counter(conn, "unknown_disposition_events", unknown_disposition_events)
    mark_file_ingested(conn, file_key, sha256, "events")


def ingest_drop(
    conn: sqlite3.Connection,
    drop_dir: str | Path,
    workflows: dict[str, dict[str, Any]],
    dispositions: dict[tuple[str, str], dict[str, Any]],
) -> None:
    drop_path = Path(drop_dir)
    if not drop_path.exists() or not drop_path.is_dir():
        raise FileNotFoundError(f"Drop directory not found: {drop_path}")

    with conn:
        # 1. Ingest leads per workflow in sorted workflow & filename order
        leads_root = drop_path / "leads"
        if leads_root.exists():
            for wf_dir in sorted(p for p in leads_root.iterdir() if p.is_dir()):
                wf_id = wf_dir.name
                if wf_id not in workflows:
                    continue
                wf_schema = workflows[wf_id]
                for csv_file in sorted(wf_dir.glob("*.csv")):
                    ingest_lead_file(conn, csv_file, wf_schema)

        # 2. Ingest events in sorted filename order
        events_root = drop_path / "events"
        if events_root.exists():
            for jsonl_file in sorted(events_root.glob("*.jsonl")):
                ingest_event_file(conn, jsonl_file, dispositions)
