from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


CSV_HEADER = ["Filename", "Title", "Keywords", "Category", "Releases"]


def build_adobe_csv(records: list[dict[str, Any]], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_HEADER)
        writer.writeheader()
        for record in records:
            metadata = record["metadata"]
            writer.writerow({
                "Filename": record["upload_filename"],
                "Title": metadata["title"],
                "Keywords": ",".join(metadata["keywords"]),
                "Category": metadata["category_code"],
                "Releases": "",
            })
    return output_path


def read_adobe_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != CSV_HEADER:
            raise ValueError(f"Adobe CSV header must be exactly {CSV_HEADER}")
        return [dict(row) for row in reader]


def validate_csv_images(csv_path: Path, images_dir: Path) -> None:
    rows = read_adobe_csv(csv_path)
    expected = {row["Filename"] for row in rows}
    actual = {path.name for path in images_dir.iterdir() if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg"}}
    missing = expected - actual
    extra = actual - expected
    if missing:
        raise ValueError(f"CSV references missing images: {sorted(missing)}")
    if extra:
        raise ValueError(f"staging directory contains images absent from CSV: {sorted(extra)}")

