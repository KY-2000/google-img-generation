from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


STRATEGIES = {
    "WORKFLOW_PROCESS", "INDUSTRY_VERTICAL", "PROBLEM_RISK", "REGIONAL_COMMERCIAL",
    "BORING_BUSINESS_OPERATIONS", "ROLE_SPECIFIC", "EMERGING_COMMERCIAL", "EVERYDAY_SCENARIO",
}
SOURCE_TYPES = {"EVERGREEN", "EMERGING", "SEASONAL"}
SIMILARITY_RISKS = {"LOW", "MEDIUM", "HIGH"}
OPTIONAL_FIELDS = {"region", "industry", "target_role", "problem_type"}
REQUIRED_FIELDS = {
    "candidate_id", "strategy", "domain", "theme", "source_type", "topic_score",
    "buyer_use_case", "differentiation_reason", "similarity_risk", "icon_1", "icon_2",
    "icon_3", "icon_4", "palette", "status",
}
KNOWN_FIELDS = REQUIRED_FIELDS | OPTIONAL_FIELDS
TEXT_FIELDS = REQUIRED_FIELDS - {"topic_score"}


class CandidateContractError(ValueError):
    pass


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def validate_finder_candidates(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, list):
        raise CandidateContractError("Finder input must be a top-level JSON array")
    if not data:
        raise CandidateContractError("Finder candidate array must not be empty")
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(data):
        label = f"candidate[{index}]"
        if not isinstance(raw, dict):
            raise CandidateContractError(f"{label} must be an object")
        missing = REQUIRED_FIELDS - set(raw)
        if missing:
            raise CandidateContractError(f"{label} missing required field: {sorted(missing)[0]}")
        unknown = set(raw) - KNOWN_FIELDS
        if unknown:
            raise CandidateContractError(f"{label} has unknown fields: {sorted(unknown)}")
        item = deepcopy(raw)
        for field in TEXT_FIELDS:
            if not isinstance(item[field], str) or not _normalize_text(item[field]):
                raise CandidateContractError(f"{label}.{field} must be a non-empty string")
            item[field] = _normalize_text(item[field])
        for field in OPTIONAL_FIELDS:
            value = item.get(field)
            if value is not None:
                if not isinstance(value, str):
                    raise CandidateContractError(f"{label}.{field} must be a string or null")
                item[field] = _normalize_text(value) or None
        if item["strategy"] not in STRATEGIES:
            raise CandidateContractError(f"{label}.strategy is invalid: {item['strategy']}")
        if item["source_type"] not in SOURCE_TYPES:
            raise CandidateContractError(f"{label}.source_type is invalid: {item['source_type']}")
        if item["similarity_risk"] not in SIMILARITY_RISKS:
            raise CandidateContractError(f"{label}.similarity_risk is invalid: {item['similarity_risk']}")
        if item["status"] != "CANDIDATE":
            raise CandidateContractError(f"{label}.status must be CANDIDATE")
        score = item["topic_score"]
        if type(score) not in {int, float} or not 0 <= score <= 100:
            raise CandidateContractError(f"{label}.topic_score must be a number from 0 to 100")
        identity = (item["strategy"], item["candidate_id"])
        if identity in seen:
            raise CandidateContractError(f"duplicate candidate identity: {identity[0]}/{identity[1]}")
        seen.add(identity)
        result.append(item)
    return result


def load_finder_candidates(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CandidateContractError(f"unable to read Finder candidates {path}: {exc}") from exc
    return validate_finder_candidates(data)
