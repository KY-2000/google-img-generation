from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AssetLogger:
    def __init__(self, asset_dir: Path):
        self.asset_dir = asset_dir
        log_path = asset_dir / "run_log.json"
        try:
            existing = json.loads(log_path.read_text(encoding="utf-8"))
            self.events = list(existing.get("events", []))
        except (OSError, json.JSONDecodeError, AttributeError):
            self.events: list[dict[str, Any]] = []

    def event(self, message: str, **details: Any) -> None:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": message,
            **details,
        }
        self.events.append(entry)
        self.flush()

    def flush(self) -> None:
        self.asset_dir.mkdir(parents=True, exist_ok=True)
        (self.asset_dir / "run_log.json").write_text(
            json.dumps({"events": self.events}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (self.asset_dir / "run_log.txt").write_text(
            "".join(f"{event['timestamp']} {event['message']}\n" for event in self.events),
            encoding="utf-8",
        )
