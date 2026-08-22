from __future__ import annotations

import csv
from pathlib import Path

from .registry import Registry


PERFORMANCE_FIELDS = ["asset_id", "status", "accepted_at", "downloads", "revenue", "snapshot_date"]


def import_performance_csv(registry: Registry, path: Path) -> int:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or any(field not in reader.fieldnames for field in PERFORMANCE_FIELDS):
            raise ValueError(f"performance CSV must contain: {', '.join(PERFORMANCE_FIELDS)}")
        rows = list(reader)
    for row in rows:
        asset_id = row["asset_id"].strip()
        if not registry.get_asset(asset_id):
            raise ValueError(f"unknown asset_id in performance CSV: {asset_id}")
        status = row["status"].strip().upper()
        if status not in {"ACCEPTED", "REJECTED", "PENDING"}:
            raise ValueError(f"unsupported Adobe performance status for {asset_id}: {status}")
        values = {
            "accepted_at": row["accepted_at"].strip() or None,
            "downloads": int(row["downloads"]) if row["downloads"].strip() else None,
            "revenue": float(row["revenue"]) if row["revenue"].strip() else None,
            "snapshot_date": row["snapshot_date"].strip() or None,
        }
        if status == "REJECTED":
            values["rejected_at"] = values["snapshot_date"]
        registry.update_performance(asset_id, **values)
    return len(rows)
