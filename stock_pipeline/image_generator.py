from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from PIL import Image


def generate_original_image(
    *,
    generate_bytes: Callable[..., bytes],
    client: Any,
    prompt: str,
    model: str,
    output_path: Path,
    temperature: float = 1.0,
    top_p: float = 0.95,
    aspect_ratio: str = "1:1",
    resolution: str = "2K",
) -> Path:
    image_bytes = generate_bytes(
        client=client,
        prompt=prompt,
        model=model,
        temperature=temperature,
        top_p=top_p,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    import io
    with Image.open(io.BytesIO(image_bytes)) as image:
        image.verify()
        if (image.format or "").upper() != "PNG":
            raise ValueError(f"generated original must be PNG, got {image.format}")
    output_path.write_bytes(image_bytes)
    return output_path
