"""Stage Dreamstime JPEGs and metadata using FTP and the signed-in upload history."""
from __future__ import annotations

import csv
import json
import os
import subprocess
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from tools.prepare_dreamstime_csv import MIN_SR_PRICE_USD, dreamstime_ftp, export_jpeg, read_saved_rows

from .manager_runtime import ensure_edge_cdp

def _state_path(root: Path) -> Path:
    return root / "output" / "stock_manager" / "dreamstime_uploads.json"


def _read_state(root: Path) -> dict:
    path = _state_path(root)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"images": {}, "batches": {}}


def uploaded_assets(root: Path) -> set[str]:
    return {asset for batch in _read_state(root)["batches"].values() if batch["verified"] for asset in batch["assets"]}


def assets_with_sent_csv(root: Path) -> set[str]:
    return {asset for batch in _read_state(root)["batches"].values() for asset in batch["assets"]}


def _save_state(root: Path, state: dict) -> None:
    path = _state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False, mode="w", encoding="utf-8") as stream:
        temporary = Path(stream.name)
        json.dump(state, stream)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_history(root: Path, cdp: str, refresh: bool = False) -> dict[str, str]:
    command = ["node", str(root / "tools" / "dreamstime_upload_history.mjs"), cdp]
    if refresh:
        command.append("--refresh")
    result = subprocess.run(
        command,
        cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=60,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Could not read Dreamstime Upload History.")
    return json.loads(result.stdout)


def _check_history(root, cdp, filenames, expected, should_stop, progress, stage):
    deadline = time.monotonic() + 90
    refresh = False
    while not should_stop():
        history = read_history(root, cdp, refresh)
        unresolved = [name for name in sorted(filenames) if expected not in history.get(name, "").casefold()]
        progress(stage, f"{len(filenames) - len(unresolved)} / {len(filenames)} confirmed in Dreamstime Upload History")
        if not unresolved:
            return
        for name in unresolved:
            status = history.get(name, "")
            if status and not any(word in status.casefold() for word in ("pending", "processing", "queued")):
                raise RuntimeError(f"Dreamstime reports {name}: {status}")
        if time.monotonic() >= deadline:
            raise TimeoutError("Dreamstime is still processing: " + ", ".join(unresolved) + ". Retry to check again.")
        refresh = True
        for _ in range(10):
            if should_stop():
                break
            time.sleep(1)
    raise InterruptedError("Dreamstime upload stopped")


def upload_dreamstime(root, run_dir, assets, *, cdp, profile, should_stop, progress, log):
    """Verify both FTP phases without consuming or changing Adobe upload receipts."""
    state = _read_state(root)
    cdp = cdp or "http://127.0.0.1:9222"
    progress("dreamstime_history", "Checking Dreamstime Upload History")
    if not ensure_edge_cdp(root, cdp, profile, log=log, should_stop=should_stop, platform="Dreamstime"):
        raise InterruptedError("Dreamstime upload stopped")
    history = read_history(root, cdp)

    # Reconcile prior CSVs first. Sending them again on an uncertain response
    # could overwrite a draft that Dreamstime already populated.
    for filename, batch in state["batches"].items():
        if not batch["verified"] and assets.keys() & set(batch["assets"]):
            _check_history(root, cdp, {filename}, "processed as csv", should_stop, progress, "dreamstime_csv")
            batch["verified"] = True
            _save_state(root, state)

    pending = sorted(assets.keys() - uploaded_assets(root))
    if not pending:
        return
    run_dir.mkdir(parents=True, exist_ok=True)
    jpeg_names = {aid: f"{aid}.jpg" for aid in pending}
    unsent = []
    for aid in pending:
        filename = jpeg_names[aid]
        if aid in state["images"] or filename in history:
            continue
        jpeg = run_dir / filename
        export_jpeg(Path(assets[aid]["image_path"]), jpeg)
        unsent.append((aid, jpeg))
    if unsent:
        with dreamstime_ftp(root) as ftp:
            queued = {entry.rsplit("/", 1)[-1].casefold() for entry in ftp.nlst()}
            for index, (aid, jpeg) in enumerate(unsent, 1):
                if should_stop():
                    raise InterruptedError("Dreamstime upload stopped")
                state["images"][aid] = True
                _save_state(root, state)
                if jpeg.name.casefold() in queued:
                    continue
                progress("dreamstime_jpeg", f"JPEG {index} / {len(unsent)} · {jpeg.name}")
                with jpeg.open("rb") as image:
                    ftp.storbinary(f"STOR {jpeg.name}", image, blocksize=64 * 1024)
                log(f"Dreamstime FTP received {jpeg.name}")

    _check_history(root, cdp, set(jpeg_names.values()), "processed with image id",
                   should_stop, progress, "dreamstime_history")
    progress("dreamstime_metadata", f"Building CSV from saved Dreamstime metadata for {len(pending)} images")
    rows = read_saved_rows(pending, MIN_SR_PRICE_USD, root=root)
    file = run_dir / f"Dreamstime_{uuid4().hex[:12]}.csv"
    with file.open("x", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    progress("dreamstime_csv", file.name)
    if should_stop():
        raise InterruptedError("Dreamstime upload stopped")
    with dreamstime_ftp(root) as ftp, file.open("rb") as data:
        state["batches"][file.name] = {"assets": pending, "verified": False}
        _save_state(root, state)
        ftp.storbinary(f"STOR {file.name}", data)
    _check_history(root, cdp, {file.name}, "processed as csv", should_stop, progress, "dreamstime_csv")
    state["batches"][file.name]["verified"] = True
    _save_state(root, state)
