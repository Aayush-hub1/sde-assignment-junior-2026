from __future__ import annotations

import csv
import json
import sqlite3
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from solution.db import get_counters


def generate_reports(
    conn: sqlite3.Connection,
    out_dir: str | Path,
    dispositions: dict[tuple[str, str], dict[str, Any]],
) -> None:
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    # 1. Load all valid leads keyed by (workflow_id, phone_hash)
    lead_rows = conn.execute(
        """
        SELECT lead_id, workflow_id, phone_hash, phone_masked, external_ref, batch_id
        FROM leads
        """
    ).fetchall()

    leads_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    all_leads: list[dict[str, Any]] = []
    for r in lead_rows:
        lead = {
            "lead_id": r["lead_id"],
            "workflow_id": r["workflow_id"],
            "phone_hash": r["phone_hash"],
            "phone_masked": r["phone_masked"],
            "external_ref": r["external_ref"],
            "batch_id": r["batch_id"],
        }
        leads_by_key[(lead["workflow_id"], lead["phone_hash"])] = lead
        all_leads.append(lead)

    # 2. Load all stored events in deterministic order
    event_rows = conn.execute(
        """
        SELECT event_id, event_type, workflow_id, call_id,
               occurred_at_utc, occurred_at_ist, ist_date,
               phone_hash, payload_json, is_unknown_disposition, ingest_seq
        FROM events
        ORDER BY ingest_seq ASC
        """
    ).fetchall()

    # Map (workflow_id, call_id) -> phone_hash from any event of that call that carries phone_hash
    call_to_phone_hash: dict[tuple[str, str], str] = {}
    for ev in event_rows:
        if ev["phone_hash"] and not str(ev["phone_hash"]).startswith("invalid:"):
            call_to_phone_hash[(ev["workflow_id"], ev["call_id"])] = ev["phone_hash"]

    # Link events to leads & count orphan events
    orphan_events = 0
    # call_records[(workflow_id, call_id)] -> aggregated call structure
    calls: dict[tuple[str, str], dict[str, Any]] = {}

    for ev in event_rows:
        wf_id = ev["workflow_id"]
        call_id = ev["call_id"]
        ev_type = ev["event_type"]

        # Resolve phone_hash (disposition.override uses call_id)
        p_hash = ev["phone_hash"]
        if ev_type == "disposition.override" and not p_hash:
            p_hash = call_to_phone_hash.get((wf_id, call_id))

        lead = leads_by_key.get((wf_id, p_hash)) if p_hash else None
        if lead is None:
            orphan_events += 1
            continue

        # Ignore unknown disposition events for metrics (already counted during ingest)
        if ev["is_unknown_disposition"]:
            continue

        call_key = (wf_id, call_id)
        c = calls.get(call_key)
        if c is None:
            c = {
                "workflow_id": wf_id,
                "call_id": call_id,
                "lead_id": lead["lead_id"],
                "initiated_at_utc": None,
                "initiated_at_ist": None,
                "initiated_ist_date": None,
                "connected": False,
                "telephony_failed": False,
                "disp_sets": [],       # list of (occurred_at_utc, ingest_seq, code)
                "disp_overrides": [],  # list of (occurred_at_utc, ingest_seq, code)
            }
            calls[call_key] = c

        if ev_type == "call.initiated":
            # Keep earliest initiated timestamp if duplicated across call_id
            if c["initiated_at_utc"] is None or ev["occurred_at_utc"] < c["initiated_at_utc"]:
                c["initiated_at_utc"] = ev["occurred_at_utc"]
                c["initiated_at_ist"] = ev["occurred_at_ist"]
                c["initiated_ist_date"] = ev["ist_date"]
        elif ev_type == "call.connected":
            c["connected"] = True
        elif ev_type == "call.telephony_failed":
            c["telephony_failed"] = True
        elif ev_type == "disposition.set":
            payload = json.loads(ev["payload_json"])
            code = str(payload.get("disposition_code", "")).strip()
            c["disp_sets"].append((ev["occurred_at_utc"], ev["ingest_seq"], code))
        elif ev_type == "disposition.override":
            payload = json.loads(ev["payload_json"])
            code = str(payload.get("disposition_code", "")).strip()
            c["disp_overrides"].append((ev["occurred_at_utc"], ev["ingest_seq"], code))

    # Filter to valid calls (must have call.initiated present and linked)
    valid_calls_by_lead: dict[int, list[dict[str, Any]]] = defaultdict(list)
    daily_stats: dict[tuple[str, str], dict[str, Any]] = {}

    for c in calls.values():
        if c["initiated_at_utc"] is None:
            continue

        # Compute effective disposition
        effective_disp: str | None = None
        if c["disp_overrides"]:
            c["disp_overrides"].sort(key=lambda x: (x[0], x[1]))
            effective_disp = c["disp_overrides"][-1][2]
        elif c["disp_sets"]:
            c["disp_sets"].sort(key=lambda x: (x[0], x[1]))
            effective_disp = c["disp_sets"][-1][2]

        c["effective_disposition"] = effective_disp
        disp_meta = dispositions.get((c["workflow_id"], effective_disp)) if effective_disp else None
        c["is_terminal"] = bool(disp_meta["is_terminal"]) if disp_meta else False
        c["is_success"] = bool(disp_meta["is_success"]) if disp_meta else False

        valid_calls_by_lead[c["lead_id"]].append(c)

        # Aggregate daily_overview metrics
        day_key = (c["workflow_id"], c["initiated_ist_date"])
        d = daily_stats.get(day_key)
        if d is None:
            d = {
                "total_calls": 0,
                "connected_calls": 0,
                "success_lead_ids": set(),
            }
            daily_stats[day_key] = d

        d["total_calls"] += 1
        if c["connected"]:
            d["connected_calls"] += 1
        if c["is_success"]:
            d["success_lead_ids"].add(c["lead_id"])

    # 3. Compute per-lead metrics & buckets
    lead_report_rows: list[dict[str, Any]] = []
    batch_counts: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {
            "total_leads": 0,
            "yet_to_reach_out": 0,
            "in_progress_telephony_failed": 0,
            "in_progress_dialed": 0,
            "completed": 0,
        }
    )

    for lead in all_leads:
        l_calls = valid_calls_by_lead.get(lead["lead_id"], [])
        l_calls.sort(key=lambda x: (x["initiated_at_utc"], x["call_id"]))

        total_calls = len(l_calls)
        connected_calls = sum(1 for c in l_calls if c["connected"])
        last_call_at = l_calls[-1]["initiated_at_ist"] if l_calls else ""

        # current_disposition: effective disposition of most recent call (by initiated time) that has one
        current_disposition = ""
        for c in reversed(l_calls):
            if c["effective_disposition"]:
                current_disposition = c["effective_disposition"]
                break

        ever_success = any(c["is_success"] for c in l_calls)

        # Bucket priority (first match wins)
        if total_calls == 0:
            bucket = "yet_to_reach_out"
        elif any(c["is_terminal"] for c in l_calls):
            bucket = "completed"
        elif all(c["telephony_failed"] for c in l_calls):
            bucket = "in_progress_telephony_failed"
        else:
            bucket = "in_progress_dialed"

        b_entry = batch_counts[(lead["workflow_id"], lead["batch_id"])]
        b_entry["total_leads"] += 1
        b_entry[bucket] += 1

        lead_report_rows.append(
            {
                "workflow_id": lead["workflow_id"],
                "external_ref": lead["external_ref"],
                "batch_id": lead["batch_id"],
                "phone_masked": lead["phone_masked"],
                "bucket": bucket,
                "total_calls": total_calls,
                "connected_calls": connected_calls,
                "last_call_at": last_call_at,
                "current_disposition": current_disposition,
                "ever_success": "true" if ever_success else "false",
            }
        )

    # Write 1: batch_summary.csv (sorted by workflow_id, batch_id)
    with (out_path / "batch_summary.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "workflow_id",
            "batch_id",
            "total_leads",
            "yet_to_reach_out",
            "in_progress_telephony_failed",
            "in_progress_dialed",
            "completed",
        ])
        for wf_id, batch_id in sorted(batch_counts.keys()):
            c = batch_counts[(wf_id, batch_id)]
            writer.writerow([
                wf_id,
                batch_id,
                c["total_leads"],
                c["yet_to_reach_out"],
                c["in_progress_telephony_failed"],
                c["in_progress_dialed"],
                c["completed"],
            ])

    # Write 2: daily_overview.csv (sorted by workflow_id, date)
    with (out_path / "daily_overview.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "workflow_id",
            "date",
            "total_calls",
            "connected_calls",
            "connectivity_rate",
            "success_leads",
        ])
        for wf_id, ist_date in sorted(daily_stats.keys()):
            d = daily_stats[(wf_id, ist_date)]
            tot = d["total_calls"]
            conn_c = d["connected_calls"]
            rate = (Decimal(conn_c) / Decimal(tot)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
            writer.writerow([
                wf_id,
                ist_date,
                tot,
                conn_c,
                f"{rate:.4f}",
                len(d["success_lead_ids"]),
            ])

    # Write 3: lead_report.csv (sorted by workflow_id, external_ref)
    lead_report_rows.sort(key=lambda r: (r["workflow_id"], r["external_ref"]))
    with (out_path / "lead_report.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "workflow_id",
            "external_ref",
            "batch_id",
            "phone_masked",
            "bucket",
            "total_calls",
            "connected_calls",
            "last_call_at",
            "current_disposition",
            "ever_success",
        ])
        for r in lead_report_rows:
            writer.writerow([
                r["workflow_id"],
                r["external_ref"],
                r["batch_id"],
                r["phone_masked"],
                r["bucket"],
                r["total_calls"],
                r["connected_calls"],
                r["last_call_at"],
                r["current_disposition"],
                r["ever_success"],
            ])

    # Write 4: rejections.csv (sorted by workflow_id, batch_id, row_number)
    rej_rows = conn.execute(
        """
        SELECT workflow_id, batch_id, row_number, reason
        FROM lead_rejections
        ORDER BY workflow_id ASC, batch_id ASC, row_number ASC
        """
    ).fetchall()
    with (out_path / "rejections.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["workflow_id", "batch_id", "row_number", "reason"])
        for r in rej_rows:
            writer.writerow([r["workflow_id"], r["batch_id"], r["row_number"], r["reason"]])

    # Write 5: data_quality.json
    counters = get_counters(conn)
    rej_by_reason_rows = conn.execute(
        """
        SELECT reason, COUNT(*) AS cnt
        FROM lead_rejections
        GROUP BY reason
        ORDER BY reason ASC
        """
    ).fetchall()
    rejections_by_reason = {r["reason"]: int(r["cnt"]) for r in rej_by_reason_rows}

    dq = {
        "lead_rows_total": counters.get("lead_rows_total", 0),
        "lead_rows_rejected": counters.get("lead_rows_rejected", 0),
        "rejections_by_reason": rejections_by_reason,
        "duplicate_lead_rows_merged": counters.get("duplicate_lead_rows_merged", 0),
        "field_warnings": counters.get("field_warnings", 0),
        "events_total": counters.get("events_total", 0),
        "malformed_events": counters.get("malformed_events", 0),
        "duplicate_events_dropped": counters.get("duplicate_events_dropped", 0),
        "orphan_events": orphan_events,
        "unknown_disposition_events": counters.get("unknown_disposition_events", 0),
    }
    with (out_path / "data_quality.json").open("w", encoding="utf-8") as f:
        json.dump(dq, f, indent=2)
        f.write("\n")
