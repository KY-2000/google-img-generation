"""Stage one immutable upload batch and reconcile individual Adobe receipts."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from uuid import uuid4

from .adobe_csv import build_adobe_csv, validate_csv_images
from .adobe_uploader import build_upload_command
from .metadata_validator import parse_categories, validate_metadata
from .registry import Registry, now_iso


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def build_combined_batch(root: Path, registry: Registry, sources: dict[str, dict], config: dict) -> Path:
    categories = parse_categories((root / "category-code.txt").read_text(encoding="utf-8"))
    metadata_config = config.get("metadata", {})
    folder = root / "output" / "adobe_batches"
    folder.mkdir(parents=True, exist_ok=True)
    batch_id = f"BATCH_COMBINED_{uuid4().hex}"
    temporary = folder / ("." + batch_id)
    temporary.mkdir()
    try:
        records, items, filenames = [], [], set()
        for aid, source in sorted(sources.items()):
            row = registry.get_asset(aid)
            if not row or row["status"] not in {"READY_TO_UPLOAD", "READY_TO_STAGE", "STAGED", "METADATA_READY"}:
                raise ValueError(f"{aid} is not prepared for upload")
            image = Path(row["image_path"])
            if not image.is_file() or image.stat().st_size == 0:
                raise ValueError(f"Missing prepared image for {aid}")
            filename = row["upload_filename"]
            if filename != aid + ".png" or filename.casefold() in filenames:
                raise ValueError(f"Invalid or duplicate upload filename for {aid}")
            filenames.add(filename.casefold())
            metadata = validate_metadata(
                json.loads((image.parent / "metadata.json").read_text(encoding="utf-8")), aid, categories,
                **{key: int(metadata_config[key]) for key in
                   ("max_title_chars", "min_expected_keywords", "hard_max_keywords") if key in metadata_config},
            )
            if (metadata["title"], metadata["keywords"], metadata["category_code"]) != (
                row["title"], json.loads(row["keywords_json"]), row["category_code"],
            ):
                raise ValueError(f"Metadata and registry disagree for {aid}")
            shutil.copy2(image, temporary / filename)
            records.append({"upload_filename": filename, "metadata": metadata})
            items.append({
                "asset_id": aid, "run_id": row["run_id"], **source, "filename": filename,
                "image_sha256": file_hash(temporary / filename), "title": metadata["title"],
                "keywords": ",".join(metadata["keywords"]), "category": str(metadata["category_code"]),
            })
        if not items:
            raise ValueError("Cannot stage an empty combined batch")
        csv_path = build_adobe_csv(records, temporary / "adobe_stock_metadata.csv")
        validate_csv_images(csv_path, temporary)
        (temporary / "batch_manifest.json").write_text(
            json.dumps({"batch_id": batch_id, "asset_ids": sorted(sources), "items": items}, indent=2), encoding="utf-8",
        )
        target = folder / batch_id
        temporary.rename(target)
    except Exception:
        # Preserve interrupted staging for diagnosis. Its dot-prefixed folder
        # cannot be passed to the managed uploader as a completed batch.
        raise

    # The complete combined batch exists before any registry state advances.
    with registry.connection:
        for item in items:
            registry.connection.execute(
                "UPDATE assets SET status='READY_TO_UPLOAD', staged_at=COALESCE(staged_at, ?) "
                "WHERE asset_id=? AND status IN ('READY_TO_STAGE','STAGED','METADATA_READY')",
                (now_iso(), item["asset_id"]),
            )
    return target


def reconcile_receipts(registry: Registry, batch: Path, receipt_dir: Path) -> set[str]:
    """Promote only assets with verified, persisted evidence matching the manifest."""
    manifest = json.loads((batch / "batch_manifest.json").read_text(encoding="utf-8"))
    confirmed = set()
    fields = ("filename", "image_sha256", "title", "keywords", "category")
    with registry.connection:
        for item in manifest["items"]:
            receipt_path = receipt_dir / (item["filename"] + ".saved.json")
            if not receipt_path.is_file():
                continue
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if receipt.get("status") != "saved_draft" or any(receipt.get(k) != item[k] for k in fields):
                raise ValueError(f"Adobe receipt does not match prepared file: {item['asset_id']}")
            row = registry.get_asset(item["asset_id"])
            if not row or row["status"] not in {"READY_TO_UPLOAD", "UPLOADED_DRAFT"}:
                raise ValueError(f"Unexpected registry state for receipt: {item['asset_id']}")
            if row["status"] == "READY_TO_UPLOAD":
                registry.connection.execute(
                    "UPDATE assets SET status='UPLOADED_DRAFT', uploaded_at=?, error=NULL WHERE asset_id=?",
                    (receipt.get("saved_at") or now_iso(), item["asset_id"]),
                )
                confirmed.add(item["asset_id"])
    return confirmed


def managed_upload_command(batch: Path, receipt_dir: Path, cdp: str | None, profile: str | None,
                           stop_file: Path | None = None) -> list[str]:
    command = build_upload_command(
        batch / "adobe_stock_metadata.csv", batch, cdp=cdp, user_data_dir=profile,
    ) + ["--managed-receipts", str(receipt_dir)]
    if stop_file:
        command += ["--managed-stop-file", str(stop_file)]
    return command
