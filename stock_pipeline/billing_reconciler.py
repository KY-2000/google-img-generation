from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


TABLE_PATH_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_-]*\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+$"
)
PROJECT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{4,61}[a-z0-9]$")


def resolve_bq_command(which: Callable[[str], str | None] = shutil.which) -> str:
    return which("bq") or which("bq.cmd") or "bq"


class BigQueryBillingReconciler:
    def __init__(
        self,
        *,
        table_path: str,
        project_id: str,
        runner: Callable[..., Any] = subprocess.run,
        bq_command: str | None = None,
    ) -> None:
        if not TABLE_PATH_PATTERN.fullmatch(table_path):
            raise ValueError("invalid billing table path")
        if not PROJECT_ID_PATTERN.fullmatch(project_id):
            raise ValueError("invalid billing project id")
        self.table_path = table_path
        self.project_id = project_id
        self.runner = runner
        self.bq_command = bq_command or resolve_bq_command()

    def lookup(self, started_at: str, ended_at: str) -> dict[str, Any]:
        start = datetime.fromisoformat(started_at)
        end = datetime.fromisoformat(ended_at)
        if start.tzinfo is None or end.tzinfo is None or end < start:
            raise ValueError("billing reconciliation requires an ordered timezone-aware interval")

        query = f"""
SELECT
  currency,
  SUM(cost) AS gross_cost,
  SUM((SELECT COALESCE(SUM(c.amount), 0) FROM UNNEST(credits) c)) AS credits,
  SUM(cost + (SELECT COALESCE(SUM(c.amount), 0) FROM UNNEST(credits) c)) AS net_cost,
  MIN(usage_start_time) AS first_usage,
  MAX(usage_end_time) AS last_usage,
  ARRAY_AGG(DISTINCT sku.description IGNORE NULLS ORDER BY sku.description) AS skus
FROM `{self.table_path}`
WHERE _PARTITIONDATE BETWEEN DATE(@start_time) AND CURRENT_DATE()
  AND project.id = @project_id
  AND service.description = 'Vertex AI'
  AND usage_start_time < @end_time
  AND usage_end_time > @start_time
GROUP BY currency
ORDER BY currency
""".strip()
        query = " ".join(query.split())
        command = [
            self.bq_command,
            "query",
            "--nouse_legacy_sql",
            "--format=json",
            f"--parameter=start_time:TIMESTAMP:{started_at}",
            f"--parameter=end_time:TIMESTAMP:{ended_at}",
            f"--parameter=project_id:STRING:{self.project_id}",
            query,
        ]
        try:
            completed = self.runner(command, capture_output=True, text=True, check=False)
        except OSError as exc:
            return {
                "status": "unavailable",
                "reason": f"unable to launch bq: {exc}",
                "table_path": self.table_path,
                "queried_at": datetime.now(timezone.utc).isoformat(),
            }
        if completed.returncode:
            return {
                "status": "unavailable",
                "reason": (
                    completed.stderr or completed.stdout or "BigQuery query failed"
                ).strip(),
                "table_path": self.table_path,
                "queried_at": datetime.now(timezone.utc).isoformat(),
            }
        rows = json.loads(completed.stdout or "[]")
        if not rows:
            return {
                "status": "pending",
                "reason": "no matching rows in the delayed billing export yet",
                "table_path": self.table_path,
                "queried_at": datetime.now(timezone.utc).isoformat(),
            }
        if len(rows) != 1:
            return {
                "status": "unavailable",
                "reason": "billing interval contains multiple currencies",
                "table_path": self.table_path,
                "queried_at": datetime.now(timezone.utc).isoformat(),
            }
        row = rows[0]
        return {
            "status": "available",
            "currency": row["currency"],
            "gross_cost": float(row["gross_cost"] or 0),
            "credits": float(row["credits"] or 0),
            "net_cost": float(row["net_cost"] or 0),
            "first_usage": row.get("first_usage"),
            "last_usage": row.get("last_usage"),
            "skus": row.get("skus") or [],
            "scope": "project_vertex_ai_hourly_billing_window",
            "table_path": self.table_path,
            "queried_at": datetime.now(timezone.utc).isoformat(),
        }


def reconcile_summary_file(
    path: Path,
    lookup: Callable[[str, str], dict[str, Any]],
) -> dict[str, Any]:
    summary_path = path
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if not summary.get("started_at") or not summary.get("ended_at"):
        raise ValueError(f"command summary is missing its time interval: {summary_path}")
    result = lookup(summary["started_at"], summary["ended_at"])
    summary["official_billing"] = result
    summary["official_reconciled_at"] = datetime.now(timezone.utc).isoformat()
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def reconcile_usage_directory(
    command_dir: Path,
    lookup: Callable[[str, str], dict[str, Any]],
    *,
    progress: Callable[[str], None] | None = None,
) -> dict[str, int]:
    counts = {"checked": 0, "available": 0, "pending": 0, "unavailable": 0}
    for path in sorted(command_dir.glob("*.json")):
        summary = json.loads(path.read_text(encoding="utf-8"))
        if not summary.get("request_count"):
            continue
        if (summary.get("official_billing") or {}).get("status") == "available":
            continue
        result = reconcile_summary_file(path, lookup)
        counts["checked"] += 1
        status = result.get("status", "unavailable")
        counts[status if status in counts else "unavailable"] += 1
        if progress is not None:
            if status == "available":
                progress(
                    f"[AI COST] {summary['command_id']} official "
                    f"gross={result['currency']} {result['gross_cost']:.6f} "
                    f"credits={result['currency']} {result['credits']:.6f} "
                    f"net={result['currency']} {result['net_cost']:.6f}"
                )
            else:
                progress(
                    f"[AI COST] {summary['command_id']} official "
                    f"status={status} reason={result.get('reason', 'unknown')}"
                )
    return counts
