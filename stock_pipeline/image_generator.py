from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PIL import Image


IMAGE_THINKING_LEVELS = {
    "gemini-3-pro-image": ("HIGH",),
    "gemini-nano-banana-2.1": ("MINIMAL", "MEDIUM", "HIGH"),
}


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
    usage_callback: Callable[[dict[str, int | float]], None] | None = None,
    thinking_level: str | None = None,
) -> Path:
    generate_kwargs = dict(
        client=client,
        prompt=prompt,
        model=model,
        temperature=temperature,
        top_p=top_p,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
    )
    if usage_callback is not None:
        generate_kwargs["usage_callback"] = usage_callback
    if thinking_level is not None:
        generate_kwargs["thinking_level"] = thinking_level
    image_bytes = generate_bytes(**generate_kwargs)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    import io
    with Image.open(io.BytesIO(image_bytes)) as image:
        image.verify()
        if (image.format or "").upper() != "PNG":
            raise ValueError(f"generated original must be PNG, got {image.format}")
    fd, temporary = tempfile.mkstemp(prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(image_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output_path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return output_path
