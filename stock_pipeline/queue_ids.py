"""Assign AST identifiers only to completely unnumbered production queues.

The queue file is the durable source for reruns.  SQLite serializes allocation;
all known queue files and generated asset directories contribute to the high
watermark so an empty/stale registry cannot silently reuse their identifiers.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from .contracts import ContractError, validate_topic_queue
from .registry import Registry

ASSET_ID = re.compile(r"^AST([0-9]{6})$")


def _number(value: Any) -> int:
    match = ASSET_ID.fullmatch(value) if isinstance(value, str) else None
    return int(match.group(1)) if match else 0


def _queue_file_ids(project_root: Path) -> dict[str, set[Path]]:
    """Read actual asset_id fields, ignoring descriptive mentions of AST IDs."""
    used: dict[str, set[Path]] = {}
    for directory in (project_root / "queues", project_root / "work_io" / "topic_finder"):
        if not directory.is_dir():
            continue
        for path in directory.rglob("*.json"):
            try:
                document = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ContractError(f"cannot scan queue file for used AST IDs: {path}: {exc}") from exc
            assets = document.get("assets") if isinstance(document, dict) else None
            if not isinstance(assets, list):
                continue
            for asset in assets:
                if not isinstance(asset, dict):
                    continue
                value = asset.get("asset_id")
                if _number(value):
                    used.setdefault(value, set()).add(path.resolve())
    return used


def _generated_asset_ids(project_root: Path) -> set[str]:
    used: set[str] = set()
    assets_dir = project_root / "output" / "assets"
    if assets_dir.is_dir():
        used.update(path.name for path in assets_dir.iterdir() if path.is_dir() and _number(path.name))
    batches_dir = project_root / "output" / "adobe_batches"
    if batches_dir.is_dir():
        used.update(path.stem for path in batches_dir.rglob("*.png") if _number(path.stem))
    return used


def _write_queue_atomic(queue: dict[str, Any], path: Path, previous_bytes: bytes) -> None:
    """Replace the user's JSON only after a final concurrent-edit check."""
    if path.read_bytes() != previous_bytes:
        raise ContractError(f"queue changed during ID allocation; retry with the current file: {path}")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(queue, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        # A queue with filled IDs is recoverable if the process exits before
        # SQLite's allocator transaction commits: subsequent scans see the IDs.
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def prepare_queue_asset_ids(queue_path: Path, project_root: Path, database_path: Path) -> list[str]:
    """Persist fresh IDs iff every asset_id is missing/blank; otherwise stay strict.

    Explicitly numbered queues are never renumbered. Mixed numbered/unnumbered
    queues fail rather than silently guessing which IDs the user intended.
    Returns the newly assigned identifiers, or [] for a numbered input.
    """
    try:
        original = queue_path.read_bytes()
        data = json.loads(original.decode("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"unable to read topic queue {queue_path}: {exc}") from exc

    if not isinstance(data, dict) or not isinstance(data.get("assets"), list) or not data["assets"]:
        # Keep the exact existing strict contract error for malformed queues.
        validate_topic_queue(data)
        return []

    assets = data["assets"]
    if not all(isinstance(asset, dict) for asset in assets):
        validate_topic_queue(data)
        return []
    blank = [asset.get("asset_id") in (None, "") for asset in assets]
    if not any(blank):
        # Manual IDs stay unchanged. Also check other local queue files, since
        # an ID collision might not yet be represented in the SQLite registry.
        validate_topic_queue(data)
        other_files = _queue_file_ids(project_root)
        this_file = queue_path.resolve()
        for asset in assets:
            conflicts = other_files.get(asset["asset_id"], set()) - {this_file}
            if conflicts:
                other = sorted(conflicts)[0]
                raise ContractError(
                    f"manually assigned asset_id {asset['asset_id']} already appears in {other}; "
                    "refusing to reassign an explicitly numbered queue"
                )
        return []
    if not all(blank):
        raise ContractError("mixed asset_id fields: remove every asset_id to auto-assign, or supply all valid AST IDs")

    # Validate before touching SQLite or rewriting the file. Provisional IDs
    # exist only in memory and are never emitted to a user or to a queue file.
    proposed = deepcopy(data)
    for index, asset in enumerate(proposed["assets"], start=1):
        if index > 999999:
            raise ContractError("too many assets for the six-digit AST identifier contract")
        asset["asset_id"] = f"AST{index:06d}"
    validate_topic_queue(proposed)

    with Registry(database_path) as registry:
        connection = registry.connection
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM assets WHERE run_id=? LIMIT 1", (data["run_id"],)).fetchone():
                raise ContractError(
                    f"run_id {data['run_id']} already exists in the registry; "
                    "restore its previously assigned queue IDs before resuming"
                )
            queue_files = _queue_file_ids(project_root)
            database_ids = {
                row[0] for row in connection.execute("SELECT asset_id FROM assets")
            }
            database_ids.update(
                row[0] for row in connection.execute(
                    "SELECT production_asset_id FROM candidate_topics WHERE production_asset_id IS NOT NULL"
                )
            )
            known_ids = set(queue_files) | database_ids | _generated_asset_ids(project_root)
            highest_used = max((_number(identifier) for identifier in known_ids), default=0)
            next_reserved = connection.execute(
                "SELECT next_number FROM id_allocator WHERE singleton=1"
            ).fetchone()[0]
            first = max(highest_used + 1, next_reserved)
            last = first + len(assets) - 1
            if last > 999999:
                raise ContractError("AST six-digit ID range exhausted; refusing to reuse any identifier")
            assigned = [f"AST{number:06d}" for number in range(first, last + 1)]
            for asset, identifier in zip(proposed["assets"], assigned):
                asset["asset_id"] = identifier
            validate_topic_queue(proposed)
            _write_queue_atomic(proposed, queue_path, original)
            connection.execute(
                "UPDATE id_allocator SET next_number=? WHERE singleton=1", (last + 1,)
            )
    return assigned
