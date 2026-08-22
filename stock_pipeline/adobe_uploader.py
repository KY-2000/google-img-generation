from __future__ import annotations

import os
import json
import subprocess
from pathlib import Path

from .registry import Registry, now_iso


def build_upload_command(
    csv_path: Path, images_dir: Path, *, cdp: str | None = None,
    user_data_dir: str | None = None, dry_run: bool = False,
) -> list[str]:
    node = "node.exe" if os.name == "nt" else "node"
    command = [
        node, "tools/adobe_upload_playwright.mjs",
        "--csv", str(csv_path), "--images", str(images_dir),
        "--file-type", "illustrations", "--mark-ai", "--save-work",
    ]
    if cdp:
        command.extend(["--cdp", cdp])
    if user_data_dir:
        command.extend(["--user-data-dir", user_data_dir])
    if dry_run:
        command.append("--dry-run")
    return command


def upload_draft(
    project_root: Path, batch_dir: Path, *, cdp: str | None = None,
    user_data_dir: str | None = None, dry_run: bool = False,
) -> None:
    command = build_upload_command(
        batch_dir / "adobe_stock_metadata.csv", batch_dir,
        cdp=cdp, user_data_dir=user_data_dir, dry_run=dry_run,
    )
    subprocess.run(command, cwd=project_root, check=True)


def mark_batch_uploaded_draft(registry: Registry, batch_dir: Path) -> list[str]:
    asset_ids = validate_batch_ready_for_upload(registry, batch_dir)
    for asset_id in asset_ids:
        registry.transition(asset_id, "UPLOADED_DRAFT")
        registry.update_asset(asset_id, uploaded_at=now_iso())
    return asset_ids


def validate_batch_ready_for_upload(registry: Registry, batch_dir: Path) -> list[str]:
    manifest_path = batch_dir / "batch_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        asset_ids = manifest["asset_ids"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError(f"invalid batch manifest: {manifest_path}") from exc
    for asset_id in asset_ids:
        asset = registry.get_asset(asset_id)
        if not asset:
            raise ValueError(f"batch manifest references unknown asset: {asset_id}")
        if asset["status"] != "READY_TO_UPLOAD":
            raise ValueError(f"asset is not READY_TO_UPLOAD: {asset_id}")
    return list(asset_ids)
