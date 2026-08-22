from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image


def inspect_image(asset_id: str, image_path: Path, minimum_resolution: int = 2048) -> dict[str, Any]:
    result = {
        "asset_id": asset_id,
        "image_valid": False,
        "square": False,
        "resolution_valid": False,
        "format_valid": False,
        "exactly_four_icons": None,
        "semantic_qc_status": "NOT_RUN",
        "commercial_ready": None,
    }
    if not image_path.exists():
        result["error"] = "image does not exist"
        return result
    try:
        with Image.open(image_path) as image:
            image.verify()
        with Image.open(image_path) as image:
            width, height = image.size
            result.update({
                "image_valid": True,
                "square": width == height,
                "resolution_valid": width >= minimum_resolution and height >= minimum_resolution,
                "format_valid": (image.format or "").upper() in {"PNG", "JPEG"},
                "width": width,
                "height": height,
                "format": image.format,
            })
    except Exception as exc:
        result["error"] = str(exc)
    return result

