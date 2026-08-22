from __future__ import annotations

import re
from typing import Any


class MetadataValidationError(ValueError):
    pass


def normalize_keyword(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def validate_metadata(
    metadata: dict[str, Any], asset_id: str, categories: dict[int, str],
    *, max_title_chars: int = 70, hard_max_keywords: int = 49,
) -> dict[str, Any]:
    required = ("asset_id", "title", "keywords", "category_code", "category_name", "keyword_count", "metadata_prompt_version", "qc")
    allowed = set(required)
    missing = [field for field in required if field not in metadata]
    if missing:
        raise MetadataValidationError(f"missing metadata fields: {', '.join(missing)}")
    unexpected = set(metadata) - allowed
    if unexpected:
        raise MetadataValidationError(f"malformed metadata fields: {sorted(unexpected)}")
    if metadata["asset_id"] != asset_id:
        raise MetadataValidationError("metadata asset_id does not match pipeline asset")
    if metadata["metadata_prompt_version"] != "META_V2":
        raise MetadataValidationError("metadata_prompt_version must be META_V2")
    qc_fields = {
        "image_grounded", "top10_defensible", "contains_speculation",
        "contains_redundancy", "commercial_ready",
    }
    qc = metadata["qc"]
    if not isinstance(qc, dict) or set(qc) != qc_fields or not all(type(qc[field]) is bool for field in qc_fields):
        raise MetadataValidationError("qc must match the metadata schema and contain booleans")
    if (
        not qc["image_grounded"] or not qc["top10_defensible"]
        or qc["contains_speculation"] or qc["contains_redundancy"]
        or not qc["commercial_ready"]
    ):
        raise MetadataValidationError("metadata qc is not commercial-ready")
    title = metadata["title"]
    if not isinstance(title, str) or not title.strip():
        raise MetadataValidationError("title must be present")
    if len(title) > max_title_chars:
        raise MetadataValidationError(f"title exceeds {max_title_chars} characters")
    keywords = metadata["keywords"]
    if not isinstance(keywords, list) or not keywords or not all(isinstance(item, str) and item.strip() for item in keywords):
        raise MetadataValidationError("keywords must be a non-empty string list")
    if len(keywords) > hard_max_keywords:
        raise MetadataValidationError(f"keywords exceed hard maximum of {hard_max_keywords}")
    normalized = [normalize_keyword(item) for item in keywords]
    if len(set(normalized)) != len(normalized):
        raise MetadataValidationError("duplicate keywords detected after normalization")
    code = metadata["category_code"]
    if not isinstance(code, int) or code not in categories:
        raise MetadataValidationError("category code is invalid")
    if metadata["category_name"] != categories[code]:
        raise MetadataValidationError("category name does not match category code")
    if "keyword_count" in metadata and metadata["keyword_count"] != len(keywords):
        raise MetadataValidationError("keyword_count does not match keywords")
    result = dict(metadata)
    result["title"] = title.strip()
    result["keywords"] = [item.strip() for item in keywords]
    result["keyword_count"] = len(keywords)
    return result


def parse_categories(text: str) -> dict[int, str]:
    categories: dict[int, str] = {}
    for line in text.splitlines():
        match = re.fullmatch(r"\s*(\d+)\.\s+(.+?)\s*", line)
        if match:
            categories[int(match.group(1))] = match.group(2)
    if not categories:
        raise MetadataValidationError("category list is empty or malformed")
    return categories
