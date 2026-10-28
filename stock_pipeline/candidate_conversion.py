from __future__ import annotations

import re
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .candidate_import import SIMILARITY_RISKS, STRATEGIES, load_finder_candidates
from .contracts import RUN_ID_PATTERN, validate_topic_queue
from .registry import Registry


def conversion_time_iso() -> str:
    try:
        zone = ZoneInfo("Asia/Kuala_Lumpur")
    except ZoneInfoNotFoundError:
        zone = timezone.utc
    return datetime.now(zone).isoformat()


def write_queue_atomic(queue: dict, output_path: Path) -> None:
    validated = validate_topic_queue(queue)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(validated, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temporary, output_path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def derive_run_id(source: Path, candidates: list[dict]) -> str:
    strategies = sorted({item["strategy"] for item in candidates})
    strategy = strategies[0] if len(strategies) == 1 else "MIXED"
    match = re.search(r"(?<!\d)(20\d{6})(?!\d)", source.stem)
    date = match.group(1) if match else datetime.fromtimestamp(source.stat().st_mtime).strftime("%Y%m%d")
    return f"TF_{strategy}_{date}"


def convert_candidates(
    input_path: Path, database_path: Path, *, run_id: str | None = None,
    topic_finder_version: str = "TF_V1_1", min_score: float = 0,
    include_risk: Iterable[str] = ("LOW", "MEDIUM", "HIGH"), strategy: str | None = None,
) -> dict:
    candidates = load_finder_candidates(input_path)
    risks = tuple(include_risk)
    invalid_risks = set(risks) - SIMILARITY_RISKS
    if invalid_risks:
        raise ValueError(f"invalid similarity risks: {sorted(invalid_risks)}")
    if strategy is not None and strategy not in STRATEGIES:
        raise ValueError(f"invalid strategy: {strategy}")
    effective_run_id = run_id or derive_run_id(input_path, candidates)
    if not RUN_ID_PATTERN.fullmatch(effective_run_id):
        raise ValueError("run_id must match ^TF_[A-Za-z0-9_-]+$")
    if not topic_finder_version.strip():
        raise ValueError("topic_finder_version must be a non-empty string")
    if not 0 <= min_score <= 100:
        raise ValueError("min_score must be from 0 to 100")
    selected = [
        item for item in candidates
        if item["topic_score"] >= min_score and item["similarity_risk"] in risks
        and (strategy is None or item["strategy"] == strategy)
    ]
    if not selected:
        raise ValueError("no candidates remain after filtering")
    assets = []
    with Registry(database_path) as registry:
        for item in candidates:
            registry.import_candidate(item, finder_run_id=effective_run_id, source_filename=input_path.name)
        for item in selected:
            asset_id = registry.allocate_candidate_asset_id(item["strategy"], item["candidate_id"])
            assets.append({
                "asset_id": asset_id, "domain": item["domain"], "theme": item["theme"],
                "source_type": item["source_type"], "topic_score": item["topic_score"],
                "topic_bucket": "APPROVED", "icon_1": item["icon_1"], "icon_2": item["icon_2"],
                "icon_3": item["icon_3"], "icon_4": item["icon_4"], "palette": item["palette"],
                "status": "APPROVED", "topic_finder_version": topic_finder_version,
            })
    return validate_topic_queue({
        "run_id": effective_run_id,
        "topic_finder_version": topic_finder_version,
        "generated_at": conversion_time_iso(),
        "assets": assets,
    })
