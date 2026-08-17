import argparse
import csv
import os
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import xhs_wallpaper_workflow as xhs
from xhs_wallpaper_workflow import run_xhs_workflow


CSV_HEADER = ["Filename", "Title", "Keywords", "Category", "Releases"]


@dataclass
class BatchConfig:
    source_root: Path
    project_root: Path
    prompt_provider: str
    prompt_model: str
    image_model: str
    metadata_provider: str
    metadata_model: str
    count: int
    aspect_ratio: str
    resolution: str
    temperature: float
    top_p: float
    overwrite: bool
    skip_adobe_upload: bool
    adobe_cdp: str | None
    adobe_user_data_dir: str | None
    adobe_file_type: str | None
    adobe_mark_ai: bool
    adobe_mark_fictional: bool
    adobe_save_work: bool
    adobe_dry_run: bool
    max_workers: int
    move_done: bool


@dataclass
class BatchResult:
    run_dirs: list[Path]
    upload_dir: Path
    done_dir: Path | None


@dataclass
class FolderResult:
    index: int
    total: int
    folder: Path
    run_dir: Path
    duration_seconds: float


_print_lock = threading.Lock()


def log(message: str) -> None:
    with _print_lock:
        print(message, flush=True)


def list_valid_xhs_folders(source_root: Path) -> list[Path]:
    if not source_root.exists():
        raise RuntimeError(f"Source root does not exist: {source_root}")
    if not source_root.is_dir():
        raise RuntimeError(f"Source root is not a folder: {source_root}")

    valid_folders: list[Path] = []
    for child in sorted(source_root.iterdir(), key=lambda item: item.name.lower()):
        if not child.is_dir():
            continue
        if not (child / "metadata.json").exists():
            continue
        has_image = any(
            item.is_file() and item.suffix.lower() in xhs.SUPPORTED_IMAGE_SUFFIXES
            for item in child.iterdir()
        )
        if has_image:
            valid_folders.append(child)
    return valid_folders


def slugify_folder_name(name: str, fallback: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return slug or fallback


def unique_prefix(folder_name: str, index: int, used_prefixes: set[str]) -> str:
    base = slugify_folder_name(folder_name, f"xhs-{index:03d}")
    candidate = base
    suffix = 2
    while candidate in used_prefixes:
        candidate = f"{base}-{suffix}"
        suffix += 1
    used_prefixes.add(candidate)
    return candidate


def unique_destination_path(destination_dir: Path, folder_name: str) -> Path:
    candidate = destination_dir / folder_name
    if not candidate.exists():
        return candidate
    suffix = 2
    while True:
        candidate = destination_dir / f"{folder_name}-{suffix}"
        if not candidate.exists():
            return candidate
        suffix += 1


def move_done_folders(folders: list[Path], done_dir: Path) -> None:
    done_dir.mkdir(parents=True, exist_ok=True)
    for folder in folders:
        destination = unique_destination_path(done_dir, folder.name)
        log(f"[BATCH] MOVE DONE {folder} -> {destination}")
        shutil.move(str(folder), str(destination))


def read_adobe_csv(csv_path: Path) -> list[dict[str, str]]:
    if not csv_path.exists():
        raise RuntimeError(f"Adobe CSV is missing: {csv_path}")
    with csv_path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames != CSV_HEADER:
            raise RuntimeError(
                f"Adobe CSV header must be exactly {','.join(CSV_HEADER)} in {csv_path}"
            )
        return [dict(row) for row in reader]


def prepare_combined_adobe_upload(run_dirs: list[Path], batch_dir: Path) -> Path:
    if not run_dirs:
        raise RuntimeError("No workflow output folders were produced.")

    upload_dir = batch_dir / "adobe_upload"
    if batch_dir.exists():
        shutil.rmtree(batch_dir)
    upload_dir.mkdir(parents=True)

    combined_rows: list[dict[str, str]] = []
    used_prefixes: set[str] = set()

    for index, run_dir in enumerate(run_dirs, start=1):
        prefix = unique_prefix(run_dir.name, index, used_prefixes)
        for row in read_adobe_csv(run_dir / "adobe_stock_metadata.csv"):
            source_name = row["Filename"]
            source_image = run_dir / source_name
            if not source_image.exists():
                raise RuntimeError(f"CSV references a missing image: {source_image}")

            target_name = f"{prefix}-{source_name}"
            shutil.copy2(source_image, upload_dir / target_name)
            rewritten = {key: row.get(key, "") for key in CSV_HEADER}
            rewritten["Filename"] = target_name
            combined_rows.append(rewritten)

    combined_csv = upload_dir / "adobe_stock_metadata.csv"
    with combined_csv.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_HEADER)
        writer.writeheader()
        writer.writerows(combined_rows)

    return upload_dir


def build_adobe_upload_command(
    *,
    csv_path: Path,
    images_dir: Path,
    config: BatchConfig,
) -> list[str]:
    npm_command = "npm.cmd" if os.name == "nt" else "npm"
    command = [
        npm_command,
        "run",
        "adobe-upload",
        "--",
        "--csv",
        str(csv_path),
        "--images",
        str(images_dir),
    ]
    if config.adobe_cdp:
        command.extend(["--cdp", config.adobe_cdp])
    if config.adobe_user_data_dir:
        command.extend(["--user-data-dir", config.adobe_user_data_dir])
    if config.adobe_file_type:
        command.extend(["--file-type", config.adobe_file_type])
    if config.adobe_mark_ai:
        command.append("--mark-ai")
    if config.adobe_mark_fictional:
        command.append("--mark-fictional")
    if config.adobe_save_work:
        command.append("--save-work")
    if config.adobe_dry_run:
        command.append("--dry-run")
    return command


def run_adobe_upload(*, csv_path: Path, images_dir: Path, config: BatchConfig) -> None:
    command = build_adobe_upload_command(csv_path=csv_path, images_dir=images_dir, config=config)
    print("[BATCH] Running Adobe upload command:")
    print("[BATCH] " + " ".join(command))
    subprocess.run(command, cwd=config.project_root, check=True)


def run_one_folder(
    *,
    index: int,
    total: int,
    folder: Path,
    config: BatchConfig,
) -> FolderResult:
    start = time.perf_counter()
    log(
        f"[BATCH] [{index}/{total}] START {folder.name} "
        f"workers={config.max_workers} count={config.count} "
        f"aspect={config.aspect_ratio} resolution={config.resolution}"
    )
    run_dir = run_xhs_workflow(
        target_dir=folder,
        project_root=config.project_root,
        prompt_provider=config.prompt_provider,
        prompt_model=config.prompt_model,
        image_model=config.image_model,
        metadata_provider=config.metadata_provider,
        metadata_model=config.metadata_model,
        count=config.count,
        aspect_ratio=config.aspect_ratio,
        resolution=config.resolution,
        temperature=config.temperature,
        top_p=config.top_p,
        overwrite=config.overwrite,
    )
    duration = time.perf_counter() - start
    log(f"[BATCH] [{index}/{total}] DONE {folder.name} duration={duration:.1f}s output={run_dir}")
    return FolderResult(
        index=index,
        total=total,
        folder=folder,
        run_dir=run_dir,
        duration_seconds=duration,
    )


def run_batch(config: BatchConfig) -> BatchResult:
    folders = list_valid_xhs_folders(config.source_root)
    if not folders:
        raise RuntimeError(f"No valid XHS folders found under: {config.source_root}")

    if config.max_workers < 1:
        raise RuntimeError("--max-workers must be at least 1")

    started_at = time.perf_counter()
    effective_workers = min(config.max_workers, len(folders))
    log(
        f"[BATCH] Found {len(folders)} valid XHS folders under {config.source_root}. "
        f"Running with max_workers={effective_workers}."
    )

    results: list[FolderResult] = []
    failures: list[tuple[Path, BaseException]] = []
    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        futures = {
            executor.submit(
                run_one_folder,
                index=index,
                total=len(folders),
                folder=folder,
                config=config,
            ): folder
            for index, folder in enumerate(folders, start=1)
        }
        completed = 0
        for future in as_completed(futures):
            folder = futures[future]
            completed += 1
            try:
                result = future.result()
                results.append(result)
                log(
                    f"[BATCH] PROGRESS {completed}/{len(folders)} completed; "
                    f"latest_success={folder.name}"
                )
            except BaseException as exc:
                failures.append((folder, exc))
                log(
                    f"[BATCH] PROGRESS {completed}/{len(folders)} completed; "
                    f"latest_failed={folder.name} error={exc}"
                )

    if failures:
        failure_text = "; ".join(f"{folder}: {exc}" for folder, exc in failures)
        raise RuntimeError(
            "One or more XHS child folders failed. Adobe upload was not started. "
            f"Failures: {failure_text}"
        )

    ordered_results = sorted(results, key=lambda result: result.index)
    run_dirs = [result.run_dir for result in ordered_results]

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    batch_name = f"_batch_{slugify_folder_name(config.source_root.name, 'xhs')}_{timestamp}"
    upload_dir = prepare_combined_adobe_upload(
        run_dirs=run_dirs,
        batch_dir=config.project_root / "output" / batch_name,
    )
    log(f"[BATCH] Prepared combined Adobe upload folder: {upload_dir}")

    done_dir = None
    if config.move_done:
        done_dir = config.source_root / f"DONE_{timestamp}"
        move_done_folders([result.folder for result in ordered_results], done_dir)

    if not config.skip_adobe_upload:
        run_adobe_upload(
            csv_path=upload_dir / "adobe_stock_metadata.csv",
            images_dir=upload_dir,
            config=config,
        )
    else:
        log("[BATCH] Skipped Adobe upload because --skip-adobe-upload was passed.")

    total_duration = time.perf_counter() - started_at
    log(
        f"[BATCH] Finished batch folders={len(folders)} "
        f"duration={total_duration:.1f}s upload_dir={upload_dir}"
    )
    return BatchResult(run_dirs=run_dirs, upload_dir=upload_dir, done_dir=done_dir)


def parse_args(argv: list[str] | None = None, project_root: Path | None = None) -> BatchConfig:
    resolved_project_root = project_root or Path(__file__).resolve().parent
    generation_defaults = xhs.get_generation_defaults(resolved_project_root)

    parser = argparse.ArgumentParser(
        description=(
            "Run xhs_wallpaper_workflow.py for every valid direct child folder, "
            "then upload all generated images and one combined Adobe CSV."
        )
    )
    parser.add_argument("source_root", help="Folder containing multiple XHS note folders.")
    parser.add_argument("--prompt-provider", choices=xhs.PROVIDER_CHOICES, default=xhs.get_default_prompt_provider(resolved_project_root))
    parser.add_argument("--prompt-model", default=xhs.get_default_prompt_model(resolved_project_root))
    parser.add_argument("--metadata-provider", choices=xhs.PROVIDER_CHOICES, default=xhs.get_default_metadata_provider(resolved_project_root))
    parser.add_argument("--metadata-model", default=xhs.resolve_metadata_model_alias(xhs.get_env_value(resolved_project_root, "METADATA_MODEL", xhs.DEFAULT_METADATA_MODEL)))
    parser.add_argument("--model", default=xhs.resolve_image_model_alias(xhs.get_env_value(resolved_project_root, "IMAGE_MODEL", xhs.DEFAULT_MODEL)))
    parser.add_argument("--count", type=int, default=generation_defaults["count"])
    parser.add_argument("--aspect-ratio", choices=xhs.VALID_ASPECT_RATIOS, default=generation_defaults["aspect_ratio"])
    parser.add_argument("--resolution", choices=xhs.VALID_RESOLUTIONS, default=generation_defaults["resolution"])
    parser.add_argument("--temperature", type=float, default=xhs.DEFAULT_TEMPERATURE)
    parser.add_argument("--top-p", type=float, default=xhs.DEFAULT_TOP_P)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--skip-adobe-upload", action="store_true")
    parser.add_argument("--adobe-cdp")
    parser.add_argument("--adobe-user-data-dir")
    parser.add_argument("--adobe-file-type", choices=("photos", "illustrations"), default="illustrations")
    parser.add_argument("--no-adobe-mark-ai", action="store_true")
    parser.add_argument("--adobe-mark-fictional", action="store_true")
    parser.add_argument("--no-adobe-save-work", action="store_true")
    parser.add_argument("--adobe-dry-run", action="store_true")
    parser.add_argument(
        "--max-workers",
        type=int,
        default=1,
        help="Number of child folders to process in parallel. Keep low to avoid Vertex AI 429 quota errors.",
    )
    parser.add_argument(
        "--no-move-done",
        action="store_true",
        help="Do not move successfully processed child folders into DONE_<timestamp>.",
    )
    args = parser.parse_args(argv)

    return BatchConfig(
        source_root=Path(args.source_root).resolve(),
        project_root=resolved_project_root,
        prompt_provider=xhs.normalize_provider_name(args.prompt_provider),
        prompt_model=xhs.METADATA_MODEL_ALIASES.get(args.prompt_model.lower(), args.prompt_model),
        image_model=xhs.resolve_image_model_alias(args.model),
        metadata_provider=xhs.normalize_provider_name(args.metadata_provider),
        metadata_model=xhs.resolve_metadata_model_alias(args.metadata_model),
        count=args.count,
        aspect_ratio=args.aspect_ratio,
        resolution=args.resolution,
        temperature=args.temperature,
        top_p=args.top_p,
        overwrite=args.overwrite,
        skip_adobe_upload=args.skip_adobe_upload,
        adobe_cdp=args.adobe_cdp,
        adobe_user_data_dir=args.adobe_user_data_dir,
        adobe_file_type=args.adobe_file_type,
        adobe_mark_ai=not args.no_adobe_mark_ai,
        adobe_mark_fictional=args.adobe_mark_fictional,
        adobe_save_work=not args.no_adobe_save_work,
        adobe_dry_run=args.adobe_dry_run,
        max_workers=args.max_workers,
        move_done=not args.no_move_done,
    )


def main() -> int:
    config = parse_args(project_root=Path(__file__).resolve().parent)
    result = run_batch(config)
    print(f"[BATCH] Completed {len(result.run_dirs)} workflows.")
    print(f"[BATCH] Combined upload folder: {result.upload_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
