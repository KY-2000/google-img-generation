from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .registry import Registry


COMPLETED_OR_WAITING = {"METADATA_READY", "READY_TO_STAGE", "STAGED", "READY_TO_UPLOAD", "UPLOADED_DRAFT"}


def process_batch(
    registry: Registry,
    processor: Callable[[dict[str, Any]], None],
    *,
    resume: bool = False,
    asset_ids: set[str] | None = None,
) -> dict[str, int]:
    assets = registry.list_assets()
    if asset_ids is not None:
        assets = [asset for asset in assets if asset["asset_id"] in asset_ids]
    failed = 0
    succeeded = 0
    for asset in assets:
        if resume and asset["status"] in COMPLETED_OR_WAITING:
            succeeded += 1
            continue
        try:
            processor(asset)
            succeeded += 1
        except Exception as exc:
            failed += 1
            current = registry.get_asset(asset["asset_id"])
            if current and current["status"] in {"IMAGE_QC_FAILED", "METADATA_QC_FAILED"}:
                registry.update_asset(asset["asset_id"], error=str(exc))
            elif current and current["status"] != "FAILED":
                registry.mark_failed(asset["asset_id"], str(exc))
    states = registry.list_assets()
    if asset_ids is not None:
        states = [asset for asset in states if asset["asset_id"] in asset_ids]
    return {
        "total": len(assets),
        "succeeded": succeeded,
        "failed": failed,
        "ready_to_upload": sum(a["status"] in {"READY_TO_UPLOAD", "UPLOADED_DRAFT"} for a in states),
        "needs_review": sum(a["status"] in {"IMAGE_QC_FAILED", "METADATA_QC_FAILED", "FAILED"} for a in states),
    }
