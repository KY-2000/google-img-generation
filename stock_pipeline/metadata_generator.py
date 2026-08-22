from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def build_metadata_user_prompt(theme: str, generation_prompt: str, category_list: str) -> str:
    return (
        "Analyze this final image for Adobe Stock metadata.\n\n"
        "The final image is the primary source of truth.\n\n"
        f"Theme:\n{theme}\n\n"
        f"Generation prompt:\n{generation_prompt}\n\n"
        f"Category list:\n{category_list}\n\n"
        "Return structured JSON only."
    )


def parse_metadata_json(response_text: str) -> dict[str, object]:
    text = response_text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"metadata response is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("metadata response must be a JSON object")
    return value


def generate_metadata_for_asset(
    *, client: Any, provider: str, image_path: Path, theme: str,
    generation_prompt: str, category_list: str, system_prompt: str,
    model: str, temperature: float = 0.2, top_p: float = 0.9,
    asset_id: str | None = None, project_root: Path | None = None,
) -> str:
    import main as legacy

    user_prompt = build_metadata_user_prompt(theme, generation_prompt, category_list)
    providers = {
        "google": legacy.generate_google_metadata_text,
        "vertex": legacy.generate_google_metadata_text,
        "openrouter": legacy.generate_openrouter_metadata_text,
        "nim": legacy.generate_nim_metadata_text,
    }
    try:
        generator = providers[provider]
    except KeyError as exc:
        raise ValueError(f"unsupported metadata provider: {provider}") from exc
    return generator(
        client=client, image_path=image_path, user_prompt=user_prompt,
        system_prompt=system_prompt, model=model,
        temperature=temperature, top_p=top_p,
    )
