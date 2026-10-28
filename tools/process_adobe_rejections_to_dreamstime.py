#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from stock_pipeline.dreamstime_rejections import prepare_manifest, stage_manifest_images


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Prepare recent Adobe rejections for Dreamstime.")
    parser.add_argument("--cdp", default="http://127.0.0.1:9222")
    parser.add_argument("--days", type=int, default=14, help="Include submissions from the last N days.")
    parser.add_argument("--since", help="Explicit inclusive cutoff date/time in ISO 8601; overrides --days.")
    parser.add_argument("--template", type=Path, default=Path(r"C:\Users\tanke\Downloads\Image_spreadsheet_template (1).xls"))
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--scan-json", type=Path, help="Reuse a prior Adobe scan instead of browsing Adobe.")
    parser.add_argument("--scan-only", action="store_true")
    parser.add_argument("--prepare-dreamstime", action="store_true", help="Upload and populate Dreamstime forms without final submission.")
    parser.add_argument("--submit-commercial", action="store_true", help="Click Dreamstime Submit commercial for every prepared image.")
    args = parser.parse_args(argv)
    if args.days < 0:
        parser.error("--days must be non-negative")
    if args.submit_commercial:
        args.prepare_dreamstime = True
    return args


def _absolute(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def _node() -> str:
    node = shutil.which("node") or shutil.which("node.exe")
    if not node:
        raise RuntimeError("Node.js was not found on PATH")
    return node


def _since(args) -> datetime:
    if args.since:
        value = datetime.fromisoformat(args.since.replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value
    return datetime.now(timezone.utc) - timedelta(days=args.days)


def main(argv=None) -> int:
    args = parse_args(argv)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = _absolute(args.output_dir) if args.output_dir else PROJECT_ROOT / "output" / "dreamstime_rejections" / timestamp
    output_dir.mkdir(parents=True, exist_ok=True)
    scan_path = _absolute(args.scan_json) if args.scan_json else output_dir / "adobe_rejections.json"
    browser_script = PROJECT_ROOT / "tools" / "adobe_rejected_dreamstime_playwright.mjs"
    if not args.scan_json:
        subprocess.run([
            _node(), str(browser_script), "--cdp", args.cdp,
            "--days", str(args.days), "--since", _since(args).isoformat(),
            "--scan-output", str(scan_path),
            "--run-dir", str(output_dir / "browser"),
        ], cwd=PROJECT_ROOT, check=True)
    if args.scan_only:
        print(f"Adobe rejection scan saved: {scan_path}")
        return 0
    scan_values = json.loads(scan_path.read_text(encoding="utf-8"))
    manifest = prepare_manifest(PROJECT_ROOT, scan_values, since=_since(args))
    stage_manifest_images(manifest, output_dir / "images")
    manifest_path = output_dir / "manifest.json"
    rows_path = output_dir / "dreamstime_rows.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    rows_path.write_text(json.dumps([item["spreadsheet_row"] for item in manifest["items"]], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if not manifest["items"]:
        print(f"No eligible locally matched Adobe rejections since {manifest['since']}; unresolved={manifest['unresolved_count']}")
        print(f"Manifest: {manifest_path}")
        return 0
    spreadsheet = output_dir / "Image_spreadsheet_rejected_assets.xls"
    powershell = shutil.which("powershell.exe") or "powershell.exe"
    subprocess.run([
        powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        str(PROJECT_ROOT / "tools" / "write_dreamstime_xls.ps1"),
        "-Template", str(_absolute(args.template)), "-RowsJson", str(rows_path), "-Output", str(spreadsheet),
    ], cwd=PROJECT_ROOT, check=True)
    print(f"Matched assets: {manifest['count']}")
    print(f"Unresolved assets: {manifest['unresolved_count']}")
    print(f"Manifest: {manifest_path}")
    print(f"Dreamstime XLS: {spreadsheet}")
    if args.prepare_dreamstime:
        command = [
            _node(), str(browser_script), "--cdp", args.cdp,
            "--manifest", str(manifest_path), "--run-dir", str(output_dir / "browser"),
        ]
        if args.submit_commercial:
            command.append("--submit-commercial")
        subprocess.run(command, cwd=PROJECT_ROOT, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
