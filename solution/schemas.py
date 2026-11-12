from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def load_workflow_schemas(schema_dir: str | Path) -> dict[str, dict[str, Any]]:
    schema_path = Path(schema_dir)
    workflows: dict[str, dict[str, Any]] = {}
    for json_file in sorted(schema_path.glob("*.json")):
        data = json.loads(json_file.read_text(encoding="utf-8"))
        wf_id = data["workflow_id"]
        workflows[wf_id] = data
    return workflows


def load_dispositions(schema_dir: str | Path) -> dict[tuple[str, str], dict[str, Any]]:
    disp_file = Path(schema_dir) / "dispositions.csv"
    catalogue: dict[tuple[str, str], dict[str, Any]] = {}
    with disp_file.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            wf_id = row["workflow_id"].strip()
            code = row["code"].strip()
            catalogue[(wf_id, code)] = {
                "label": row["label"].strip(),
                "is_terminal": row["is_terminal"].strip().lower() == "true",
                "is_success": row["is_success"].strip().lower() == "true",
            }
    return catalogue
