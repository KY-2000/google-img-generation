"""Generate Dreamstime metadata or send a prepared CSV for already imported JPEGs."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from ftplib import FTP
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError

from PIL import Image, ImageCms

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import main as legacy
from stock_pipeline.dreamstime_rejections import build_dreamstime_row, resolve_local_asset
from stock_pipeline.manager_runtime import redactor
from stock_pipeline.metadata_generator import NEMOTRON_VL_MODEL, parse_metadata_json


SYSTEM_PROMPT = """Write English metadata for a Dreamstime commercial stock listing.
Use the final image as the primary evidence. Existing Adobe metadata is reference only.
Return one JSON object with exactly these keys: title, description, keywords, subject_category_codes.
Write a concise title that identifies the main subject and a useful visible detail.
Write one or two natural sentences describing the subject, composition, and distinctive
visible details. Add information beyond the title. The exporter adds the AI disclosure.
Return keywords as an array of relevant, distinct search terms, most relevant first.
Avoid padding, repeated variants, inferred brands, locations, events, and techniques.
Choose up to two subject category codes from the Dreamstime list supplied in the request.
Return them as integers ordered by relevance. Use fewer if there are no strong matches.
The exporter adds category 212 for AI-generated work."""
AI_DISCLOSURE = "This image was generated using AI."


def prepare_rows(asset_ids: list[str], price: int) -> list[dict]:
    category_lines = (ROOT / "config/dreamstime-categories.txt").read_text(encoding="utf-8-sig").splitlines()
    categories = {
        int(code): name
        for code, name in (line.split(" ", 1) for line in category_lines)
        if code != "212" and not name.startswith("Editorial ->")
    }
    legend = "\n".join(f"{code} {name}" for code, name in categories.items())
    nim = legacy.build_nim_metadata_client(legacy.get_nim_api_key(ROOT))
    vertex = None
    rows = []
    with TemporaryDirectory() as temporary:
        for asset_id in dict.fromkeys(asset_ids):
            local = resolve_local_asset(ROOT, asset_id)
            filename = f"{local.asset_id}.jpg"
            jpeg = Path(temporary) / filename
            with Image.open(local.image_path) as image:
                if image.width * image.height < 3_000_000:
                    raise ValueError(f"{filename} is below Dreamstime's 3 MP minimum")
                profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
                image.convert("RGB").save(jpeg, quality=97, subsampling=0, icc_profile=profile.tobytes())
            adobe = json.loads(local.metadata_path.read_text(encoding="utf-8"))
            user_prompt = (
                f"Adobe title: {local.title}\n"
                f"Adobe category: {adobe.get('category_name', '')}\n"
                f"Adobe keywords: {', '.join(local.keywords)}\n"
                f"Dreamstime subject category legend:\n{legend}"
            )
            prompt = user_prompt
            for attempt in range(2):
                if vertex is None:
                    try:
                        raw = legacy.generate_nim_metadata_text(
                            nim, jpeg, prompt, SYSTEM_PROMPT, NEMOTRON_VL_MODEL, 0.2, 0.9,
                        )
                    except HTTPError as exc:
                        if exc.code != 429:
                            raise
                        print("NVIDIA NIM returned HTTP 429. Using Gemini 3.8 Flash for remaining metadata.")
                        if legacy.get_google_auth_mode(ROOT) == "api_key":
                            vertex = legacy.build_google_client(api_key=legacy.get_google_api_key(ROOT))
                        else:
                            config = legacy.get_vertex_ai_config(ROOT)
                            vertex = legacy.build_google_client(project=config["project"], location=config["location"])
                if vertex is not None:
                    raw = legacy.generate_google_metadata_text(
                        vertex, local.image_path, prompt, SYSTEM_PROMPT, "gemini-3.8-flash", 0.2, 0.9,
                    )
                try:
                    row = make_row(filename, parse_metadata_json(raw), categories, price)
                except ValueError as exc:
                    if attempt:
                        raise ValueError(f"{filename}: {exc}") from exc
                    prompt = f"{user_prompt}\nRevise the response to address this validation error: {exc}"
                else:
                    rows.append(row)
                    print(f"{filename}: {row['Category 2']}, {row['Category 3']} · {row['Image Name']}")
                    break
    return rows


def make_row(filename: str, metadata: dict, categories: dict[int, str], price: int) -> dict:
    if set(metadata) != {"title", "description", "keywords", "subject_category_codes"}:
        raise ValueError("The JSON object must have exactly the requested four fields")
    title, description = metadata["title"], metadata["description"]
    if not all(isinstance(value, str) and value.strip() for value in (title, description)):
        raise ValueError("Title and description must be nonempty strings")
    keywords = metadata["keywords"]
    if not isinstance(keywords, list) or not keywords or not all(
        isinstance(value, str) and value.strip() and "," not in value for value in keywords
    ):
        raise ValueError("Keywords must be a nonempty array of individual terms")
    unique_keywords = {}
    for word in keywords:
        unique_keywords.setdefault(word.strip().casefold(), word.strip())
    codes = metadata["subject_category_codes"]
    if not isinstance(codes, list) or len(codes) > 2 or any(type(code) is not int or code not in categories for code in codes):
        raise ValueError("Subject category codes must contain up to two valid Dreamstime integers")
    codes = (list(dict.fromkeys(codes)) + [0, 0])[:2]
    row = build_dreamstime_row(filename=filename, title=title, keywords=list(unique_keywords.values()))
    description = " ".join(description.split())
    if AI_DISCLOSURE.casefold() not in description.casefold():
        description = f"{description.rstrip('. ')}. {AI_DISCLOSURE}"
    row.update({
        "Description": description,
        "Category 2": codes[0], "Category 3": codes[1],
        "SR-Price": price, "MR doc Ids": "", "Pr Docs": "",
    })
    return row


def upload_csv(file: Path) -> None:
    if file.suffix.lower() != ".csv" or any(char in file.name for char in "\r\n"):
        raise ValueError("Expected a CSV file")
    settings = legacy.load_env_file(ROOT / ".env")
    username = settings.get("DREAMSTIME_FTP_USER_ID") or os.getenv("DREAMSTIME_FTP_USER_ID")
    password = settings.get("DREAMSTIME_FTP_PASSWORD") or os.getenv("DREAMSTIME_FTP_PASSWORD")
    if not username or not username.isdecimal() or not password:
        raise ValueError("Set DREAMSTIME_FTP_USER_ID and DREAMSTIME_FTP_PASSWORD in .env")
    with file.open("rb") as data, FTP(timeout=45) as ftp:
        ftp.connect("upload.dreamstime.com", 21)
        ftp.login(username, password)
        ftp.set_pasv(True)
        ftp.cwd("/")
        if any(name.rsplit("/", 1)[-1].casefold() == file.name.casefold() for name in ftp.nlst()):
            raise ValueError("A CSV with this filename is already queued on Dreamstime")
        response = ftp.storbinary(f"STOR {file.name}", data)
        print(f"{file.name}: FTP {response[:3]}. Check Dreamstime Upload History for import status.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    prepare = subcommands.add_parser("prepare", help="Generate and validate metadata for saved assets")
    prepare.add_argument("asset_ids", nargs="+")
    prepare.add_argument("--sr-price", type=int, default=10)
    prepare.add_argument("--output", required=True, type=Path)
    upload = subcommands.add_parser("upload", help="Send CSV after all named JPEGs are in Unfinished Files")
    upload.add_argument("file", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "upload":
            upload_csv(args.file)
        else:
            if args.sr_price <= 0:
                raise ValueError("--sr-price must be positive")
            if args.output.exists():
                raise FileExistsError(f"Output already exists: {args.output}")
            rows = prepare_rows(args.asset_ids, args.sr_price)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8", newline="") as output:
                writer = csv.DictWriter(output, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            print(args.output)
        return 0
    except Exception as exc:
        print(redactor(ROOT)(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
