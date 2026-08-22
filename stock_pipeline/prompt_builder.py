from __future__ import annotations

import re
from pathlib import Path


DEFAULT_STYLE = "clean 2D flat minimalist commercial illustration"
PLACEHOLDER = re.compile(r"{{([A-Z0-9_]+)}}")


def render_image_prompt(template: str, asset: dict[str, object], style: str = DEFAULT_STYLE) -> str:
    values = {
        "THEME": asset.get("theme"),
        "ICON_1": asset.get("icon_1"),
        "ICON_2": asset.get("icon_2"),
        "ICON_3": asset.get("icon_3"),
        "ICON_4": asset.get("icon_4"),
        "PALETTE": asset.get("palette"),
        "STYLE": style,
    }
    rendered = template
    for key, value in values.items():
        if value in (None, ""):
            raise ValueError(f"missing prompt value: {key}")
        rendered = rendered.replace("{{" + key + "}}", str(value))
    unresolved = PLACEHOLDER.findall(rendered)
    if unresolved:
        raise ValueError(f"unresolved prompt placeholders: {', '.join(unresolved)}")
    return rendered.strip()


def render_image_prompt_file(path: Path, asset: dict[str, object], style: str = DEFAULT_STYLE) -> str:
    return render_image_prompt(path.read_text(encoding="utf-8"), asset, style)

