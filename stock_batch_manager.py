"""Windows desktop entry point. A fresh worker loads pipeline modules after Git sync."""
from __future__ import annotations

import argparse
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

from stock_pipeline.manager_runtime import (
    cdp_connected, git_sync, manager_lock, push_to_main, recent_error_log, redactor, validate_cdp,
    verify_reviewed_checkout,
)
from stock_pipeline.manager_baseline import TEST_QUEUE, snapshot_baseline

ROOT = Path(__file__).resolve().parent
TITLE = "Adobe Stock Batch Manager"
IMAGE_MODELS = {
    "Nano Banana Pro": "gemini-3-pro-image",
    "Nano Banana 2.1": "gemini-nano-banana-2.1",
}
DEFAULT_METADATA_MODEL = "gemini-3.8-flash"
METADATA_MODELS = (DEFAULT_METADATA_MODEL, "gemini-4-hydrogen")
SUPPORT_CHAT_FILE = Path("output/stock_manager/support_chat.txt")
CHAT_LINK_PREFIX = "codex://threads/"
GITHUB_COMMIT_PREFIX = "https://github.com/KY-2000/google-img-generation/commit/"

ASSET_STAGES = {
    "preparing": ("Preparing", 0, "#536879"),
    "image": ("Generating image", 0.5, "#194f70"),
    "qc": ("Checking image", 1.5, "#194f70"),
    "metadata": ("Generating metadata", 2.5, "#194f70"),
    "metadata_check": ("Checking metadata", 2.5, "#194f70"),
    "ready": ("Ready for draft upload", 3, "#21634d"),
    "simulated_ready": ("Simulation complete", 3, "#21634d"),
    "failed": ("Failed", None, "#a33232"),
    "deferred": ("Deferred", None, "#71571f"),
    "interrupted": ("Interrupted", None, "#71571f"),
    "local_loading": ("Loading local model", 0, "#194f70"),
    "local_sampling": ("Sampling", None, "#194f70"),
    "local_decoding": ("Decoding image", 2.5, "#194f70"),
    "local_complete": ("Saved locally", 3, "#21634d"),
    "local_stopped": ("Stopped", None, "#71571f"),
}
FINISHED_ASSET_STAGES = {"ready", "simulated_ready", "failed", "deferred", "interrupted", "local_complete", "local_stopped"}

UPLOAD_STAGES = {
    "waiting": "Waiting to upload",
    "browser": "Connecting to Adobe browser",
    "opening": "Opening Contributor uploads",
    "checking": "Checking existing drafts",
    "uploading": "Uploading image files",
    "importing": "Importing metadata CSV",
    "saving": "Saving and verifying draft",
    "draft_saved": "Draft verified",
    "finalizing": "Reconciling saved drafts",
    "complete": "Draft upload complete",
    "stopped": "Draft upload stopped",
    "incomplete": "Draft upload incomplete",
}


def simulate_asset_progress(max_workers, stop, log):
    themes = ("Botanical illustration", "Textured paper background", "Abstract geometric shapes")
    stages = ("preparing", "image", "qc", "metadata", "metadata_check", "simulated_ready")
    # Start each sample one step apart so the cards show different stages together.
    ticks = len(stages) + max_workers - 1
    generated = 0
    for tick in range(ticks):
        if stop.exists():
            return False
        for slot in range(max_workers):
            stage_index = tick - slot
            if not 0 <= stage_index < len(stages):
                continue
            aid = f"DEMO-{slot + 1:02d}"
            stage = stages[stage_index]
            progress = {"asset_id": aid, "stage": stage}
            if stage == "preparing":
                progress.update(index=slot + 1, total=max_workers, theme=themes[slot])
            generated += stage == "qc"
            log(f"[{aid}] {ASSET_STAGES[stage][0]}", asset_progress=progress,
                overview=dict(total=max_workers, pending=max_workers - generated, generated=generated,
                              uploaded=0, failed=0))
        if tick + 1 < ticks:
            time.sleep(0.8)
    return True


def simulate_upload_progress(total, stop, log):
    for stage, detail, done in (
        ("browser", "", 0), ("opening", "", 0), ("checking", "Page 1", 0),
        ("uploading", f"{total} file(s) in this batch", 0), ("importing", f"{total} file(s)", 0),
        *(("saving", f"DEMO-{index + 1:02d}.png", index) for index in range(total)),
        ("finalizing", "", total), ("complete", "", total),
    ):
        if stop.exists():
            return False
        log(f"[Simulation] {UPLOAD_STAGES[stage]}", overview=dict(total=total, pending=0, generated=total - done,
                                                                  uploaded=done, failed=0), upload_progress={
            "stage": stage, "detail": detail, "done": done, "total": total, "simulated": True,
        })
        if stage != "complete":
            time.sleep(0.6)
    return True


def reviewed_selection(selection, rows, lanes):
    requested = json.loads(selection)
    if type(requested) is int:
        # Older desktop windows send the number of rows from the same preview.
        pending = [row for row in rows if row["selectable"]]
        if not 1 <= requested <= len(pending):
            raise ValueError("Choose a valid number of queues and check the preview again.")
        requested = {lane: [row["source_file"] for row in pending[:requested]
                            if row["lanes"][lane]["selectable"]] for lane in lanes}
    if not isinstance(requested, dict) or set(requested) != set(lanes):
        raise ValueError("Choose JSON queues for the displayed production routes.")
    selected = {}
    for lane, paths in requested.items():
        allowed = {row["source_file"] for row in rows if row["lanes"][lane]["selectable"]}
        if not isinstance(paths, list) or any(not isinstance(path, str) or path not in allowed for path in paths):
            raise ValueError(f"Choose runnable JSON queues for {lane.title()} and check the preview again.")
        selected[lane] = set(paths)
    if not any(selected.values()):
        raise ValueError("Choose at least one runnable JSON queue.")
    return selected


def worker(run_dir: Path, cdp: str | None, profile: str | None, offline_test: bool = False,
           image_model: str | None = None, metadata_model: str | None = None, *,
           metadata_provider: str | None = None, temperature: float = 1.0,
           top_p: float = 0.95, max_images: int | None = None, max_workers: int = 1,
           local_queue: Path | None = None, generation_route: str = "cloud") -> int:
    # The UI generates a fresh local directory for each Start.
    home = (ROOT / "output" / "stock_manager" / "runs").resolve()
    run_dir = run_dir.resolve()
    if run_dir.parent != home or not run_dir.is_dir():
        raise ValueError("Invalid Batch Manager run directory")
    stop = run_dir / "stop.request"
    redact = redactor(ROOT)
    started = time.monotonic()
    sha = ""

    def emit(event):
        print(json.dumps(event, ensure_ascii=True), flush=True)

    def log(message, **fields):
        message = redact(message)
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        with (run_dir / "activity.log").open("a", encoding="utf-8") as out:
            out.write(f"{stamp} {message}\n")
        emit({"message": message, "time": stamp, **fields})

    try:
        if local_queue is not None:
            from stock_pipeline.batch_manager import write_report
            from stock_pipeline.local_image_test import run_local_image_test

            log("Running MiniMax H3 Image locally", phase="Local image test", source=local_queue.name,
                github="Not used", browser="Not used")
            report = run_local_image_test(ROOT, local_queue, run_dir, max_images or 1, stop.exists, log)
            report.update(log_path=str(run_dir / "activity.log"), elapsed_seconds=round(time.monotonic() - started, 2))
            write_report(report, run_dir)
            log(report.get("last_error") or report["status"].replace("_", " ").title(), report=report)
            return int(report["status"] == "LOCAL_TEST_FAILED")
        if offline_test:
            from stock_pipeline.batch_manager import validate_test_queue, write_report
            log("Validating the r02 queue. No generation or upload.", phase="Offline test",
                source=TEST_QUEUE, github="Not used", browser="Not used")
            report = validate_test_queue(ROOT, run_dir)
            log(f"Validated {report['total_assets']} assets. Simulating {max_workers} progress card(s).",
                phase="Offline layout test", source="Simulated assets")
            simulation_lanes = ("cloud", "local") if generation_route == "both" else (generation_route,)
            for lane in simulation_lanes:
                def simulation_log(message, **fields):
                    log(message, lane=lane, **fields)
                cards = 1 if lane == "local" else max_workers
                if not (simulate_asset_progress(cards, stop, simulation_log)
                        and simulate_upload_progress(cards, stop, simulation_log)):
                    report["status"] = "STOPPED"
                    break
            report["layout_test"] = {"cards": max_workers, "status": report["status"]}
            report["elapsed_seconds"] = round(time.monotonic() - started, 2)
            write_report(report, run_dir)
            outcome = "complete" if report["status"] == "TEST_PASSED" else "stopped"
            log(f"Layout test {outcome}. No generation or upload.", report=report)
            return 0
        if cdp:
            cdp = validate_cdp(cdp)
        with manager_lock(ROOT / "output" / "stock_manager" / "manager.lock"):
            log("Checking clean main and fetching GitHub…", phase="Git sync", github="Checking")
            changes = {}
            commit_state = {}
            sha, commit_time = git_sync(ROOT, incoming_changes=changes, commit_state=commit_state)
            log(f"Git synced: {sha}", phase="Git sync", github="Connected", commit=sha, **commit_state)
            from stock_pipeline.manager_lanes import preview_for_route, run_lanes
            rows, _ = preview_for_route(ROOT, commit_time, changes, generation_route)
            lanes = ("cloud", "local") if generation_route == "both" else (generation_route,)
            pending = [row["source_file"] for row in rows if row["selectable"]]
            log(f"{len(pending)} runnable queue(s) awaiting review", phase="Queue preview",
                preview={"queues": rows, "max_images": max_images, "lanes": lanes})
            selection = "" if stop.exists() else sys.stdin.readline().strip()
            if stop.exists() or not selection:
                log("Queue preview closed.", phase="Preview closed")
                return 0
            selected = reviewed_selection(selection, rows, lanes)
            counts = ", ".join(f"{lane.title()} {len(paths)} JSON" for lane, paths in selected.items())
            log(f"Confirmed {counts}. Verifying the preview before starting…", phase="Verifying queues")
            verify_reviewed_checkout(ROOT, sha)
            current_rows, sources = preview_for_route(ROOT, commit_time, changes, generation_route)
            if current_rows != rows:
                raise RuntimeError("Queues or local progress changed since preview. Check queues again.")
            if not cdp and selected.get("local"):
                cdp = "http://127.0.0.1:9222"
            if cdp:
                connected = cdp_connected(cdp)
                log("Edge CDP is reachable" if connected else "Edge is closed or unreachable; will try to start it for upload",
                    browser="Connected" if connected else "Disconnected")
            elif selected.get("cloud"):
                log("Adobe will use the persistent browser profile at the upload stage", browser="Profile mode")
            report = run_lanes(ROOT, sha, commit_time, run_dir, should_stop=stop.exists, emit=emit,
                                 cdp=cdp, profile=profile, stop_file=stop, image_model=image_model,
                                 metadata_model=metadata_model, metadata_provider=metadata_provider, temperature=temperature,
                                 top_p=top_p, max_images=max_images, max_workers=max_workers,
                                 sources=sources, route=generation_route, selected_by_lane=selected)
            from stock_pipeline.batch_manager import write_report
            write_report({**report, "log_path": str(run_dir / "activity.log")}, run_dir)
            log(report["status"].replace("_", " ").title(), report=report)
        return 0 if report["status"] in {"COMPLETE", "NO_WORK", "STOPPED", "LIMIT_REACHED"} else 1
    except Exception as exc:
        error = redact(exc)
        if isinstance(exc, ModuleNotFoundError):
            error += " — run .venv\\Scripts\\python.exe -m pip install -r requirements.txt"
        report = {"status": "FAILED", "git_commit": sha, "last_error": error, "queues": [],
                  "elapsed_seconds": round(time.monotonic() - started, 2),
                  "log_path": str(run_dir / "activity.log")}
        (run_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        (run_dir / "report.md").write_text(f"# Batch Manager\n\nStatus: FAILED\n\n{error}\n", encoding="utf-8")
        github = "Not used" if local_queue is not None else "Connected" if sha else "Disconnected"
        log(error, phase="Error", github=github, report=report)
        return 1


class Desktop:
    def __init__(self, window):
        import tkinter as tk
        from tkinter import ttk

        self.window, self.tk = window, tk
        self.events = queue.Queue()
        self.active = False
        self.pushing = False
        self.closing = False
        self.run_dir = None
        self.preview_window = None
        self.log_window = None
        self.log_text = None
        self.log_lines = deque(maxlen=2500)
        self.progress_panels = {}
        self.lane_overviews = {}
        self.lane_totals = {}
        self.local_run = False
        self.worker_input = None
        self.offline_test = tk.BooleanVar(value=True)
        self.local_test = tk.BooleanVar(value=False)
        self.generation_route = tk.StringVar(value="Cloud")
        self.image_model = tk.StringVar(value="Nano Banana Pro")
        self.cloud_image_model = self.image_model.get()
        self.metadata_model = tk.StringVar(value=DEFAULT_METADATA_MODEL)
        self.temperature = tk.StringVar(value="1.0")
        self.top_p = tk.StringVar(value="0.95")
        self.max_images = tk.StringVar()
        self.max_workers = tk.StringVar(value="1")
        self.endpoint = tk.StringVar(value=os.environ.get("ADOBE_CDP", "http://127.0.0.1:9222"))
        self.git_status = tk.StringVar(value="Not checked")
        self.browser_status = tk.StringVar(value="Not checked")
        self.commit = tk.StringVar(value="—")
        self.commit_sha = ""
        self.commit_on_github = False
        self.stage = tk.StringVar(value="Ready")
        self.current = tk.StringVar(value="No active queue")
        self.count = tk.StringVar(value="0 / 0 assets prepared")
        self.metrics = {key: tk.StringVar(value="—") for key in ("total", "pending", "generated", "uploaded", "failed")}
        window.title(TITLE)
        window.minsize(780, 755)
        window.geometry("1000x775")
        window.configure(bg="#edf0f3")
        window.protocol("WM_DELETE_WINDOW", self.close)

        style = ttk.Style(window)
        style.theme_use("clam")
        style.configure("TFrame", background="#edf0f3")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("TLabel", background="#edf0f3", foreground="#162230", font=("Segoe UI", 10))
        style.configure("Link.TLabel", foreground="#194f70", font=("Segoe UI", 10, "underline"))
        style.configure("Card.TLabel", background="#ffffff")
        style.configure("Hero.TLabel", font=("Segoe UI Semibold", 24))
        style.configure("Stage.TLabel", background="#ffffff", font=("Segoe UI Semibold", 15))
        style.configure("AssetTitle.TLabel", background="#ffffff", font=("Segoe UI Semibold", 12))
        style.configure("Metric.TLabel", background="#ffffff", font=("Segoe UI Semibold", 22))
        style.configure("TButton", font=("Segoe UI", 10), padding=(13, 7))
        style.configure("Start.TButton", background="#194f70", foreground="#ffffff",
                        font=("Segoe UI Semibold", 12), padding=(23, 10), borderwidth=0)
        style.map("Start.TButton", background=[("disabled", "#a4b3bd"), ("active", "#246489")],
                  foreground=[("disabled", "#ffffff")])
        style.configure("Horizontal.TProgressbar", troughcolor="#e3ebef", background="#217b7d")

        container = ttk.Frame(window, padding=(24, 14))
        container.pack(fill="both", expand=True)
        ttk.Label(container, text=TITLE, style="Hero.TLabel").pack(anchor="w", pady=(0, 4))
        links = ttk.Frame(container)
        links.pack(fill="x", pady=(0, 8))
        for label, var in (("GitHub", self.git_status), ("Adobe Browser", self.browser_status)):
            ttk.Label(links, text=label + ":").pack(side="left", padx=(0, 4))
            ttk.Label(links, textvariable=var).pack(side="left", padx=(0, 18))
        ttk.Label(links, text="Commit:").pack(side="left", padx=(0, 4))
        self.commit_link = ttk.Label(links, textvariable=self.commit)
        self.commit_link.pack(side="left")
        self.commit_link.bind("<Button-1>", lambda _event: self.open_commit())
        self.commit_link.bind("<Return>", lambda _event: self.open_commit())
        self.push_button = ttk.Button(links, text="Push to main", command=self.push_code)
        self.push_button.pack(side="right")
        stats = ttk.Frame(container)
        stats.pack(fill="x", pady=(0, 8))
        for index, key in enumerate(self.metrics):
            stats.columnconfigure(index, weight=1, uniform="stats")
            tile = ttk.Frame(stats, style="Card.TFrame", padding=(12, 5))
            tile.grid(row=0, column=index, sticky="nsew", padx=(0, 8 if index < 4 else 0))
            ttk.Label(tile, text=key.title(), style="Card.TLabel").pack(anchor="w")
            ttk.Label(tile, textvariable=self.metrics[key], style="Metric.TLabel").pack(anchor="w")

        progress = ttk.Frame(container, style="Card.TFrame", padding=(16, 8))
        progress.pack(fill="x", pady=(0, 8))
        progress_header = ttk.Frame(progress, style="Card.TFrame")
        progress_header.pack(fill="x")
        ttk.Label(progress_header, textvariable=self.stage, style="Stage.TLabel").pack(side="left")
        ttk.Label(progress_header, textvariable=self.count, style="Card.TLabel").pack(side="right")
        ttk.Label(progress, textvariable=self.current, style="Card.TLabel", wraplength=650).pack(anchor="w", pady=(1, 4))
        self.bar = ttk.Progressbar(progress, maximum=1, value=0)
        self.bar.pack(fill="x")

        controls = ttk.Frame(container)
        controls.pack(fill="x", pady=(0, 8))
        self.start_button = ttk.Button(controls, text="Run Offline Test", command=self.start, style="Start.TButton")
        self.start_button.pack(side="left", padx=(0, 8))
        self.stop_button = ttk.Button(controls, text="Stop After Active Assets", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=(0, 8))
        self.retry_button = ttk.Button(controls, text="Retry Failed", command=self.start, state="disabled")
        self.retry_button.pack(side="left")
        ttk.Button(controls, text="Open Logs", command=lambda: self.open_path(
            self.run_dir or ROOT / "output" / "stock_manager")).pack(side="right")
        ttk.Button(controls, text="Open Output Folder", command=self.open_output_folder).pack(
            side="right", padx=(0, 8))
        settings = ttk.Frame(container)
        settings.pack(fill="x", pady=(0, 8))
        ttk.Label(settings, text="Adobe CDP").pack(side="left", padx=(0, 8))
        self.endpoint_entry = ttk.Entry(settings, textvariable=self.endpoint, width=28)
        self.endpoint_entry.pack(side="left")
        ttk.Label(settings, text="Shared by Cloud and Local. Blank uses port 9222 when Local runs.").pack(side="left", padx=10)
        options = ttk.Frame(container)
        options.pack(fill="x", pady=(0, 8))
        self.image_model_label = ttk.Label(options, text="Image model")
        self.image_model_label.pack(side="left", padx=(0, 8))
        self.model_picker = ttk.Combobox(options, values=tuple(IMAGE_MODELS),
                                         textvariable=self.image_model, state="readonly", width=19)
        self.model_picker.pack(side="left", padx=(0, 16))
        ttk.Label(options, text="Metadata model").pack(side="left", padx=(0, 8))
        self.metadata_picker = ttk.Combobox(options, values=METADATA_MODELS,
                                            textvariable=self.metadata_model, width=23)
        self.metadata_picker.pack(side="left", padx=(0, 16))
        ttk.Label(options, text="Parallel assets").pack(side="left", padx=(0, 8))
        self.worker_picker = ttk.Combobox(options, values=("1", "2", "3"), textvariable=self.max_workers,
                                          width=3, state="readonly")
        self.worker_picker.pack(side="left")
        sampling = ttk.Frame(container)
        sampling.pack(fill="x", pady=(0, 8))
        self.generation_entries = []
        for label, value in (("Temperature", self.temperature), ("Top-p", self.top_p),
                             ("Max new images", self.max_images)):
            ttk.Label(sampling, text=label).pack(side="left", padx=(0, 6))
            entry = ttk.Entry(sampling, textvariable=value, width=7)
            entry.pack(side="left", padx=(0, 14))
            self.generation_entries.append(entry)
        self.image_limit_hint = ttk.Label(sampling, text="Blank = unlimited")
        self.image_limit_hint.pack(side="left")
        self.model_picker.bind("<<ComboboxSelected>>", lambda _event: self.refresh_generation_entries())
        modes = ttk.Frame(container)
        modes.pack(fill="x", pady=(0, 8))
        self.test_check = ttk.Checkbutton(modes, text="Offline test (simulation)", variable=self.offline_test,
                                         command=lambda: self.select_test_mode(self.offline_test, self.local_test))
        self.test_check.pack(side="left", padx=(0, 16))
        self.local_check = ttk.Checkbutton(modes, text="Local Image Test (MiniMax H3)", variable=self.local_test,
                                          command=lambda: self.select_test_mode(self.local_test, self.offline_test))
        self.local_check.pack(side="left")
        ttk.Label(modes, text="Production images model").pack(side="left", padx=(16, 6))
        self.route_picker = ttk.Combobox(modes, textvariable=self.generation_route, values=("Cloud", "Local", "Both"),
                                          state="readonly", width=8)
        self.route_picker.pack(side="left")
        self.route_picker.bind("<<ComboboxSelected>>", lambda _event: self.refresh_route())
        self.progress_tabs = ttk.Notebook(container)
        self.progress_tabs.pack(fill="x", pady=(0, 8))
        self.reset_asset_cards()
        self.max_workers.trace_add("write", lambda *_args: self.reset_asset_cards())
        log_controls = ttk.Frame(container)
        log_controls.pack(fill="x")
        ttk.Button(log_controls, text="Show Activity Log", command=self.show_activity_log).pack(side="left")
        ttk.Button(log_controls, text="Open ChatGPT + Copy Error Log",
                   command=self.open_error_in_chatgpt).pack(side="right")
        window.after(75, self.poll)

    def reset_asset_cards(self):
        from stock_pipeline.manager_progress import ProgressPanel

        for tab in self.progress_tabs.tabs():
            self.progress_tabs.forget(tab)
        for child in self.progress_tabs.winfo_children():
            child.destroy()
        self.progress_panels = {}
        route = self.generation_route.get()
        lanes = ("cloud", "local") if route == "Both" else ("local",) if route == "Local" else ("cloud",)
        if self.local_test.get():
            lanes = ("local",)
        from tkinter import ttk
        for lane in lanes:
            frame = ttk.Frame(self.progress_tabs)
            title = "Local · MiniMax H3 Image" if lane == "local" and len(lanes) == 2 else lane.title()
            self.progress_tabs.add(frame, text=title)
            count = 1 if lane == "local" else int(self.max_workers.get())
            self.progress_panels[lane] = ProgressPanel(frame, count, ASSET_STAGES, UPLOAD_STAGES, FINISHED_ASSET_STAGES)

    def show_activity_log(self):
        from tkinter import ttk

        if self.log_window is not None:
            self.log_window.lift()
            self.log_window.focus_set()
            return
        self.log_window = self.tk.Toplevel(self.window)
        self.log_window.title("Activity Log")
        self.log_window.geometry("940x440")
        self.log_window.protocol("WM_DELETE_WINDOW", self.close_activity_log)
        self.log_window.bind("<Escape>", lambda _event: self.close_activity_log())
        logs = ttk.Frame(self.log_window, padding=12)
        logs.pack(fill="both", expand=True)
        self.log_text = self.tk.Text(logs, wrap="word", background="#142331", foreground="#e6eff4",
                                     font=("Consolas", 10), relief="flat", padx=10, pady=8)
        scroll = ttk.Scrollbar(logs, command=self.log_text.yview)
        scroll.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        self.log_text.insert("end", "".join(self.log_lines))
        self.log_text.configure(state="disabled")
        self.log_text.see("end")

    def close_activity_log(self):
        self.log_window.destroy()
        self.log_window = self.log_text = None

    def refresh_start_button(self):
        test_label = "Run Offline Test" if self.offline_test.get() else "Check Queues"
        text = "Run Local Image Test" if self.local_test.get() else test_label
        self.start_button.configure(text=text)
        self.image_limit_hint.configure(text="Blank = 1 in local test" if self.local_test.get() else "Blank = unlimited")
        stop_text = "Stop Production" if self.generation_route.get() != "Cloud" else "Stop After Active Assets"
        self.stop_button.configure(text="Stop Local Test" if self.local_test.get() else stop_text)

    def refresh_route(self):
        if self.generation_route.get() == "Local":
            self.metadata_model.set(DEFAULT_METADATA_MODEL)
        self.refresh_start_button()
        self.refresh_generation_entries()
        self.reset_asset_cards()

    def select_test_mode(self, selected, other):
        if selected.get():
            other.set(False)
        self.refresh_start_button()
        self.refresh_generation_entries()
        self.reset_asset_cards()

    def open_output_folder(self):
        if self.local_run:
            results = self.run_dir / "local_images"
            self.open_path(results if results.is_dir() else self.run_dir)
        else:
            self.open_path(ROOT / "output")

    def set_commit(self, sha="", on_github=False):
        self.commit_sha = sha if re.fullmatch(r"[0-9a-f]{40}", sha) else ""
        self.commit_on_github = on_github
        if self.commit_sha:
            self.commit.set(self.commit_sha[:12] + ("" if on_github else " (local)"))
            self.commit_link.configure(style="Link.TLabel", cursor="hand2", takefocus=True)
        else:
            self.commit.set("—")
            self.commit_link.configure(style="TLabel", cursor="", takefocus=False)

    def open_commit(self):
        if not self.commit_sha:
            return
        try:
            if self.commit_on_github:
                os.startfile(GITHUB_COMMIT_PREFIX + self.commit_sha)
                return
            result = subprocess.run(
                ["git", "show", "--no-ext-diff", "--no-color", self.commit_sha, "--"], cwd=ROOT,
                stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode:
                raise RuntimeError("Git could not read this local commit.")
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            self.append(f"Could not open commit: {exc}")
            return
        from tkinter import ttk
        dialog = self.tk.Toplevel(self.window)
        dialog.title(f"Local commit {self.commit_sha[:12]}")
        dialog.geometry("1000x660")
        diff = self.tk.Text(dialog, wrap="word", font=("Consolas", 10), padx=12, pady=12)
        scrollbar = ttk.Scrollbar(dialog, command=diff.yview)
        scrollbar.pack(side="right", fill="y")
        diff.configure(yscrollcommand=scrollbar.set)
        diff.pack(side="left", fill="both", expand=True)
        diff.insert("1.0", result.stdout)
        diff.configure(state="disabled")

    def push_code(self):
        if self.active or self.pushing or self.closing:
            return
        self.pushing = True
        retry_enabled = self.retry_button.instate(["!disabled"])
        self.start_button.state(["disabled"])
        self.retry_button.state(["disabled"])
        self.push_button.configure(text="Pushing to main…", state="disabled")
        self.append("Syncing origin/main before pushing committed local changes…")

        def push():
            event = {"push_done": True, "retry_enabled": retry_enabled}
            try:
                with manager_lock(ROOT / "output" / "stock_manager" / "manager.lock"):
                    git_sync(ROOT)
                    sha = push_to_main(ROOT)
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                event["message"] = f"Push failed: {exc}"
            else:
                event.update(pushed_commit=sha, message=f"origin/main is at {sha[:12]}.")
            self.events.put(event)

        threading.Thread(target=push, daemon=True).start()

    def refresh_generation_entries(self):
        local = self.local_test.get() or self.generation_route.get() == "Local"
        if self.image_model.get() in IMAGE_MODELS:
            self.cloud_image_model = self.image_model.get()
        self.model_picker.configure(values=("MiniMax H3 Image",) if local else tuple(IMAGE_MODELS))
        self.image_model.set("MiniMax H3 Image" if local else self.cloud_image_model)
        self.image_model_label.configure(text="Cloud image model" if not local and self.generation_route.get() == "Both" else "Image model")
        self.model_picker.state(["disabled" if self.active else "!disabled"])
        pro = self.image_model.get() == "Nano Banana Pro"
        self.endpoint_entry.state(["disabled" if self.active or self.local_test.get() else "!disabled"])
        for widget in (self.metadata_picker, self.worker_picker):
            widget.state(["disabled" if self.active or local else "!disabled"])
        for index, entry in enumerate(self.generation_entries):
            entry.state(["disabled" if self.active or (index < 2 and (local or not pro)) else "!disabled"])

    def append(self, message, stamp=None):
        if stamp:
            stamp = datetime.fromisoformat(stamp).astimezone().strftime("%H:%M:%S")
        else:
            stamp = datetime.now().strftime("%H:%M:%S")
        line = f"{stamp}  {message}\n"
        self.log_lines.append(line)
        if self.log_text is not None:
            following = self.log_text.yview()[1] == 1.0
            self.log_text.configure(state="normal")
            self.log_text.insert("end", line)
            if int(self.log_text.index("end-1c").split(".")[0]) > 3000:
                self.log_text.delete("1.0", "501.0")
            if following:
                self.log_text.see("end")
            self.log_text.configure(state="disabled")

    def start(self):
        if self.active or self.pushing:
            return
        try:
            local = self.local_test.get()
            testing = self.offline_test.get() and not local
            live = not (testing or local)
            metadata_model = self.metadata_model.get().strip()
            if live and self.generation_route.get() != "Local" and not metadata_model:
                raise ValueError("Enter a metadata model ID.")
            generation_args = []
            if not testing:
                limit = self.max_images.get().strip()
                if limit:
                    try:
                        count = int(limit)
                    except ValueError:
                        count = 0
                    if count < 1:
                        raise ValueError("Max new images must be a positive whole number, or blank.")
                    generation_args.extend(("--max-images", str(count)))
                if live and self.generation_route.get() != "Local" and self.image_model.get() == "Nano Banana Pro":
                    for option, raw, maximum, label in (
                        ("--temperature", self.temperature.get(), 2, "Temperature"),
                        ("--top-p", self.top_p.get(), 1, "Top-p"),
                    ):
                        try:
                            value = float(raw)
                        except ValueError:
                            value = -1
                        if not 0 <= value <= maximum:
                            raise ValueError(f"{label} must be between 0 and {maximum}.")
                        generation_args.extend((option, str(value)))
            local_queue = ""
            if local:
                from tkinter import filedialog

                local_queue = filedialog.askopenfilename(parent=self.window, title="Choose a local image queue",
                                                         initialdir=ROOT / "queues" / "incoming",
                                                         filetypes=[("Queue JSON", "*.json")])
                if not local_queue:
                    return
            cdp = (validate_cdp(self.endpoint.get().strip())
                   if live and self.endpoint.get().strip() else None)
            self.run_dir = ROOT / "output" / "stock_manager" / "runs" / uuid4().hex
            self.run_dir.mkdir(parents=True)
        except (ValueError, OSError) as exc:
            self.append(str(exc))
            self.current.set(str(exc))
            return
        self.active = True
        self.local_run = local
        self.reset_asset_cards()
        self.lane_overviews.clear()
        self.lane_totals.clear()
        self.push_button.state(["disabled"])
        self.refresh_generation_entries()
        self.start_button.state(["disabled"])
        self.retry_button.state(["disabled"])
        self.stop_button.state(["!disabled"])
        self.test_check.state(["disabled"])
        self.local_check.state(["disabled"])
        self.route_picker.state(["disabled"])
        for metric in self.metrics.values():
            metric.set("—")
        self.stage.set("Starting")
        self.current.set(Path(local_queue).name if local else
                         "Preparing offline queue check" if testing else "Waiting for Git sync")
        self.count.set("0 / 0 assets prepared")
        self.bar.configure(value=0)
        self.git_status.set("Checking")
        self.browser_status.set("Not checked")
        self.set_commit()
        python = Path(sys.executable)
        if os.name == "nt" and python.name.casefold() == "pythonw.exe":
            python = python.with_name("python.exe")
        args = [str(python), str(Path(__file__).resolve()), "--worker", str(self.run_dir),
                "--max-workers", self.max_workers.get(), "--generation-route", self.generation_route.get().lower()]
        if local:
            args.extend(("--local-queue", local_queue, *generation_args))
        elif testing:
            args.append("--offline-test")
        else:
            if self.generation_route.get() != "Local":
                args.extend(("--image-model", IMAGE_MODELS[self.image_model.get()], "--metadata-model", metadata_model))
            args.extend(generation_args)
        if cdp:
            args.extend(("--cdp", cdp))
        if live:
            profile = os.environ.get("ADOBE_USER_DATA_DIR")
            if profile:
                args.extend(("--profile", profile))
        threading.Thread(target=self.read_worker, args=(args,), daemon=True).start()

    def read_worker(self, args):
        redact = redactor(ROOT)
        try:
            with subprocess.Popen(args, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, encoding="utf-8", errors="replace",
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as child:
                self.worker_input = child.stdin
                for line in child.stdout:
                    try:
                        event = json.loads(line)
                        if not isinstance(event, dict):
                            raise ValueError()
                    except ValueError:
                        # Unstructured dependency diagnostics can contain request
                        # material. Keep only redacted application events.
                        continue
                    self.events.put(event)
                self.events.put({"exit": child.wait()})
        except Exception as exc:
            self.events.put({"message": redact(exc), "exit": 1})
        finally:
            self.worker_input = None

    def show_preview(self, preview):
        from tkinter import ttk

        rows = preview["queues"]
        lanes = preview["lanes"]
        pending = [row for row in rows if row["selectable"]]
        eligible = {lane: {row["source_file"] for row in pending if row["lanes"][lane]["selectable"]} for lane in lanes}
        selected = {lane: set(paths) for lane, paths in eligible.items()}
        new = sum(row["git_change"] == "New" for row in rows)
        updated = sum(row["git_change"] == "Updated" for row in rows)
        invalid = sum(any(data["status"] == "Invalid" for data in row["lanes"].values()) for row in rows)
        runnable_images = sum(data["total"] for row in pending for data in row["lanes"].values() if data["selectable"])
        dialog = self.preview_window = self.tk.Toplevel(self.window)
        dialog.title("Queue Preview")
        dialog.geometry("1070x682")
        dialog.minsize(760, 537)
        dialog.transient(self.window)
        dialog.grab_set()
        panel = ttk.Frame(dialog, padding=16)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text=f"Pulled {new} new and {updated} updated JSON files  •  "
                             f"{len(pending)} runnable queues / {runnable_images} images"
                             f"  •  {invalid} invalid").pack(anchor="w", pady=(0, 10))
        table_box = ttk.Frame(panel)
        table_box.pack(fill="both", expand=True)
        columns = ("file", "themes", "git", *lanes, "total", "remaining")
        table = ttk.Treeview(table_box, show="headings", selectmode="browse", columns=columns)
        scroll = ttk.Scrollbar(table_box, orient="vertical", command=table.yview)
        table.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        table.pack(side="left", fill="both", expand=True)
        for key, title, width in (
            ("file", "JSON file", 265), ("themes", "Themes", 285), ("git", "Git", 60),
            *((lane, f"{lane.title()} status", 140) for lane in lanes), ("total", "Images", 65), ("remaining", "To generate", 95),
        ):
            table.heading(key, text=title)
            table.column(key, width=width, minwidth=55, stretch=key in {"file", "themes"})
        table.tag_configure("planned", background="#dcecf3")
        visible = [row for row in rows if row["selectable"] or row["git_change"] or row["error"]]
        for index, row in enumerate(visible):
            table.insert("", "end", iid=str(index), values=(
                row["source_file"].removeprefix("queues/incoming/"), ", ".join(row["themes"]),
                row["git_change"], *(row["lanes"][lane]["status"] for lane in lanes),
                sum(data["total"] for data in row["lanes"].values() if data["selectable"]) or "—",
                row["new_images"] if row["selectable"] else "—",
            ))
        detail_box = ttk.Frame(panel)
        detail_box.pack(fill="x", pady=(6, 6))
        detail = self.tk.Text(detail_box, height=9, wrap="word", font=("Segoe UI", 10), state="disabled")
        detail_scroll = ttk.Scrollbar(detail_box, orient="vertical", command=detail.yview)
        detail.configure(yscrollcommand=detail_scroll.set)
        detail_scroll.pack(side="right", fill="y")
        detail.pack(side="left", fill="x", expand=True)

        def show_details(_event=None):
            selection = table.selection()
            detail.configure(state="normal")
            detail.delete("1.0", "end")
            if selection:
                row = visible[int(selection[0])]
                lines = [row["source_file"]]
                lines.extend(f"{number}. {theme}" for number, theme in enumerate(row["themes"], start=1))
                if row["error"]:
                    lines.append(row["error"])
                detail.insert("end", "\n".join(lines))
            detail.yview_moveto(0)
            detail.configure(state="disabled")

        table.bind("<<TreeviewSelect>>", show_details)
        controls = ttk.Frame(panel)
        controls.pack(fill="x")
        summary = ttk.Label(panel)
        summary.pack(anchor="w", pady=(6, 0))

        def close_preview():
            self.stop()

        def confirm():
            if not any(selected.values()):
                return
            try:
                stream = self.worker_input
                if stream is None or stream.closed:
                    raise OSError("The worker is no longer waiting for confirmation.")
                stream.write(json.dumps({lane: sorted(paths) for lane, paths in selected.items()}) + "\n")
                stream.flush()
            except (OSError, ValueError) as exc:
                self.append(f"Could not start: {exc}")
                return
            self.dismiss_preview()
            self.stage.set("Starting selected queues")

        ttk.Button(controls, text="Cancel" if pending else "Close", command=close_preview).pack(side="right")
        start = ttk.Button(controls, text="Start Selected", command=confirm, style="Start.TButton")
        start.pack(side="right", padx=8)

        def update_selection():
            start.state(["!disabled" if any(selected.values()) else "disabled"])
            for index, row in enumerate(visible):
                path = row["source_file"]
                for lane in lanes:
                    status = row["lanes"][lane]["status"]
                    if path in eligible[lane]:
                        checked = "☑" if path in selected[lane] else "☐"
                        status = f"{checked}  {status}"
                    table.set(str(index), lane, status)
                table.item(str(index), tags=("planned",) if any(path in paths for paths in selected.values()) else ())
            totals = []
            for lane in lanes:
                chosen = [row["lanes"][lane] for row in pending if row["source_file"] in selected[lane]]
                new_images = sum(data["new_images"] for data in chosen)
                limit = preview["max_images"]
                if limit is not None:
                    new_images = min(new_images, limit)
                totals.append(f"{lane.title()}  {len(chosen)} JSON / {sum(data['total'] for data in chosen)} images ({new_images} new)")
            summary.configure(text="  •  ".join(totals))

        def select_lane(lane, all_files):
            selected[lane] = eligible[lane].copy() if all_files else set()
            update_selection()

        def toggle(lane, row_id):
            path = visible[int(row_id)]["source_file"]
            if path in eligible[lane]:
                selected[lane].symmetric_difference_update({path})
                update_selection()

        def click_status(event):
            row_id = table.identify_row(event.y)
            column = table.identify_column(event.x)
            if row_id and column:
                lane = columns[int(column[1:]) - 1]
                if lane in lanes:
                    toggle(lane, row_id)

        table.bind("<ButtonRelease-1>", click_status)
        for lane in lanes:
            for label, all_files in (("All", True), ("Clear", False)):
                ttk.Button(controls, text=f"{lane.title()} {label}", padding=(5, 4),
                           command=lambda lane=lane, all_files=all_files: select_lane(lane, all_files)).pack(side="left", padx=(0, 4))
            table.bind(f"<KeyPress-{lane[0]}>",
                       lambda _event, lane=lane: toggle(lane, table.focus()) if table.focus() else None)
        update_selection()
        dialog.protocol("WM_DELETE_WINDOW", close_preview)
        dialog.bind("<Escape>", lambda _event: close_preview())

    def dismiss_preview(self):
        if self.preview_window:
            self.preview_window.destroy()
            self.preview_window = None

    def poll(self):
        for _ in range(100):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event.get("message"):
                self.append(event["message"], event.get("time"))
            lane = event.get("lane", "local" if self.local_run or self.generation_route.get() == "Local" else "cloud")
            panel = self.progress_panels.get(lane)
            if event.get("asset_progress") and panel:
                panel.update_asset_card(event["asset_progress"])
            if event.get("upload_progress") and panel:
                panel.update_upload_panel(event["upload_progress"])
            if event.get("phase") and panel:
                panel.summary.configure(text=event["phase"])

            for field, var in (("github", self.git_status), ("browser", self.browser_status), ("phase", self.stage)):
                if event.get(field) and (field != "phase" or not event.get("asset")):
                    var.set("Production running" if field == "phase" and event.get("lane") and not self.offline_test.get()
                            else event[field])
            if event.get("commit"):
                self.set_commit(event["commit"], event.get("on_github", False))
            if event.get("push_done"):
                self.pushing = False
                self.push_button.configure(text="Push to main", state="normal")
                self.start_button.state(["!disabled"])
                if event.get("retry_enabled"):
                    self.retry_button.state(["!disabled"])
                pushed_commit = event.get("pushed_commit")
                if pushed_commit and (not self.active or self.commit_sha == pushed_commit):
                    self.set_commit(pushed_commit, True)
            if event.get("source"):
                self.current.set(event["source"])
            if "total" in event:
                done, total = event.get("completed", 0), event["total"]
                if event.get("lane"):
                    self.lane_totals[lane] = (done, total)
                    done = sum(value[0] for value in self.lane_totals.values())
                    total = sum(value[1] for value in self.lane_totals.values())
                self.bar.configure(maximum=max(total, 1), value=done)
                unit = "local previews saved" if self.local_run else "assets prepared"
                self.count.set(f"{done} / {total} {unit}")
            if event.get("overview"):
                self.lane_overviews[lane] = event["overview"]
                if panel:
                    panel.update_overview(event["overview"])
                for key, variable in self.metrics.items():
                    variable.set(str(sum(value.get(key, 0) for value in self.lane_overviews.values())))
            if event.get("report"):
                report = event["report"]
                if event.get("lane"):
                    if panel:
                        panel.finish(report["status"], report.get("last_error", ""))
                    continue
                self.stage.set(report["status"].replace("_", " ").title())
                if "lanes" not in report and panel:
                    panel.finish(report["status"], report.get("last_error", ""))
                if self.local_run:
                    saved, total = report.get("generated", 0), report.get("total_assets", 0)
                    self.count.set(f"{saved} / {total} local previews saved")
                    if report.get("last_error"):
                        self.current.set(report["last_error"])
                elif report["status"] == "TEST_PASSED":
                    self.bar.configure(maximum=report["total_assets"], value=report["total_assets"])
                    self.count.set(f"{report['total_assets']} assets validated; 0 generated")
                    self.current.set(TEST_QUEUE)
                else:
                    self.current.set(f"Generated {report.get('generated', 0)}  •  Failed {report.get('failed_assets', 0)}"
                                 f"  •  Drafts saved {report.get('uploaded', 0)}"
                                 f"  •  Already uploaded {report.get('already_uploaded', 0)}")
            if event.get("preview") and not (self.closing or (self.run_dir / "stop.request").exists()):
                self.show_preview(event["preview"])
            if "exit" in event:
                for panel in self.progress_panels.values():
                    for asset_id in list(panel.asset_slots):
                        panel.update_asset_card({"asset_id": asset_id, "stage": "interrupted"})
                self.dismiss_preview()
                self.active = False
                self.refresh_generation_entries()
                self.push_button.state(["!disabled"])
                self.start_button.state(["!disabled"])
                self.retry_button.state(["!disabled"] if event["exit"] else ["disabled"])
                self.stop_button.state(["disabled"])
                self.test_check.state(["!disabled"])
                self.local_check.state(["!disabled"])
                self.route_picker.state(["!disabled"])
                if event["exit"]:
                    self.append(f"Worker stopped (exit {event['exit']}). See Open Logs.")
            if self.closing and not (self.active or self.pushing):
                self.window.destroy()
                return
        for panel in self.progress_panels.values():
            panel.tick()
        self.window.after(75, self.poll)

    def stop(self):
        if self.active and self.run_dir:
            try:
                (self.run_dir / "stop.request").touch()
                stream = self.worker_input
                if stream is not None and not stream.closed:
                    stream.close()
            except OSError as exc:
                self.append(f"Could not request stop: {exc}")
                return
            self.dismiss_preview()
            self.stop_button.state(["disabled"])
            self.append("Stopping the local image test." if self.local_run else
                        "Stop requested. Waiting for the current operation to reach a safe boundary.")

    def close(self):
        if self.active or self.pushing:
            self.closing = True
            if self.active:
                self.stop()
        else:
            self.window.destroy()

    def open_path(self, path):
        try:
            path.mkdir(parents=True, exist_ok=True)
            os.startfile(str(path))
        except OSError as exc:
            self.append(str(exc))

    def open_error_in_chatgpt(self):
        from tkinter import TclError

        try:
            log = recent_error_log(ROOT, self.run_dir)
            if not log:
                self.append("No current activity or failed run log was found.")
                return
            self.window.clipboard_clear()
            self.window.clipboard_append(log)
            self.window.update_idletasks()
        except (OSError, ValueError, TclError) as exc:
            self.append(f"Could not copy the error log: {exc}")
            return
        try:
            link = (ROOT / SUPPORT_CHAT_FILE).read_text(encoding="utf-8-sig").strip()
            if not link.startswith(CHAT_LINK_PREFIX):
                raise ValueError()
            thread_id = UUID(link.removeprefix(CHAT_LINK_PREFIX))
            os.startfile(CHAT_LINK_PREFIX + str(thread_id))
        except (OSError, ValueError):
            self.append(f"Error log copied. To open the support chat automatically, check {SUPPORT_CHAT_FILE}.")
        else:
            self.append("Error log copied. Press Ctrl+V in the ChatGPT message box to review it.")


def main():
    parser = argparse.ArgumentParser(description=TITLE)
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--cdp", help=argparse.SUPPRESS)
    parser.add_argument("--profile", help=argparse.SUPPRESS)
    parser.add_argument("--offline-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--local-queue", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--generation-route", choices=("cloud", "local", "both"), default="cloud", help=argparse.SUPPRESS)
    parser.add_argument("--image-model", choices=tuple(IMAGE_MODELS.values()), help=argparse.SUPPRESS)
    parser.add_argument("--metadata-model", help=argparse.SUPPRESS)
    parser.add_argument("--metadata-provider", choices=("vertex", "gemini-developer"),
                        help=argparse.SUPPRESS)
    parser.add_argument("--temperature", type=float, default=1.0, help=argparse.SUPPRESS)
    parser.add_argument("--top-p", type=float, default=0.95, help=argparse.SUPPRESS)
    parser.add_argument("--max-images", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--max-workers", type=int, choices=(1, 2, 3), default=1, help=argparse.SUPPRESS)
    parser.add_argument("--snapshot-baseline", action="store_true",
                        help="Skip the current incoming JSON snapshot in live runs, excluding the r02 test queue.")
    args = parser.parse_args()
    if args.snapshot_baseline:
        print(f"Snapshotted {snapshot_baseline(ROOT)} files; excluded {TEST_QUEUE}")
        return 0
    if args.worker:
        return worker(args.worker, args.cdp, args.profile, args.offline_test, args.image_model, args.metadata_model,
                      metadata_provider=args.metadata_provider, temperature=args.temperature,
                      top_p=args.top_p, max_images=args.max_images, max_workers=args.max_workers,
                      local_queue=args.local_queue, generation_route=args.generation_route)
    import tkinter as tk
    window = tk.Tk()
    icon = ROOT / "config" / "stock_manager.ico"
    if os.name == "nt" and icon.is_file():
        window.iconbitmap(default=str(icon))
    Desktop(window)
    window.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
