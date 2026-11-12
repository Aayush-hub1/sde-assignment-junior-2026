from __future__ import annotations

import csv
import json
from pathlib import Path

from solution.cli import main
from solution.validators import (
    normalize_header,
    normalize_phone,
    parse_date,
    parse_decimal,
    parse_timestamp_ist,
)

SCHEMAS_DIR = Path("assignment-a-data/schemas")


def test_phone_normalization():
    assert normalize_phone("9876543210") == "+919876543210"
    assert normalize_phone("+91 98765-43210") == "+919876543210"
    assert normalize_phone("919876543210") == "+919876543210"
    assert normalize_phone("09876543210") == "+919876543210"
    assert normalize_phone("(0) 61234 56789") == "+916123456789"
    assert normalize_phone("5876543210") is None
    assert normalize_phone("1234567890") is None
    assert normalize_phone("987654321") is None
    assert normalize_phone("9198765432101") is None
    assert normalize_phone("") is None
    assert normalize_phone(None) is None


def test_date_parsing_day_first_and_impossible():
    assert parse_date("2026-10-15") == "2026-10-15"
    assert parse_date("05/10/2026") == "2026-10-05"
    assert parse_date("05-10-2026") == "2026-10-05"
    assert parse_date("01-Dec-2026") == "2026-12-01"
    assert parse_date("01-dec-2026") == "2026-12-01"
    assert parse_date("31/02/2026") is None
    assert parse_date("29/02/2026") is None
    assert parse_date("29/02/2024") == "2024-02-29"
    assert parse_date("2026-13-01") is None


def test_decimal_and_header_normalization():
    assert parse_decimal("₹ 12,450.5") == "12450.50"
    assert parse_decimal("Rs. 999.995") == "1000.00"
    assert parse_decimal("INR 0") == "0.00"
    assert parse_decimal("-50") is None
    assert parse_decimal("abc") is None
    assert normalize_header("  Mobile_No  ") == "mobile no"
    assert normalize_header("Customer__Name") == "customer name"


def test_timestamp_ist_conversion():
    dt_utc = parse_timestamp_ist("2026-10-01T19:30:00Z")
    assert dt_utc is not None
    assert dt_utc.strftime("%Y-%m-%d") == "2026-10-02"
    dt_naive = parse_timestamp_ist("2026-10-03 10:01:00")
    assert dt_naive is not None
    assert dt_naive.isoformat() == "2026-10-03T10:01:00+05:30"


def test_end_to_end_rules_and_pii_protection(tmp_path: Path):
    drop1 = tmp_path / "drop_1"
    drop2 = tmp_path / "drop_2"
    (drop1 / "leads" / "motor_renewal").mkdir(parents=True)
    (drop1 / "events").mkdir(parents=True)
    (drop2 / "leads" / "motor_renewal").mkdir(parents=True)
    (drop2 / "events").mkdir(parents=True)

    (drop1 / "leads" / "motor_renewal" / "batch_001.csv").write_text(
        "Name,Mobile No,Policy,Expiry Date,Premium,Language,ExtraCol\n"
        "Aarav Sharma,9876543210,POL-100,01-Dec-2026,₹12000,HI,ignored\n"
        " , , , , , , \n"
        ",12345,POL-BAD,,,en,x\n"
        "Priya Nair,12345,POL-BAD2,,,en,x\n"
        "Rohan Das,09123456789,POL-200,31/02/2026,Rs. 5500,,x\n",
        encoding="utf-8",
    )

    events_d1 = [
        "NOT VALID JSON",
        json.dumps({
            "event_id": "e1", "event_type": "call.initiated", "workflow_id": "motor_renewal",
            "call_id": "c1", "occurred_at": "2026-10-01T10:00:00+05:30", "phone": "+919876543210", "payload": {}
        }),
        json.dumps({
            "event_id": "e2", "event_type": "call.connected", "workflow_id": "motor_renewal",
            "call_id": "c1", "occurred_at": "2026-10-01T10:00:10+05:30", "phone": "9876543210", "payload": {}
        }),
        json.dumps({
            "event_id": "e3", "event_type": "disposition.set", "workflow_id": "motor_renewal",
            "call_id": "c1", "occurred_at": "2026-10-01T10:02:00+05:30", "phone": "9876543210",
            "payload": {"disposition_code": "callback_requested"}
        }),
        json.dumps({
            "event_id": "e4", "event_type": "disposition.override", "workflow_id": "motor_renewal",
            "call_id": "c1", "occurred_at": "2026-10-01T11:00:00+05:30",
            "payload": {"disposition_code": "renewed", "overridden_by": "sup_1", "reason": "paid"}
        }),
        json.dumps({
            "event_id": "e5", "event_type": "disposition.set", "workflow_id": "motor_renewal",
            "call_id": "c1", "occurred_at": "2026-10-01T12:00:00+05:30", "phone": "9876543210",
            "payload": {"disposition_code": "invented_code"}
        }),
        json.dumps({
            "event_id": "e6", "event_type": "call.initiated", "workflow_id": "motor_renewal",
            "call_id": "c2", "occurred_at": "2026-10-01T14:00:00+05:30", "phone": "8888877777", "payload": {}
        }),
        json.dumps({
            "event_id": "e7", "event_type": "call.telephony_failed", "workflow_id": "motor_renewal",
            "call_id": "c2", "occurred_at": "2026-10-01T14:00:05+05:30", "phone": "8888877777",
            "payload": {"reason": "busy"}
        }),
    ]
    (drop1 / "events" / "events_0001.jsonl").write_text("\n".join(events_d1) + "\n", encoding="utf-8")

    (drop2 / "leads" / "motor_renewal" / "batch_002.csv").write_text(
        "customer_name,phone,policy_number\n"
        "Aarav S,919876543210,POL-100-NEW\n"
        "Meera Iyer,8888877777,POL-300\n",
        encoding="utf-8",
    )
    (drop2 / "events" / "events_0002.jsonl").write_text(
        json.dumps({
            "event_id": "e1", "event_type": "call.initiated", "workflow_id": "motor_renewal",
            "call_id": "c1", "occurred_at": "2026-10-01T10:00:00+05:30", "phone": "+919876543210", "payload": {}
        }) + "\n",
        encoding="utf-8",
    )

    db_file = tmp_path / "test.db"
    out_inc = tmp_path / "reports_inc"

    assert main(["--db", str(db_file), "--schemas", str(SCHEMAS_DIR), "ingest", str(drop1)]) == 0
    assert main(["--db", str(db_file), "--schemas", str(SCHEMAS_DIR), "ingest", str(drop2)]) == 0
    assert main(["--db", str(db_file), "--schemas", str(SCHEMAS_DIR), "ingest", str(drop1)]) == 0
    assert main(["--db", str(db_file), "--schemas", str(SCHEMAS_DIR), "report", "--out", str(out_inc)]) == 0

    raw_db_bytes = db_file.read_bytes()
    assert b"9876543210" not in raw_db_bytes
    assert b"8888877777" not in raw_db_bytes
    assert b"Aarav Sharma" not in raw_db_bytes
    assert b"Meera Iyer" not in raw_db_bytes

    dq = json.loads((out_inc / "data_quality.json").read_text(encoding="utf-8"))
    assert dq == {
        "lead_rows_total": 6,
        "lead_rows_rejected": 2,
        "rejections_by_reason": {
            "invalid_phone": 1,
            "missing_required:customer_name": 1,
        },
        "duplicate_lead_rows_merged": 1,
        "field_warnings": 1,
        "events_total": 9,
        "malformed_events": 1,
        "duplicate_events_dropped": 1,
        "orphan_events": 0,
        "unknown_disposition_events": 1,
    }

    with (out_inc / "lead_report.csv").open("r", encoding="utf-8") as f:
        leads = list(csv.DictReader(f))
    assert len(leads) == 3

    l1 = leads[0]
    assert l1["external_ref"] == "POL-100-NEW"
    assert l1["batch_id"] == "batch_001"
    assert l1["phone_masked"] == "+91******3210"
    assert l1["bucket"] == "completed"
    assert l1["current_disposition"] == "renewed"
    assert l1["ever_success"] == "true"

    l2 = leads[1]
    assert l2["external_ref"] == "POL-200"
    assert l2["bucket"] == "yet_to_reach_out"

    l3 = leads[2]
    assert l3["external_ref"] == "POL-300"
    assert l3["batch_id"] == "batch_002"
    assert l3["bucket"] == "in_progress_telephony_failed"
    assert l3["total_calls"] == "1"
    assert l3["connected_calls"] == "0"
