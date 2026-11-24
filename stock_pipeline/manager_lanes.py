"""Independent generation lanes sharing the repository's production pipeline."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Lock
import time

from .batch_manager import preview_queues, run_manager
from .local_image_test import ANSI_ESCAPE, LOCAL_IMAGE_MODELS, STEP_LINE, STEPS, local_command, run_sd_cli
from .manager_queues import adopt_existing_finder_run, fingerprint, runtime_queue
from .manager_runtime import redactor
from .metadata_generator import NEMOTRON_VL_MODEL
from .orchestrator import StockPipeline
from .registry import Registry, now_iso


def preview_for_route(root, commit_time, changes, route):
    cloud, sources = preview_queues(root, commit_time, changes)
    if route == "cloud":
        return [{**row, "lanes": {"cloud": preview_lane(row)}} for row in cloud], sources
    local, _ = preview_queues(root, commit_time, changes, source_transform=local_sources)
    if route == "local":
        return [{**row, "lanes": {"local": preview_lane(row)}} for row in local], sources
    local_rows = {row["source_file"]: row for row in local}
    combined = []
    for row in cloud:
        peer = local_rows[row["source_file"]]
        combined.append({**row, "selectable": row["selectable"] or peer["selectable"],
                         "total": row["total"] + peer["total"],
                         "new_images": row["new_images"] + peer["new_images"],
                         "lanes": {"cloud": preview_lane(row), "local": preview_lane(peer)},
                         "error": row["error"] or peer["error"]})
    return combined, sources


def preview_lane(row):
    return {key: row[key] for key in ("status", "selectable", "total", "new_images")}


def local_sources(sources):
    result = []
    for source in sources:
        if source.error or source.example or source.baseline or source.duplicate:
            result.append(replace(source))
            continue
        if source.queue_type == "FINDER":
            assets = [{**{key: item[key] for key in (
                "domain", "theme", "source_type", "topic_score", "icon_1", "icon_2", "icon_3", "icon_4", "palette",
            )}, "topic_bucket": "APPROVED", "status": "APPROVED", "topic_finder_version": "TF_V1_1"}
                for item in source.data]
            data = dict(run_id=source.run_id, topic_finder_version="TF_V1_1", generated_at=source.order, assets=assets)
        else:
            data = deepcopy(source.data)
        data["run_id"] = "TF_LOCAL_" + fingerprint(source.run_id)[:24]
        for asset in data["assets"]:
            asset.pop("asset_id", None)
        result.append(replace(source, queue_type="PRODUCTION", run_id=data["run_id"],
                              identity=fingerprint(data), data=data))
    return result


class LocalStockPipeline(StockPipeline):
    def __init__(self, *, should_stop, text_encoder=None, image_quant="Q4", **kwargs):
        self.should_stop = should_stop
        self.text_encoder = text_encoder
        self.image_quant = image_quant
        self.local_started = 0
        self.local_step = 0
        kwargs.update(image_model=LOCAL_IMAGE_MODELS[image_quant], image_generate_bytes=self.generate_local)
        super().__init__(**kwargs)

    def process_asset(self, asset, **kwargs):
        self.local_aid = asset["asset_id"]
        self.local_step = 0
        self.local_started = time.monotonic()
        return super().process_asset(asset, **kwargs)

    def _emit_progress(self, asset_id, message, *, stage=None):
        if stage is not None and self.asset_stage is not None:
            self.asset_stage(asset_id, stage, local=True, step=self.local_step, steps=STEPS,
                             elapsed_seconds=time.monotonic() - self.local_started)
        super()._emit_progress(asset_id, message)

    def generate_local(self, *, prompt, aspect_ratio, resolution, **_kwargs):
        cli, command = local_command(self.project_root, self.text_encoder, self.image_quant)
        sizes = {"16:9": (4096, 2304), "9:16": (2304, 4096), "1:1": (4096, 4096)}
        width, height = sizes[aspect_ratio]
        if resolution != "4K":
            scale = {"512": 0.125, "1K": 0.25, "2K": 0.5}[resolution]
            width, height = int(width * scale), int(height * scale)
        oom = False

        def on_line(line):
            nonlocal oom
            line = ANSI_ESCAPE.sub("", line)
            oom |= "out of memory" in line.lower()
            match = STEP_LINE.search(line)
            stage = None
            if match and int(match[2]) == STEPS and self.local_step < int(match[1]) <= STEPS:
                self.local_step = int(match[1])
                stage = "local_decoding" if self.local_step == STEPS else "local_sampling"
            self._emit_progress(self.local_aid, line, stage=stage)

        folder = self._asset_dir(self.local_aid)
        with TemporaryDirectory(prefix="local-", dir=folder) as temporary:
            directory = Path(temporary)
            prompt_file, output = directory / "prompt.txt", directory / "image.png"
            prompt_file.write_text(prompt, encoding="utf-8")
            self._emit_progress(self.local_aid, "Loading local model", stage="local_loading")
            status = run_sd_cli([*command, "--prompt-file", str(prompt_file), "-W", str(width), "-H", str(height),
                                 "-o", str(output)], cli.parent, self.should_stop, on_line)
            if status is None:
                raise RuntimeError("Local image generation stopped")
            if status:
                reason = "CUDA memory exhausted" if oom else f"sd-cli exited with code {status}"
                raise RuntimeError(reason + ". See Activity Log.")
            return output.read_bytes()


def run_lanes(root, commit, commit_time, run_dir, sources, *, route, emit, should_stop, stop_file,
              cdp, profile, image_model, metadata_model, metadata_provider, temperature, top_p,
              max_images, max_workers, selected_by_lane=None, text_encoder=None, image_quant="Q4",
              image_thinking_level=None, manager=run_manager):
    lanes = ("cloud", "local") if route == "both" else (route,)
    plans = {lane: [source for source in sources
                    if selected_by_lane is None or source.source_file in selected_by_lane[lane]] for lane in lanes}
    if plans.get("local") and not cdp:
        cdp = "http://127.0.0.1:9222"
    if "local" in plans:
        plans["local"] = local_sources(plans["local"])
    # Allocate every lane's identifiers before concurrent registry writes begin.
    redact = redactor(root)
    with Registry(root / "data/stock_pipeline.sqlite") as registry:
        for planned in plans.values():
            for source in planned:
                if not (source.error or source.example or source.baseline or source.duplicate):
                    try:
                        adopt_existing_finder_run(registry, source)
                        runtime_queue(root, registry, source)
                    except Exception as exc:
                        source.error = redact(exc)
    event_lock = Lock()
    upload_lock = Lock()
    metadata_fallback = Event() if metadata_model == NEMOTRON_VL_MODEL else None

    @contextmanager
    def upload_slot(on_wait):
        acquired = upload_lock.acquire(blocking=False)
        try:
            if not acquired and not should_stop():
                on_wait()
                while not acquired and not should_stop():
                    acquired = upload_lock.acquire(timeout=0.2)
            yield acquired
        finally:
            if acquired:
                upload_lock.release()

    def send(lane, event):
        with event_lock:
            if event.get("message"):
                with (run_dir / "activity.log").open("a", encoding="utf-8") as stream:
                    stream.write(f"{event.get('time', now_iso())} [{lane}] {event['message']}\n")
            emit({**event, "lane": lane})

    def run(lane):
        directory = run_dir / lane
        directory.mkdir()
        options = dict(should_stop=should_stop, emit=lambda event: send(lane, event),
                       stop_file=stop_file, sources=plans[lane], max_images=max_images,
                       max_workers=max_workers if lane == "cloud" else 1,
                       temperature=temperature, top_p=top_p, cdp=cdp, profile=profile,
                       metadata_model=metadata_model, metadata_provider=metadata_provider)
        if route == "both":
            options["upload_slot"] = upload_slot
        if metadata_fallback is not None:
            options["metadata_fallback"] = metadata_fallback
        if lane == "local":
            options.update(pipeline_factory=lambda **kwargs: LocalStockPipeline(
                               should_stop=should_stop, text_encoder=text_encoder, image_quant=image_quant, **kwargs),
                           image_model=LOCAL_IMAGE_MODELS[image_quant], receipt_subdir="local")
        else:
            options.update(image_model=image_model)
            if image_thinking_level is not None:
                options["image_thinking_level"] = image_thinking_level
        try:
            return manager(root, commit, commit_time, directory, **options)
        except Exception as exc:
            report = {"status": "FAILED", "last_error": redact(exc)}
            send(lane, {"report": report})
            return report

    reports = {}
    for lane in lanes:
        if not plans[lane]:
            reports[lane] = {"status": "NO_WORK"}
            send(lane, {"report": reports[lane]})
    with ThreadPoolExecutor(max_workers=len(lanes)) as pool:
        pending = {pool.submit(run, lane): lane for lane in lanes if plans[lane]}
        for future in as_completed(pending):
            reports[pending[future]] = future.result()
    success = {"COMPLETE", "NO_WORK", "STOPPED", "LIMIT_REACHED"}
    status = "COMPLETE" if all(r["status"] in {"COMPLETE", "NO_WORK"} for r in reports.values()) else "STOPPED"
    if any(r["status"] not in success for r in reports.values()):
        status = "FAILED"
    from .batch_manager import COUNTERS
    return {**{key: sum(report.get(key, 0) for report in reports.values()) for key in COUNTERS},
            "status": status, "git_commit": commit, "lanes": reports, "last_error": " | ".join(
                f"{lane}: {report['last_error']}" for lane, report in reports.items() if report.get("last_error"))}
