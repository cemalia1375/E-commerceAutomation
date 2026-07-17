"""Automation run logging."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


class RunLogger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, message: str) -> None:
        line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n"
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line)
        print(message, flush=True)
