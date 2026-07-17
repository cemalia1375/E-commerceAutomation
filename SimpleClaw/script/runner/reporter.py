"""Automation report generation."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def write_report(run_dir: Path, result: dict[str, Any]) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "report.json"
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        **result,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
