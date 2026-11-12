from __future__ import annotations

import argparse
import os
from pathlib import Path

from solution.db import connect_db
from solution.ingest import ingest_drop
from solution.reporting import generate_reports
from solution.schemas import load_dispositions, load_workflow_schemas

DEFAULT_DB = os.environ.get("PIPELINE_DB_PATH", "pipeline.db")
DEFAULT_SCHEMAS = os.environ.get("PIPELINE_SCHEMAS_DIR", "assignment-a-data/schemas")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m solution.cli",
        description="Interaction Ledger & Reporting Pipeline",
    )
    parser.add_argument("--db", default=DEFAULT_DB, help="SQLite database path")
    parser.add_argument("--schemas", default=DEFAULT_SCHEMAS, help="Path to schemas directory")

    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_p = subparsers.add_parser("ingest", help="Ingest one or more drop directories")
    ingest_p.add_argument("drops", nargs="+", help="Drop directory path(s)")

    report_p = subparsers.add_parser("report", help="Generate all 5 reporting artifacts")
    report_p.add_argument("--out", required=True, help="Output directory for reports")

    args = parser.parse_args(argv)

    workflows = load_workflow_schemas(args.schemas)
    dispositions = load_dispositions(args.schemas)
    conn = connect_db(args.db)

    try:
        if args.command == "ingest":
            for drop in args.drops:
                ingest_drop(conn, Path(drop), workflows, dispositions)
        elif args.command == "report":
            generate_reports(conn, Path(args.out), dispositions)
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
