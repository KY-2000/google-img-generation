"""Sequential generation and one gated, resumable Adobe upload per Start."""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import main as legacy

from .ai_usage import UsageLedger
from .managed_adobe import build_combined_batch, managed_upload_command, reconcile_receipts
from .manager_baseline import TEST_QUEUE
from .manager_queues import adopt_existing_finder_run, discover, runtime_queue
from .manager_runtime import redactor
from .orchestrator import StockPipeline
from .registry import Registry, now_iso

DONE = {"METADATA_READY", "READY_TO_STAGE", "STAGED", "READY_TO_UPLOAD", "UPLOADED_DRAFT"}
FAILED = {"FAILED", "IMAGE_QC_FAILED", "METADATA_QC_FAILED"}
COUNTERS = (
    "scanned", "new_queues", "skipped_queues", "generated", "failed_assets", "prepared",
    "uploaded", "already_uploaded", "total_assets", "completed_assets",
)

SKIPPED = {"DUPLICATE", "SKIPPED_EXAMPLE", "SKIPPED_BASELINE"}


def validate_test_queue(root: Path, run_dir: Path) -> dict:
    """Run discovery/contract validation only. No registry, provider, or browser."""
    sources = discover(root, now_iso(), only=TEST_QUEUE)
    if not sources:
        raise ValueError(f"Test queue not found: {TEST_QUEUE}")
    source = sources[0]
    if source.error:
        raise ValueError(source.error)
    if source.queue_type != "PRODUCTION":
        raise ValueError("Expected a production queue for the offline test")
    report = {
        **dict.fromkeys(COUNTERS, 0),
        "status": "TEST_PASSED", "scanned": 1, "total_assets": source.count,
        "log_path": str(run_dir / "activity.log"),
        "queues": [{
            "source_file": source.source_file, "run_id": source.run_id, "queue_status": "VALIDATED",
            "total_assets": source.count, "completed_assets": 0,
            "assets": [{key: a.get(key) for key in ("asset_id", "aspect_ratio", "resolution")}
                       for a in source.data["assets"]],
        }],
    }
    write_report(report, run_dir)
    return report


def new_run_dir(root: Path) -> Path:
    path = root / "output" / "stock_manager" / "runs" / uuid4().hex
    path.mkdir(parents=True)
    return path


def write_report(report: dict, directory: Path) -> None:
    for name, content in (
        ("report.json", json.dumps(report, ensure_ascii=False, indent=2)),
        ("report.md", "\n".join([
            "# Adobe Stock Batch Manager", "",
            f"- Status: {report['status']}",
            f"- Commit: {report.get('git_commit', '')}",
            f"- Duration: {report.get('elapsed_seconds', 0)} seconds",
            f"- Activity log: {report.get('log_path', directory / 'activity.log')}",
            *(f"- {key.replace('_', ' ').title()}: {report.get(key, 0)}" for key in COUNTERS),
            "", str(report.get("last_error", "")), "",
            "## Queues", "",
            *(f"- {q['source_file']}: {q['queue_status']} "
              f"({q['completed_assets']}/{q['total_assets']}) {q.get('last_error') or ''}"
              for q in report.get("queues", [])),
        ])),
    ):
        target = directory / name
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(content + "\n", encoding="utf-8")
        temporary.replace(target)


class QueueState:
    def __init__(self, registry: Registry):
        self.registry = registry
        registry.connection.execute(
            "CREATE TABLE IF NOT EXISTS manager_queues ("
            "queue_key TEXT PRIMARY KEY, identity TEXT NOT NULL, state_json TEXT NOT NULL)"
        )
        registry.connection.commit()

    def get(self, key):
        row = self.registry.connection.execute(
            "SELECT identity,state_json FROM manager_queues WHERE queue_key=?", (key,),
        ).fetchone()
        return (row[0], json.loads(row[1])) if row else ("", {})

    def save(self, key, identity, state):
        with self.registry.connection:
            self.registry.connection.execute(
                "INSERT INTO manager_queues VALUES(?,?,?) "
                "ON CONFLICT(queue_key) DO UPDATE SET identity=excluded.identity,state_json=excluded.state_json",
                (key, identity, json.dumps(state, ensure_ascii=False)),
            )


def upload_process(root, batch, receipt_dir, *, cdp, profile, log, stop_file=None):
    command = managed_upload_command(batch, receipt_dir, cdp, profile, stop_file)
    with subprocess.Popen(command, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, encoding="utf-8", errors="replace",
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as process:
        for line in process.stdout:
            log(line.rstrip())
        if process.wait():
            raise RuntimeError("Adobe uploader stopped; confirmed drafts were retained. See activity log")


def run_manager(root: Path, commit: str, commit_time: str, run_dir: Path, *,
                should_stop=lambda: False, emit=lambda event: None,
                pipeline_factory=StockPipeline, uploader=upload_process,
                cdp="http://127.0.0.1:9222", profile=None, stop_file=None, image_model=None,
                metadata_model=None) -> dict:
    start = time.monotonic()
    log_path = run_dir / "activity.log"
    redact = redactor(root)
    report = dict.fromkeys(COUNTERS, 0)
    report.update(status="RUNNING", git_commit=commit, started_at=now_iso(), queues=[],
                  log_path=str(log_path), last_error="")
    db = root / "data" / "stock_pipeline.sqlite"
    receipts = root / "output" / "stock_manager" / "adobe_receipts"

    def log(message, phase=None, **fields):
        message = redact(message)
        stamp = now_iso()
        with log_path.open("a", encoding="utf-8") as out:
            out.write(f"{stamp} {message}\n")
        emit({"message": message, "time": stamp, "phase": phase, **fields})

    def overview():
        queues = report["queues"]
        states = [q["queue_status"] for q in queues]
        return {
            "total": len(queues),
            "pending": sum(s in {"NEW", "READY", "PARTIAL", "READY_TO_UPLOAD"} for s in states),
            "running": sum(s in {"RUNNING", "UPLOADING"} for s in states),
            "completed": sum(s == "UPLOADED_DRAFT" or s in SKIPPED for s in states),
            "failed": sum(s in {"INVALID", "FAILED"} for s in states),
        }

    def refresh(record, registry, ids):
        rows = [registry.get_asset(aid) for aid in ids]
        record["completed_assets"] = sum(row["status"] in DONE for row in rows)
        record["failed_assets"] = sum(row["status"] in FAILED for row in rows)
        errors = [f"{row['asset_id']}: {redact(row['error'] or row['status'])}"
                  for row in rows if row["status"] in FAILED]
        if errors:
            record["last_error"] = "; ".join(errors)
        if rows and all(row["status"] == "UPLOADED_DRAFT" for row in rows):
            record["queue_status"] = "UPLOADED_DRAFT"
            stamps = [row["uploaded_at"] for row in rows if row["uploaded_at"]]
            record["uploaded_at"] = max(stamps, default=None)
        elif rows and record["completed_assets"] == len(rows):
            record["queue_status"] = "READY_TO_UPLOAD"
        elif record["completed_assets"]:
            record["queue_status"] = "PARTIAL"
        else:
            record["queue_status"] = "FAILED" if record["failed_assets"] else "READY"
        if rows and record["completed_assets"] == len(rows):
            record["completed_at"] = record["completed_at"] or now_iso()
        return rows

    try:
        log(f"Scanning queues at {commit}", "Discovery")
        sources = discover(root, commit_time)
        report["scanned"] = len(sources)
        config = json.loads((root / "config" / "stock_icon_pipeline.json").read_text(encoding="utf-8"))
        ledger = UsageLedger(usage_dir=root / "output" / "usage", command_id="manager_" + run_dir.name,
                             progress=log)
        with Registry(db) as registry:
            state = QueueState(registry)
            prepared, ready, original_states = [], {}, {}
            for source in sources:
                if not (source.error or source.duplicate or source.example or source.baseline):
                    try:
                        adopt_existing_finder_run(registry, source)
                    except Exception as exc:
                        source.error = redact(exc)
                identity, old = state.get(source.run_id)
                record = {
                    "source_file": source.source_file, "source_commit_sha": commit,
                    "source_content_sha256": source.source_content_sha256,
                    "run_id": source.run_id, "queue_type": source.queue_type or "UNKNOWN",
                    "total_assets": source.count, "completed_assets": 0, "failed_assets": 0,
                    "queue_status": "VALIDATING", "started_at": old.get("started_at"),
                    "completed_at": old.get("completed_at"), "uploaded_at": old.get("uploaded_at"),
                    "batch_id": old.get("batch_id"), "last_error": "",
                }
                report["queues"].append(record)
                try:
                    if source.example or source.baseline:
                        record["queue_status"] = "SKIPPED_EXAMPLE" if source.example else "SKIPPED_BASELINE"
                        report["skipped_queues"] += 1
                        continue
                    if source.error:
                        raise ValueError(source.error)
                    if source.duplicate:
                        record["queue_status"] = "DUPLICATE"
                        report["skipped_queues"] += 1
                        continue
                    if identity and identity != source.identity:
                        raise ValueError(f"Content changed for existing run_id {source.run_id}; use a new run_id for a new batch")
                    if should_stop():
                        record["queue_status"] = "NEW"
                        continue
                    queue = runtime_queue(root, registry, source)
                    ids = [a["asset_id"] for a in queue["assets"]]
                    if set(ids) & original_states.keys():
                        raise ValueError("This asset set is already planned by a different source queue")
                    rows = refresh(record, registry, ids)
                    original_states.update({row["asset_id"]: row for row in rows})
                    report["already_uploaded"] += sum(row["status"] == "UPLOADED_DRAFT" for row in rows)
                    report["total_assets"] += len(ids)
                    if record["queue_status"] == "UPLOADED_DRAFT":
                        report["skipped_queues"] += 1
                    elif not identity:
                        report["new_queues"] += 1
                    state.save(source.run_id, source.identity, record)
                    prepared.append((source, queue, ids, record))
                except Exception as exc:
                    record.update(queue_status="INVALID", last_error=redact(exc))
                    state.save("invalid:" + source.source_file, source.identity, record)
                    log(f"INVALID {source.source_file}: {record['last_error']}", "Validation")

            def progress(message):
                match = re.match(r"\[(AST\d{6})\]\s*(.*)", message)
                log(message, match[2].split(" started", 1)[0] if match else "Generation",
                    asset=match[1] if match else "", source=current_source,
                    completed=sum(q["completed_assets"] for q in report["queues"]),
                    total=report["total_assets"], overview=overview())

            current_source = ""
            selected_model = legacy.resolve_image_model_alias(
                image_model or legacy.load_env_file(root / ".env").get("IMAGE_MODEL")
                or os.environ.get("IMAGE_MODEL") or "gemini-3-pro-image"
            )
            report["image_model"] = selected_model
            metadata_provider = "vertex" if metadata_model else legacy.get_default_metadata_provider(root)
            selected_metadata = metadata_model or legacy.get_default_metadata_model(root)
            report.update(metadata_model=selected_metadata, metadata_provider=metadata_provider)
            log(f"Image model: {selected_model}", "Generation")
            log(f"Metadata model: {selected_metadata} ({metadata_provider})", "Metadata")
            pipeline = pipeline_factory(
                project_root=root, registry=registry, config=config, progress=progress,
                image_model=selected_model,
                metadata_provider=metadata_provider,
                metadata_model=selected_metadata, usage_recorder=ledger.record,
                redact_error=redact,
            )
            try:
                for source, queue, ids, record in prepared:
                    if should_stop():
                        break
                    if record["completed_assets"] == len(ids):
                        continue
                    current_source = source.source_file
                    record.update(queue_status="RUNNING", started_at=record["started_at"] or now_iso(), last_error="")
                    state.save(source.run_id, source.identity, record)
                    log(f"Processing {current_source}", "Generation", source=current_source,
                        overview=overview(), total=report["total_assets"],
                        completed=sum(q["completed_assets"] for q in report["queues"]))
                    try:
                        def checkpoint(aid):
                            refresh(record, registry, ids)
                            record["queue_status"] = "RUNNING"
                            state.save(source.run_id, source.identity, record)
                            log(f"{aid}: {registry.get_asset(aid)['status']}", "Generation",
                                source=current_source, asset=aid, overview=overview(),
                                completed=sum(q["completed_assets"] for q in report["queues"]),
                                total=report["total_assets"])

                        pipeline.run_queue(queue, resume=True, skip_staging=True, should_stop=should_stop,
                                           asset_finished=checkpoint)
                        refresh(record, registry, ids)
                    except Exception as exc:
                        refresh(record, registry, ids)
                        record.update(queue_status="FAILED", last_error=redact(exc))
                        log(f"FAILED {current_source}: {record['last_error']}", "Generation")
                    state.save(source.run_id, source.identity, record)
            finally:
                ledger.finalize()

            for source, _, ids, record in prepared:
                for aid in ids:
                    row = registry.get_asset(aid)
                    if row["generated_at"] and row["generated_at"] != original_states[aid]["generated_at"]:
                        report["generated"] += 1
                    if row["status"] in DONE and row["status"] != "UPLOADED_DRAFT":
                        ready[aid] = {key: record[key] for key in (
                            "source_file", "source_commit_sha", "source_content_sha256",
                        )}
            report["failed_assets"] = sum(q["failed_assets"] for q in report["queues"])
            report["completed_assets"] = sum(q["completed_assets"] for q in report["queues"])
            report["prepared"] = len(ready)
            if should_stop():
                report.update(status="STOPPED", last_error="Stopped at asset boundary; no upload started")
                return report

            # Build even a partial local batch, but never contact Adobe until
            # every eligible queue and artifact passes the combined gate.
            batch = None
            if ready:
                log(f"Staging {len(ready)} files", "Staging", overview=overview())
                batch = build_combined_batch(root, registry, ready, config)
                pipeline.refresh_manifests(list(ready))
                report["batch_id"] = batch.name
                for source, _, ids, record in prepared:
                    if any(aid in ready for aid in ids):
                        record["batch_id"] = batch.name
                        state.save(source.run_id, source.identity, record)
            blockers = [q["source_file"] for q in report["queues"]
                        if q["queue_status"] not in {"READY_TO_UPLOAD", "UPLOADED_DRAFT"} | SKIPPED]
            if blockers:
                report.update(status="BLOCKED", last_error="Upload blocked: " + ", ".join(blockers))
                log(report["last_error"], "Quality gate")
                return report
            if not batch:
                report["status"] = "NO_WORK"
                log("No pending drafts. Nothing generated or uploaded again.", "Complete", overview=overview())
                return report

            receipts.mkdir(parents=True, exist_ok=True)
            recovered = reconcile_receipts(registry, batch, receipts)
            report["already_uploaded"] += len(recovered)
            outstanding = set(ready) - recovered
            upload_error = ""
            try:
                if outstanding and not should_stop():
                    for source, _, ids, record in prepared:
                        if outstanding.intersection(ids):
                            record["queue_status"] = "UPLOADING"
                            state.save(source.run_id, source.identity, record)
                    log(f"Reconciling and uploading {len(outstanding)} drafts", "Adobe", overview=overview())
                    uploader(root, batch, receipts, cdp=cdp, profile=profile, stop_file=stop_file,
                             log=lambda m: log(m, "Adobe"))
                elif outstanding:
                    report["status"] = "STOPPED"
                    return report
            except Exception as exc:
                upload_error = redact(exc)
            finally:
                confirmed = reconcile_receipts(registry, batch, receipts)
                report["uploaded"] = len(confirmed)
                pipeline.refresh_manifests(list(confirmed | recovered))
                for source, _, ids, record in prepared:
                    refresh(record, registry, ids)
                    if record["queue_status"] != "UPLOADED_DRAFT":
                        record["last_error"] = upload_error or "Waiting for verified Adobe save receipt"
                    state.save(source.run_id, source.identity, record)
            if outstanding - confirmed:
                report.update(status="STOPPED" if should_stop() else "UPLOAD_INCOMPLETE",
                              last_error=upload_error or "Missing saved draft receipts")
            else:
                report["status"] = "COMPLETE"
            return report
    except Exception as exc:
        report.update(status="FAILED", last_error=redact(exc))
        log(report["last_error"], "Error")
        return report
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - start, 2)
        report["completed_at"] = now_iso()
        write_report(report, run_dir)
        log(f"{report['status']} — {report['last_error'] or 'Report saved'}", "Finished",
            overview=overview(), completed=report["completed_assets"], total=report["total_assets"],
            report=report)
