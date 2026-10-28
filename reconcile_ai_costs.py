#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from stock_pipeline.billing_reconciler import (
    BigQueryBillingReconciler,
    reconcile_usage_directory,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reconcile pending AI command summaries with Cloud Billing export."
    )
    parser.add_argument(
        "--config", type=Path, default=Path("config/stock_icon_pipeline.json")
    )
    parser.add_argument(
        "--usage-dir", type=Path, default=Path("output/usage/commands")
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    project_root = Path(__file__).resolve().parent
    config_path = args.config if args.config.is_absolute() else project_root / args.config
    command_dir = args.usage_dir if args.usage_dir.is_absolute() else project_root / args.usage_dir
    config = json.loads(config_path.read_text(encoding="utf-8"))
    billing = config.get("billing") or {}
    if not billing.get("export_table") or not billing.get("project_id"):
        raise ValueError("billing.project_id and billing.export_table must be configured")
    reconciler = BigQueryBillingReconciler(
        table_path=billing["export_table"],
        project_id=billing["project_id"],
    )
    summary = reconcile_usage_directory(
        command_dir,
        reconciler.lookup,
        progress=lambda message: print(message, flush=True),
    )
    print(
        f"Checked: {summary['checked']} | Available: {summary['available']} | "
        f"Pending: {summary['pending']} | Unavailable: {summary['unavailable']}"
    )
    return 1 if summary["unavailable"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
