from __future__ import annotations

import json
import shutil
import time
import re
from pathlib import Path

from .adobe_csv import build_adobe_csv, validate_csv_images
from .logging import AssetLogger
from .registry import Registry, now_iso


def stage_ready_assets(
    registry: Registry, batch_id: str, output_root: Path,
    asset_ids: set[str] | None = None,
) -> Path:
    started = time.perf_counter()
    if not re.fullmatch(r"BATCH_[A-Za-z0-9_-]+", batch_id):
        raise ValueError("batch_id must match ^BATCH_[A-Za-z0-9_-]+$")
    resolved_root = output_root.resolve()
    batch_dir = (resolved_root / batch_id).resolve()
    if batch_dir.parent != resolved_root:
        raise ValueError("batch_id escapes the Adobe batch root")
    ready = registry.list_assets("READY_TO_STAGE")
    if asset_ids is not None:
        ready = [asset for asset in ready if asset["asset_id"] in asset_ids]
    existing_ids: list[str] = []
    existing_manifest = batch_dir / "batch_manifest.json"
    if existing_manifest.exists():
        try:
            manifest_data = json.loads(existing_manifest.read_text(encoding="utf-8"))
            existing_ids = list(manifest_data["asset_ids"])
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ValueError(f"invalid existing batch manifest: {existing_manifest}") from exc
    candidate_ids = sorted(set(existing_ids) | {asset["asset_id"] for asset in ready})
    if not candidate_ids:
        raise ValueError("no READY_TO_STAGE assets")
    candidates = []
    for asset_id in candidate_ids:
        asset = registry.get_asset(asset_id)
        if not asset:
            raise ValueError(f"batch manifest references unknown asset: {asset_id}")
        candidates.append(asset)
    if existing_ids and any(
        registry.get_asset(asset_id)["status"] == "UPLOADED_DRAFT" for asset_id in existing_ids
    ) and any(asset["asset_id"] not in existing_ids for asset in ready):
        raise ValueError(
            f"batch {batch_id} is frozen after upload; choose a new batch_id for late assets"
        )
    batch_dir.mkdir(parents=True, exist_ok=True)
    expected_names = {asset["upload_filename"] for asset in candidates}
    for old_image in batch_dir.iterdir():
        if old_image.is_file() and old_image.suffix.lower() in {".png", ".jpg", ".jpeg"} and old_image.name not in expected_names:
            old_image.unlink()
    records = []
    asset_ids = []
    for asset in candidates:
        source = Path(asset["image_path"])
        if not source.exists():
            raise ValueError(f"missing source image for {asset['asset_id']}: {source}")
        filename = asset["upload_filename"]
        shutil.copy2(source, batch_dir / filename)
        metadata = {
            "title": asset["title"],
            "keywords": json.loads(asset["keywords_json"]),
            "category_code": asset["category_code"],
        }
        records.append({"upload_filename": filename, "metadata": metadata})
        asset_ids.append(asset["asset_id"])
    csv_path = build_adobe_csv(records, batch_dir / "adobe_stock_metadata.csv")
    validate_csv_images(csv_path, batch_dir)
    manifest = {"batch_id": batch_id, "asset_ids": asset_ids, "count": len(asset_ids)}
    (batch_dir / "batch_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    for asset_id in asset_ids:
        current = registry.get_asset(asset_id)
        if current and current["status"] == "READY_TO_STAGE":
            registry.transition(asset_id, "STAGED")
            registry.update_asset(asset_id, staged_at=now_iso())
            registry.transition(asset_id, "READY_TO_UPLOAD")
        asset = registry.get_asset(asset_id)
        AssetLogger(Path(asset["image_path"]).parent).event(
            "staging", batch_id=batch_id,
            duration_seconds=round(time.perf_counter() - started, 3),
        )
    return batch_dir
