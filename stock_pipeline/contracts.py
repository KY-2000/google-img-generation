from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any


ASSET_ID_PATTERN = re.compile(r"^AST\d{6}$")
RUN_ID_PATTERN = re.compile(r"^TF_[A-Za-z0-9_-]+$")
REQUIRED_QUEUE_FIELDS = ("run_id", "topic_finder_version", "generated_at", "assets")
REQUIRED_ASSET_FIELDS = (
    "asset_id",
    "domain",
    "theme",
    "source_type",
    "topic_score",
    "topic_bucket",
    "icon_1",
    "icon_2",
    "icon_3",
    "icon_4",
    "palette",
    "status",
    "topic_finder_version",
)
QUEUE_FIELDS = set(REQUIRED_QUEUE_FIELDS)
ASSET_FIELDS = set(REQUIRED_ASSET_FIELDS)


class ContractError(ValueError):
    pass


def validate_topic_queue(queue: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(queue, dict):
        raise ContractError("topic queue must be a JSON object")
    for field in REQUIRED_QUEUE_FIELDS:
        if field not in queue:
            raise ContractError(f"topic queue is missing required field: {field}")
    unexpected = set(queue) - QUEUE_FIELDS
    if unexpected:
        raise ContractError(f"unexpected topic queue fields: {sorted(unexpected)}")
    if not isinstance(queue["run_id"], str) or not RUN_ID_PATTERN.fullmatch(queue["run_id"]):
        raise ContractError("run_id must match ^TF_[A-Za-z0-9_-]+$")
    if not isinstance(queue["topic_finder_version"], str) or not queue["topic_finder_version"].strip():
        raise ContractError("topic_finder_version must be a non-empty string")
    if not isinstance(queue["generated_at"], str):
        raise ContractError("generated_at must be a string")
    try:
        generated_at = datetime.fromisoformat(str(queue["generated_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError("generated_at must be a valid ISO 8601 date-time") from exc
    if generated_at.tzinfo is None:
        raise ContractError("generated_at must include a timezone offset")
    if not isinstance(queue["assets"], list) or not queue["assets"]:
        raise ContractError("assets must be a non-empty array")

    seen: set[str] = set()
    for index, asset in enumerate(queue["assets"]):
        if not isinstance(asset, dict):
            raise ContractError(f"assets[{index}] must be an object")
        for field in REQUIRED_ASSET_FIELDS:
            if field not in asset or asset[field] in (None, ""):
                raise ContractError(f"assets[{index}] is missing required field: {field}")
        unexpected_asset = set(asset) - ASSET_FIELDS
        if unexpected_asset:
            raise ContractError(f"unexpected fields in assets[{index}]: {sorted(unexpected_asset)}")
        for field in REQUIRED_ASSET_FIELDS:
            if field == "topic_score":
                continue
            if not isinstance(asset[field], str):
                raise ContractError(f"assets[{index}].{field} must be a string")
        asset_id = str(asset["asset_id"])
        if not ASSET_ID_PATTERN.fullmatch(asset_id):
            raise ContractError(f"invalid asset_id: {asset_id}")
        if asset_id in seen:
            raise ContractError(f"duplicate asset_id: {asset_id}")
        seen.add(asset_id)
        if type(asset["topic_score"]) not in {int, float} or not 0 <= asset["topic_score"] <= 100:
            raise ContractError(f"invalid topic_score for {asset_id}")
        if asset["status"] != "APPROVED":
            raise ContractError(f"asset {asset_id} status must be APPROVED")
        if asset["topic_finder_version"] != queue["topic_finder_version"]:
            raise ContractError(f"topic_finder_version mismatch for {asset_id}")
    return deepcopy(queue)


def load_topic_queue(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"unable to read topic queue {path}: {exc}") from exc
    return validate_topic_queue(data)
