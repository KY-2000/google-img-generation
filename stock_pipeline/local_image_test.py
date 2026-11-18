"""Run a local MiniMax H3 Image smoke test without touching production assets."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty, Queue
from threading import Thread

from .image_qc import inspect_image
from .manager_queues import discover
from .prompt_builder import render_image_prompt_file


STEPS = 6
STEP_LINE = re.compile(r"\|\s*(\d+)\s*/\s*(\d+)\s*-\s*[\d.]+\s*(?:s/it|it/s)")
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def preview_size(aspect_ratio: str) -> tuple[int, int]:
    return {"16:9": (2048, 1152), "9:16": (1152, 2048), "1:1": (2048, 2048)}[aspect_ratio]


def run_sd_cli(command, cwd, should_stop, on_line):
    """Stream CR-delimited sampling updates and terminate promptly on Stop."""
    with subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as child:
        lines = Queue()

        def read_output():
            pending = b""
            try:
                while chunk := child.stdout.read1(4096):
                    *complete, pending = re.split(br"[\r\n]+", pending + chunk)
                    for line in complete:
                        if line.strip():
                            lines.put(line.decode("utf-8", errors="replace"))
                if pending.strip():
                    lines.put(pending.decode("utf-8", errors="replace"))
            finally:
                lines.put(None)

        reader = Thread(target=read_output, daemon=True)
        reader.start()
        try:
            while not should_stop():
                try:
                    line = lines.get(timeout=0.2)
                except Empty:
                    continue
                if line is None:
                    while not should_stop():
                        try:
                            return child.wait(timeout=0.2)
                        except subprocess.TimeoutExpired:
                            continue
                    break
                on_line(line)
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
            reader.join()
    return None


def local_command(root, text_encoder=None):
    from main import load_env_file

    settings = {**load_env_file(root / ".env"), **os.environ}
    encoder_key = "LOCAL_H3_TEXT_ENCODER"
    if text_encoder is not None:
        encoder_key += f"_{text_encoder}"
    encoder = settings.get(encoder_key, "")
    if not encoder and text_encoder is not None:
        legacy_encoder = settings.get("LOCAL_H3_TEXT_ENCODER", "")
        if f"-{text_encoder}_" in Path(legacy_encoder).name.upper():
            encoder = legacy_encoder
    paths = {
        "LOCAL_SD_CLI": settings.get("LOCAL_SD_CLI") or shutil.which("sd-cli") or "",
        "LOCAL_H3_DIT": settings.get("LOCAL_H3_DIT", ""),
        encoder_key: encoder,
        "LOCAL_H3_VAE": settings.get("LOCAL_H3_VAE", ""),
    }
    resolved = {key: (root / value).resolve() for key, value in paths.items()}
    missing = [key for key, path in resolved.items() if not path.is_file()]
    if missing:
        raise ValueError("Local MiniMax H3 setup needs valid file paths in .env: " + ", ".join(missing))
    cli = resolved["LOCAL_SD_CLI"]
    command = [str(cli), "--diffusion-model", str(resolved["LOCAL_H3_DIT"]),
               "--llm", str(resolved[encoder_key]), "--vae", str(resolved["LOCAL_H3_VAE"]),
               "--steps", str(STEPS), "--cfg-scale", "1.0", "--sampling-method", "euler",
               "--sigmas", "minimax-h3-image", "--rng", "cpu", "--offload-to-cpu", "--vae-tiling",
               "--backend", "diffusion=cuda0,te=cpu,vae=cuda0", "--max-vram", "5", "--mmap", "--sage-attn",
               "--vae-tile-size", "1024x1024"]

    return cli, command


def run_local_image_test(root, queue_path, run_dir, limit, should_stop, log, *, text_encoder=None):
    incoming = (root / "queues" / "incoming").resolve()
    path = queue_path.resolve()
    if not path.is_relative_to(incoming):
        raise ValueError("Choose an existing JSON from queues/incoming for the local test.")
    sources = discover(root, datetime.now(timezone.utc).isoformat(), only=path.relative_to(root.resolve()).as_posix())
    if not sources or sources[0].example:
        raise ValueError("Choose a real image queue from queues/incoming.")
    source = sources[0]
    if source.error:
        raise ValueError(source.error)
    assets = (source.data if source.queue_type == "FINDER" else source.data["assets"])[:limit]

    cli, command = local_command(root, text_encoder)

    results = run_dir / "local_images"
    results.mkdir()
    gallery = {"queue": source.source_file, "images": []}
    report = {"status": "LOCAL_TEST_COMPLETE", "total_assets": len(assets), "generated": 0, "failed_assets": 0}
    for index, asset in enumerate(assets, 1):
        if should_stop():
            report["status"] = "STOPPED"
            break
        aid = asset.get("asset_id") or f"LOCAL-{index:02d}"
        aspect_ratio = asset.get("aspect_ratio", "1:1")
        width, height = preview_size(aspect_ratio)
        started = time.monotonic()
        step = 0
        out_of_memory = False

        def progress(stage, message):
            event = {"asset_id": aid, "stage": stage, "step": step, "steps": STEPS, "local": True,
                     "elapsed_seconds": time.monotonic() - started}
            if stage == "preparing":
                event.update(index=index, total=len(assets), theme=f"{asset['theme']} · {width}×{height}")
            log(f"[{aid}] {message}", asset_progress=event, total=len(assets), completed=report["generated"])

        def on_line(line):
            nonlocal step, out_of_memory
            line = ANSI_ESCAPE.sub("", line)
            if "out of memory" in line.lower():
                out_of_memory = True
            match = STEP_LINE.search(line)
            if match and int(match[2]) == STEPS and step < int(match[1]) <= STEPS:
                step = int(match[1])
                stage = "local_decoding" if step == STEPS else "local_sampling"
                progress(stage, f"Sampling step {step}/{STEPS}")
            else:
                log(f"[{aid}] {line.strip()}")

        progress("preparing", f"Preparing {width}×{height} preview ({aspect_ratio})")
        prompt = results / f"{index:03d}_prompt.txt"
        png = results / f"{index:03d}.png"
        try:
            prompt.write_text(render_image_prompt_file(root / "config/prompts/image_prompt_v1.txt", asset), encoding="utf-8")
            progress("local_loading", "Loading MiniMax H3 Image with a 5 GiB CUDA budget; text encoder on CPU, tiled VAE on CUDA")
            status = run_sd_cli([*command, "--prompt-file", str(prompt), "-W", str(width), "-H", str(height),
                                 "-o", str(png)], cli.parent, should_stop, on_line)
            if status is None:
                png.unlink(missing_ok=True)
                progress("local_stopped", "Local generation stopped")
                report["status"] = "STOPPED"
                break
            if status:
                if out_of_memory:
                    raise RuntimeError("Local GPU memory exhausted. Close GPU-heavy applications and retry. "
                                       "The local runner uses a 5 GiB GPU budget with the text encoder on CPU and tiled VAE on CUDA.")
                raise RuntimeError(f"sd-cli exited with code {status}. See Activity Log.")
            step = STEPS
            progress("qc", "Checking preview PNG")
            qc = inspect_image(aid, png, 1, aspect_ratio=aspect_ratio)
            if not qc["image_valid"] or qc.get("format") != "PNG" or (qc.get("width"), qc.get("height")) != (width, height):
                raise ValueError(f"Expected a valid {width}×{height} PNG")
            gallery["images"].append({"file": png.name, "theme": asset["theme"], "asset_id": aid})
            gallery_tmp = results / "gallery.json.tmp"
            gallery_tmp.write_text(json.dumps(gallery, ensure_ascii=False), encoding="utf-8")
            gallery_tmp.replace(results / "gallery.json")
            report["generated"] += 1
            progress("local_complete", f"Saved {png}")
        except (OSError, RuntimeError, ValueError) as exc:
            png.unlink(missing_ok=True)
            report["failed_assets"] += 1
            report["status"] = "LOCAL_TEST_FAILED"
            report["last_error"] = str(exc)
            progress("failed", str(exc))
            break
    report["output_dir"] = str(results)
    return report
