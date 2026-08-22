from __future__ import annotations

import re


class FilenameError(ValueError):
    pass


def map_upload_filename(asset_id: str, extension: str = ".png", max_chars: int = 30) -> str:
    if not re.fullmatch(r"AST\d{6}", asset_id):
        raise FilenameError(f"invalid asset_id: {asset_id}")
    if not extension.startswith(".") or not re.fullmatch(r"\.[A-Za-z0-9]+", extension):
        raise FilenameError(f"invalid extension: {extension}")
    filename = f"{asset_id}{extension.lower()}"
    if len(filename) > max_chars:
        raise FilenameError(f"filename exceeds {max_chars} characters: {filename}")
    return filename


def validate_unique_filenames(filenames: list[str]) -> None:
    folded = [name.casefold() for name in filenames]
    if len(folded) != len(set(folded)):
        raise FilenameError("upload filenames must be unique")

