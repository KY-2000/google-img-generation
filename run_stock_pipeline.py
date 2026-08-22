#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
import re
from pathlib import Path

import main as legacy
from stock_pipeline.adobe_uploader import (
    mark_batch_uploaded_draft,
    upload_draft,
    validate_batch_ready_for_upload,
)
from stock_pipeline.contracts import load_topic_queue
from stock_pipeline.logging import AssetLogger
from stock_pipeline.orchestrator import StockPipeline
from stock_pipeline.registry import Registry


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Stock Icon Pipeline V1.")
    parser.add_argument("--queue", type=Path, required=True, help="Validated Topic Finder queue JSON.")
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
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    project_root = Path(__file__).resolve().parent
    queue_path = args.queue if args.queue.is_absolute() else project_root / args.queue
    database_path = args.database if args.database.is_absolute() else project_root / args.database
    queue = load_topic_queue(queue_path)
    config = json.loads((project_root / "config/stock_icon_pipeline.json").read_text(encoding="utf-8"))
    if args.max_workers > 1:
        print("Stock Icon Pipeline V1 serializes registry writes; max-workers is currently capped at 1.", file=sys.stderr)

    image_model = legacy.resolve_image_model_alias(args.image_model or legacy.get_default_image_model(project_root))
    metadata_provider = (args.metadata_provider or legacy.get_default_metadata_provider(project_root)).lower()
    metadata_model = legacy.resolve_metadata_model_alias(args.metadata_model or legacy.get_default_metadata_model(project_root))
    batch_id = args.batch_id or queue["run_id"].replace("TF_", "BATCH_", 1)
    with Registry(database_path) as registry:
        pipeline = StockPipeline(
            project_root=project_root, registry=registry,
            image_model=image_model, metadata_provider=metadata_provider,
            metadata_model=metadata_model,
            minimum_resolution=config["image"]["minimum_resolution"],
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
        if args.upload_draft:
            if args.dry_run:
                raise ValueError("--upload-draft cannot be combined with pipeline --dry-run")
            batch_dir = project_root / "output" / "adobe_batches" / batch_id
            validate_batch_ready_for_upload(registry, batch_dir)
            upload_started = time.perf_counter()
            upload_draft(
                project_root, batch_dir, cdp=args.cdp,
                user_data_dir=args.user_data_dir,
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

    print(f"Total: {summary['total']}")
    print(f"Succeeded: {summary['succeeded']}")
    print(f"Failed: {summary['failed']}")
    print(f"Ready to Upload: {summary['ready_to_upload']}")
    print(f"Needs Review: {summary['needs_review']}")
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
