from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image


def inspect_image(asset_id: str, image_path: Path, minimum_resolution: int = 2048, *, aspect_ratio: str | None = None) -> dict[str, Any]:
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
            if aspect_ratio is not None:
                numerator, denominator = map(int, aspect_ratio.split(":"))
                result["aspect_ratio_valid"] = abs(width / height / (numerator / denominator) - 1) <= 0.02
                # Non-square 4K output has a 4K long edge, not two 4K edges.
                result["resolution_valid"] = max(width, height) >= minimum_resolution
    except Exception as exc:
        result["error"] = str(exc)
    return result

