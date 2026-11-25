"""Discover immutable source queues and prepare resumable local copies."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from .candidate_conversion import convert_candidates, write_queue_atomic
from .candidate_import import validate_finder_candidates
from .contracts import load_topic_queue, validate_topic_queue
from .manager_baseline import load_baseline
from .queue_ids import _queue_file_ids, next_available_asset_number, prepare_queue_asset_ids
from .registry import Registry


def fingerprint(value) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass
class QueueSource:
    path: Path
    source_file: str
    source_content_sha256: str = ""
    queue_type: str = ""
    run_id: str = ""
    identity: str = ""
    data: dict | list | None = None
    order: str = ""
    error: str = ""
    duplicate: bool = False
    example: bool = False
    baseline: bool = False

    @property
    def count(self) -> int:
        return len(self.data) if isinstance(self.data, list) else len((self.data or {}).get("assets", []))


def discover(root: Path, commit_time: str, *, only: str | None = None,
             read_baseline: bool = False) -> list[QueueSource]:
    validators = {
        name: Draft202012Validator(
            json.loads((root / "schemas" / schema).read_text(encoding="utf-8")),
            format_checker=FormatChecker(),
        )
        for name, schema in (("PRODUCTION", "topic_queue.schema.json"), ("FINDER", "finder_candidates.schema.json"))
    }
    incoming = root / "queues" / "incoming"
    if not incoming.is_dir():
        raise ValueError("queues/incoming directory is missing")
    sources = []
    ignored_hashes = load_baseline(root)
    for path in sorted(incoming.rglob("*.json"), key=lambda p: (p.as_posix().casefold(), p.as_posix())):
        source = QueueSource(path, path.relative_to(root).as_posix(), order=commit_time)
        if only and source.source_file != only:
            continue
        sources.append(source)
        if path.name.casefold().endswith(".example.json"):
            source.example = True
            source.queue_type = "EXAMPLE"
            continue
        try:
            if not path.resolve().is_relative_to(incoming.resolve()):
                raise ValueError("Queue path points outside queues/incoming")
            raw = path.read_bytes()
            source.source_content_sha256 = sha256(raw).hexdigest()
            if not only and source.source_content_sha256 in ignored_hashes:
                source.baseline = True
                if not read_baseline:
                    source.queue_type = "BASELINE"
                    continue
            value = json.loads(raw.decode("utf-8-sig"))
            if isinstance(value, list):
                source.queue_type = "FINDER"
                validators["FINDER"].validate(value)
                source.data = validate_finder_candidates(value)
                source.identity = fingerprint(source.data)
                source.run_id = "TF_FINDER_" + source.identity
            elif isinstance(value, dict) and "assets" in value:
                source.queue_type = "PRODUCTION"
                source.run_id = str(value.get("run_id", ""))
                provisional = deepcopy(value)
                if isinstance(provisional["assets"], list) and all(isinstance(a, dict) for a in provisional["assets"]):
                    blanks = [a.get("asset_id") in (None, "") for a in provisional["assets"]]
                    if any(blanks) and not all(blanks):
                        raise ValueError("Mixed numbered/unnumbered assets; supply every ID or omit all IDs")
                    if blanks and all(blanks):
                        for index, asset in enumerate(provisional["assets"], 1):
                            asset["asset_id"] = f"AST{index:06d}"
                        for asset in value["assets"]:
                            asset.pop("asset_id", None)
                validators["PRODUCTION"].validate(provisional)
                validate_topic_queue(provisional)
                source.data = value
                source.identity = fingerprint(value)
                source.order = value["generated_at"]
            else:
                raise ValueError("Expected a production queue object with assets or a Finder candidate array")
        except Exception as exc:
            # JSON Schema errors include the entire instance. Report only location/reason.
            if hasattr(exc, "absolute_path"):
                location = ".".join(map(str, exc.absolute_path)) or "root"
                source.error = f"Schema violation at {location} ({exc.validator})"
            else:
                source.error = str(exc)
    sources.sort(key=lambda s: (
        datetime.fromisoformat(s.order.replace("Z", "+00:00")).timestamp(),
        s.source_file.casefold(), s.source_file,
    ))
    groups = defaultdict(list)
    for source in sources:
        if not (source.error or source.example or source.baseline):
            groups[source.run_id].append(source)
    for run_id, group in groups.items():
        if len({s.identity for s in group}) > 1:
            for s in group:
                s.error = f"Incompatible queue contents for run_id {run_id}"
        else:
            for s in group[1:]:
                s.duplicate = True

    owners = defaultdict(list)
    for source in sources:
        if source.error or source.duplicate or source.example or source.baseline:
            continue
        if source.queue_type == "PRODUCTION":
            identities = [a["asset_id"] for a in source.data["assets"] if "asset_id" in a]
        else:
            identities = [(a["strategy"], a["candidate_id"]) for a in source.data]
        for identity in identities:
            owners[identity].append(source)
    for identity, group in owners.items():
        if len(group) > 1:
            for source in group:
                source.error = f"Identity {identity} also belongs to another incoming queue"
    return sources


def adopt_existing_finder_run(registry: Registry, source: QueueSource) -> None:
    """Adopt an exact previously converted Finder batch without new AST IDs."""
    if source.queue_type != "FINDER":
        return
    prior = [registry.get_candidate(c["strategy"], c["candidate_id"]) for c in source.data]
    assigned = [row for row in prior if row and row["production_asset_id"]]
    foreign = {row["finder_run_id"] for row in assigned} - {source.run_id}
    if not foreign:
        return
    if len(assigned) != len(prior) or len(foreign) != 1 or any(row["finder_run_id"] not in foreign for row in assigned):
        raise ValueError("Finder batch overlaps another batch without matching it in full")
    for candidate, row in zip(source.data, prior):
        if any(row.get(key) != value for key, value in candidate.items() if key != "status"):
            raise ValueError("Previously converted Finder candidate content changed")
    run_id = foreign.pop()
    expected = {row["production_asset_id"] for row in assigned}
    owned = {row[0] for row in registry.connection.execute(
        "SELECT production_asset_id FROM candidate_topics WHERE finder_run_id=? AND production_asset_id IS NOT NULL",
        (run_id,),
    )}
    if expected != owned:
        raise ValueError("Finder batch does not contain the complete previously converted asset set")
    source.run_id = run_id


def runtime_queue(root: Path, registry: Registry, source: QueueSource) -> dict:
    folder = root / "work_io" / "topic_finder" / "runtime_queues"
    folder.mkdir(parents=True, exist_ok=True)
    # Hash avoids Windows case-folding and path-length collisions.
    path = folder / (fingerprint(source.run_id) + ".json")
    registered = [a for a in registry.list_assets() if a["run_id"] == source.run_id]
    if path.exists():
        queue = load_topic_queue(path)
    elif source.queue_type == "FINDER":
        # A candidate ID belongs to one batch. Reusing it with different contents
        # would otherwise silently reuse an AST ID allocated to another run.
        for candidate in source.data:
            prior = registry.get_candidate(candidate["strategy"], candidate["candidate_id"])
            if prior and prior["production_asset_id"] and prior["finder_run_id"] != source.run_id:
                raise ValueError(f"Finder candidate already allocated in {prior['finder_run_id']}; use its existing production queue")
        connection = registry.connection
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            number = next_available_asset_number(root, registry, skip_invalid_json=True)
            connection.execute("UPDATE id_allocator SET next_number=? WHERE singleton=1", (number,))
        # Use the repository conversion, with a content-derived run ID rather than
        # its legacy date-only default. Persist the input snapshot outside the scan.
        snapshot = root / "output" / "stock_manager" / "finder_sources" / source.identity / source.path.name
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_text(json.dumps(source.data, ensure_ascii=False), encoding="utf-8")
        queue = convert_candidates(snapshot, registry.path, run_id=source.run_id)
        write_queue_atomic(queue, path)
    else:
        queue = deepcopy(source.data)
        if "asset_id" not in queue["assets"][0]:
            if registered:
                # Adopt a prior single-queue allocation only if the entire run
                # matches in Registry.register_queue below.
                if len(registered) != len(queue["assets"]):
                    raise ValueError("Existing run has a different number of assets; restore its numbered queue")
                for asset, previous in zip(queue["assets"], registered):
                    asset["asset_id"] = previous["asset_id"]
            else:
                if not path.exists():
                    # An interrupted pre-allocation must never leave an invalid
                    # runtime file in the AST search directory.
                    provisional = path.with_suffix(".pending")
                    provisional.write_text(json.dumps(queue, ensure_ascii=False), encoding="utf-8")
                    prepare_queue_asset_ids(provisional, root, registry.path, skip_invalid_json=True)
                    provisional.replace(path)
                queue = load_topic_queue(path)
        elif not registered:
            used = _queue_file_ids(root, skip_invalid_json=True)
            for asset in queue["assets"]:
                aid = asset["asset_id"]
                for other in used.get(aid, set()) - {source.path.resolve(), path.resolve()}:
                    other_queue = json.loads(other.read_text(encoding="utf-8-sig"))
                    if other_queue != queue:
                        raise ValueError(f"AST ID {aid} also occurs in {other.name}")
                reserved = registry.connection.execute(
                    "SELECT 1 FROM candidate_topics WHERE production_asset_id=?", (aid,)
                ).fetchone()
                if reserved:
                    raise ValueError(f"AST ID {aid} is reserved for a Finder candidate")
                if (root / "output/assets" / aid).exists() and not registry.get_asset(aid):
                    raise ValueError(f"AST ID {aid} already has local output without a registry record")
        # Write numbered copies only after identity validation below.
    if queue["run_id"] != source.run_id:
        raise ValueError("Runtime queue run_id does not match the source")
    if source.queue_type == "PRODUCTION":
        comparable = deepcopy(queue)
        if "asset_id" not in source.data["assets"][0]:
            for asset in comparable["assets"]:
                asset.pop("asset_id", None)
        if fingerprint(comparable) != source.identity:
            raise ValueError("Runtime queue differs from source production identity")
    if registered and {a["asset_id"] for a in registered} != {a["asset_id"] for a in queue["assets"]}:
        raise ValueError("Existing registry run has a different asset set")
    registry.register_queue(queue)
    if not path.exists():
        write_queue_atomic(queue, path)
    return queue
