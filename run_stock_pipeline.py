#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
import re
from datetime import datetime, timezone
from pathlib import Path

import main as legacy
from stock_pipeline.adobe_uploader import (
    mark_batch_uploaded_draft,
    upload_draft,
    validate_batch_ready_for_upload,
)
from stock_pipeline.ai_usage import UsageLedger
from stock_pipeline.billing_reconciler import BigQueryBillingReconciler
from stock_pipeline.candidate_conversion import convert_candidates, write_queue_atomic
from stock_pipeline.contracts import load_topic_queue
from stock_pipeline.logging import AssetLogger
from stock_pipeline.orchestrator import StockPipeline
from stock_pipeline.registry import Registry


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Stock Icon Pipeline V1.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--queue", type=Path, help="Validated production Topic Queue JSON.")
    source.add_argument(
        "--candidates", type=Path,
        help="Finder Candidate JSON array to validate, register, convert, and run.",
    )
    parser.add_argument("--database", type=Path, default=Path("data/stock_pipeline.sqlite"))
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--image-model")
    parser.add_argument("--metadata-provider", choices=("google", "vertex", "openrouter", "nim"))
    parser.add_argument("--metadata-model")
    parser.add_argument("--skip-image-generation", action="store_true")
    parser.add_argument("--skip-metadata", action="store_true")
    parser.add_argument("--skip-staging", action="store_true")
    parser.add_argument("--upload-draft", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Validate/register/build prompts without external calls.")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--force-image", action="store_true")
    parser.add_argument("--force-metadata", action="store_true")
    parser.add_argument("--cdp")
    parser.add_argument("--user-data-dir")
    parser.add_argument(
        "--upload-resume-after", type=int, default=0,
        help="Skip a verified prefix of images already uploaded to Adobe.",
    )
    parser.add_argument("--batch-id", help="Override the deterministic BATCH_<run> staging ID.")
    args = parser.parse_args(argv)
    if args.max_workers < 1:
        parser.error("--max-workers must be at least 1")
    if args.force_image and args.skip_image_generation:
        parser.error("--force-image cannot be combined with --skip-image-generation")
    if args.force_metadata and args.skip_metadata:
        parser.error("--force-metadata cannot be combined with --skip-metadata")
    if args.batch_id and not re.fullmatch(r"BATCH_[A-Za-z0-9_-]+", args.batch_id):
        parser.error("--batch-id must match ^BATCH_[A-Za-z0-9_-]+$")
    if args.upload_resume_after < 0:
        parser.error("--upload-resume-after must be nonnegative")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    project_root = Path(__file__).resolve().parent
    database_path = args.database if args.database.is_absolute() else project_root / args.database
    if args.candidates:
        candidates_path = args.candidates if args.candidates.is_absolute() else project_root / args.candidates
        queue = convert_candidates(candidates_path, database_path)
        generated_queue_path = (
            project_root / "queues" / "production"
            / f"topic_queue_{queue['run_id'].removeprefix('TF_').lower()}.json"
        )
        write_queue_atomic(queue, generated_queue_path)
        print(f"[pipeline] converted candidates to {generated_queue_path}", flush=True)
    else:
        queue_path = args.queue if args.queue.is_absolute() else project_root / args.queue
        queue = load_topic_queue(queue_path)
    config = json.loads((project_root / "config/stock_icon_pipeline.json").read_text(encoding="utf-8"))
    print(
        f"[pipeline] queue={queue['run_id']} assets={len(queue['assets'])} "
        f"resume={args.resume} upload_draft={args.upload_draft}",
        flush=True,
    )
    if args.max_workers > 1:
        print("Stock Icon Pipeline V1 serializes registry writes; max-workers is currently capped at 1.", file=sys.stderr)

    image_model = legacy.resolve_image_model_alias(args.image_model or legacy.get_default_image_model(project_root))
    metadata_provider = (args.metadata_provider or legacy.get_default_metadata_provider(project_root)).lower()
    metadata_model = legacy.resolve_metadata_model_alias(args.metadata_model or legacy.get_default_metadata_model(project_root))
    batch_id = args.batch_id or queue["run_id"].replace("TF_", "BATCH_", 1)
    command_id = f"{queue['run_id']}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}"
    billing_config = config.get("billing") or {}
    official_cost_lookup = None
    if billing_config.get("export_table") and billing_config.get("project_id"):
        official_cost_lookup = BigQueryBillingReconciler(
            table_path=billing_config["export_table"],
            project_id=billing_config["project_id"],
        ).lookup
    usage_ledger = UsageLedger(
        usage_dir=project_root / "output" / "usage",
        command_id=command_id,
        progress=lambda message: print(message, flush=True),
        official_cost_lookup=official_cost_lookup,
    )
    try:
        with Registry(database_path) as registry:
            pipeline = StockPipeline(
                project_root=project_root, registry=registry,
                image_model=image_model, metadata_provider=metadata_provider,
                metadata_model=metadata_model,
                minimum_resolution=config["image"]["minimum_resolution"],
                progress=lambda message: print(message, flush=True),
                usage_recorder=usage_ledger.record,
                config=config,
            )
            summary = pipeline.run_queue(
                queue, resume=args.resume, force_image=args.force_image,
                force_metadata=args.force_metadata,
                skip_image_generation=args.skip_image_generation or args.dry_run,
                skip_metadata=args.skip_metadata or args.dry_run,
                skip_staging=args.skip_staging or args.dry_run,
                batch_id=batch_id,
            )
            if args.upload_draft and summary["failed"]:
                print("[pipeline] Adobe upload skipped: assets failed processing/QC. "
                      "Review per-asset qc.json and registry errors; saved originals are preserved.", flush=True)
            elif args.upload_draft:
                if args.dry_run:
                    raise ValueError("--upload-draft cannot be combined with pipeline --dry-run")
                batch_dir = project_root / "output" / "adobe_batches" / batch_id
                validate_batch_ready_for_upload(registry, batch_dir)
                upload_started = time.perf_counter()
                upload_draft(
                    project_root, batch_dir, cdp=args.cdp,
                    user_data_dir=args.user_data_dir,
                    resume_after=args.upload_resume_after,
                )
                uploaded_ids = mark_batch_uploaded_draft(registry, batch_dir)
                upload_duration = round(time.perf_counter() - upload_started, 3)
                for asset_id in uploaded_ids:
                    asset = registry.get_asset(asset_id)
                    AssetLogger(Path(asset["image_path"]).parent).event(
                        "adobe_upload", batch_id=batch_id,
                        duration_seconds=upload_duration,
                    )
                pipeline.refresh_manifests(uploaded_ids)
    finally:
        usage_ledger.finalize()

    print(f"Total: {summary['total']}")
    print(f"Succeeded: {summary['succeeded']}")
    print(f"Failed: {summary['failed']}")
    print(f"Ready to Upload: {summary['ready_to_upload']}")
    print(f"Needs Review: {summary['needs_review']}")
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
