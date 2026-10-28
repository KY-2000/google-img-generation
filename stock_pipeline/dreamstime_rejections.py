from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


# Verified against the bundled Dreamstime template's Image Legend sheet.
# This workflow is intentionally scoped to Stock Icon Pipeline illustrations.
DREAMSTIME_ICON_CATEGORIES = (212, 163, 166)


@dataclass(frozen=True)
class AdobeRejection:
    title: str
    original_name: str
    submitted_at: datetime
    reason: str


@dataclass(frozen=True)
class LocalAsset:
    asset_id: str
    image_path: Path
    metadata_path: Path
    title: str
    keywords: list[str]


def parse_adobe_rejections(values: Any, *, since: datetime) -> list[AdobeRejection]:
    if not isinstance(values, list):
        raise ValueError("Adobe rejection scan must be a JSON array")
    result = []
    seen = set()
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            raise ValueError(f"rejection[{index}] must be an object")
        missing = {"title", "original_name", "submitted_at"} - set(value)
        if missing:
            raise ValueError(f"rejection[{index}] missing: {', '.join(sorted(missing))}")
        title = str(value["title"]).strip()
        original_name = Path(str(value["original_name"]).strip()).name
        if not title or not re.fullmatch(r"AST\d{6}\.[A-Za-z0-9]+", original_name, re.I):
            raise ValueError(f"rejection[{index}] has invalid title or original_name")
        submitted_at = datetime.fromisoformat(str(value["submitted_at"]).replace("Z", "+00:00"))
        if submitted_at.tzinfo is None:
            raise ValueError(f"rejection[{index}].submitted_at requires timezone")
        if original_name.casefold() in seen:
            continue
        seen.add(original_name.casefold())
        if submitted_at >= since:
            result.append(AdobeRejection(title, original_name, submitted_at, str(value.get("reason", "")).strip()))
    return sorted(result, key=lambda item: item.submitted_at, reverse=True)


def resolve_local_asset(project_root: Path, original_name: str) -> LocalAsset:
    asset_id = Path(original_name).stem.upper()
    if not re.fullmatch(r"AST\d{6}", asset_id):
        raise ValueError(f"unsupported Adobe original filename: {original_name}")
    asset_dir = project_root / "output" / "assets" / asset_id
    metadata_path = asset_dir / "metadata.json"
    preferred = asset_dir / "original.png"
    if not metadata_path.is_file():
        raise FileNotFoundError(f"metadata not found for {asset_id}: {metadata_path}")
    image_path = preferred if preferred.is_file() else next(
        (path for path in asset_dir.iterdir() if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg"}),
        None,
    )
    if image_path is None:
        raise FileNotFoundError(f"image not found for {asset_id}: {asset_dir}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    keywords = metadata.get("keywords")
    if not isinstance(keywords, list) or not all(isinstance(item, str) and item.strip() for item in keywords):
        raise ValueError(f"invalid keywords for {asset_id}")
    return LocalAsset(asset_id, image_path, metadata_path, str(metadata.get("title", "")).strip(), [item.strip() for item in keywords])


def build_dreamstime_row(*, filename: str, title: str, keywords: list[str]) -> dict[str, Any]:
    clean_title = " ".join(title.split())
    description = f"{clean_title}. Commercial AI-generated illustration suitable for web, print, and design projects."
    return {
        "Filename": filename,
        "Image Name": clean_title,
        "Description": description[:255],
        "Category 1": DREAMSTIME_ICON_CATEGORIES[0],
        "Category 2": DREAMSTIME_ICON_CATEGORIES[1],
        "Category 3": DREAMSTIME_ICON_CATEGORIES[2],
        "keywords": ",".join(dict.fromkeys(item.strip() for item in keywords if item.strip())),
        "Free": 0,
        "W-EL": 1,
        "P-EL": 1,
        "SR-EL": 1,
        "SR-Price": 1,
        "Editorial": 0,
        "MR doc Ids": 0,
        "Pr Docs": 0,
    }


def stage_manifest_images(manifest: dict[str, Any], destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for item in manifest.get("items", []):
        source = Path(item["image_path"])
        filename = Path(item["spreadsheet_row"]["Filename"]).name
        target = destination / filename
        shutil.copy2(source, target)
        item["source_image_path"] = str(source.resolve())
        item["image_path"] = str(target.resolve())


def prepare_manifest(project_root: Path, scan_values: Any, *, since: datetime) -> dict[str, Any]:
    items = []
    unresolved = []
    for rejection in parse_adobe_rejections(scan_values, since=since):
        try:
            local = resolve_local_asset(project_root, rejection.original_name)
        except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as exc:
            unresolved.append({
                "title": rejection.title,
                "original_name": rejection.original_name,
                "submitted_at": rejection.submitted_at.isoformat(),
                "reason": rejection.reason,
                "error": str(exc),
            })
            continue
        row = build_dreamstime_row(filename=rejection.original_name, title=rejection.title, keywords=local.keywords)
        items.append({
            "asset_id": local.asset_id,
            "title": rejection.title,
            "reason": rejection.reason,
            "submitted_at": rejection.submitted_at.isoformat(),
            "image_path": str(local.image_path.resolve()),
            "metadata_path": str(local.metadata_path.resolve()),
            "keywords": local.keywords,
            "spreadsheet_row": row,
        })
    return {
        "since": since.isoformat(), "count": len(items),
        "unresolved_count": len(unresolved), "items": items, "unresolved": unresolved,
    }
