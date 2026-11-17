"""Windows desktop entry point. A fresh worker loads pipeline modules after Git sync."""
from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from stock_pipeline.manager_runtime import cdp_connected, git_sync, manager_lock, redactor, validate_cdp
from stock_pipeline.manager_baseline import TEST_QUEUE, snapshot_baseline

ROOT = Path(__file__).resolve().parent
TITLE = "Adobe Stock Batch Manager"
IMAGE_MODELS = {
    "Nano Banana Pro": "gemini-3-pro-image",
    "Nano Banana 2.1": "gemini-nano-banana-2.1",
}


def worker(run_dir: Path, cdp: str | None, profile: str | None, offline_test: bool = False,
           image_model: str | None = None) -> int:
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
        if offline_test:
            from stock_pipeline.batch_manager import validate_test_queue
            log("Validating only the r02 queue. No generation or upload.", phase="Offline test",
                source=TEST_QUEUE, github="Not used", browser="Not used")
            report = validate_test_queue(ROOT, run_dir)
            log(f"Validated {report['total_assets']} assets. No generation or upload.",
                phase="Offline test", report=report)
            return 0
        if cdp:
            cdp = validate_cdp(cdp)
        with manager_lock(ROOT / "output" / "stock_manager" / "manager.lock"):
            log("Checking clean main and fetching GitHub…", phase="Git sync", github="Checking")
            sha, commit_time = git_sync(ROOT)
            log(f"Git synced: {sha}", phase="Git sync", github="Connected", commit=sha)
            if cdp:
                connected = cdp_connected(cdp)
                log("Edge CDP is reachable" if connected else "Edge CDP is not reachable; upload will need Edge",
                    browser="Connected" if connected else "Disconnected")
            else:
                log("Adobe will use the persistent browser profile at the upload stage", browser="Profile mode")
            from stock_pipeline.batch_manager import run_manager
            report = run_manager(ROOT, sha, commit_time, run_dir, should_stop=stop.exists, emit=emit,
                                 cdp=cdp, profile=profile, stop_file=stop, image_model=image_model)
        return 0 if report["status"] in {"COMPLETE", "NO_WORK", "STOPPED"} else 1
    except Exception as exc:
        error = redact(exc)
        if isinstance(exc, ModuleNotFoundError):
            error += " — run .venv\\Scripts\\python.exe -m pip install -r requirements.txt"
        report = {"status": "FAILED", "git_commit": sha, "last_error": error, "queues": [],
                  "elapsed_seconds": round(time.monotonic() - started, 2),
                  "log_path": str(run_dir / "activity.log")}
        (run_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        (run_dir / "report.md").write_text(f"# Batch Manager\n\nStatus: FAILED\n\n{error}\n", encoding="utf-8")
        log(error, phase="Error", github="Connected" if sha else "Disconnected", report=report)
        return 1


class Desktop:
    def __init__(self, window):
        import tkinter as tk
        from tkinter import ttk

        self.window, self.tk = window, tk
        self.events = queue.Queue()
        self.active = False
        self.closing = False
        self.run_dir = None
        self.offline_test = tk.BooleanVar(value=True)
        self.image_model = tk.StringVar(value="Nano Banana Pro")
        self.endpoint = tk.StringVar(value=os.environ.get("ADOBE_CDP", "http://127.0.0.1:9222"))
        self.git_status = tk.StringVar(value="Not checked")
        self.browser_status = tk.StringVar(value="Not checked")
        self.commit = tk.StringVar(value="—")
        self.stage = tk.StringVar(value="Ready")
        self.current = tk.StringVar(value="No active queue")
        self.count = tk.StringVar(value="0 / 0 assets prepared")
        self.metrics = {key: tk.StringVar(value="—") for key in ("total", "pending", "running", "completed", "failed")}
        window.title(TITLE)
        window.geometry("1000x740")
        window.minsize(740, 610)
        window.configure(bg="#edf0f3")
        window.protocol("WM_DELETE_WINDOW", self.close)

        style = ttk.Style(window)
        style.theme_use("clam")
        style.configure("TFrame", background="#edf0f3")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("TLabel", background="#edf0f3", foreground="#162230", font=("Segoe UI", 10))
        style.configure("Card.TLabel", background="#ffffff")
        style.configure("Hero.TLabel", font=("Segoe UI Semibold", 24))
        style.configure("Stage.TLabel", background="#ffffff", font=("Segoe UI Semibold", 15))
        style.configure("Metric.TLabel", background="#ffffff", font=("Segoe UI Semibold", 22))
        style.configure("TButton", font=("Segoe UI", 10), padding=(13, 7))
        style.configure("Start.TButton", background="#194f70", foreground="#ffffff",
                        font=("Segoe UI Semibold", 12), padding=(23, 10), borderwidth=0)
        style.map("Start.TButton", background=[("disabled", "#a4b3bd"), ("active", "#246489")],
                  foreground=[("disabled", "#ffffff")])
        style.configure("Horizontal.TProgressbar", troughcolor="#e3ebef", background="#217b7d")

        container = ttk.Frame(window, padding=24)
        container.pack(fill="both", expand=True)
        ttk.Label(container, text="PRODUCTION  /  DRAFT UPLOAD").pack(anchor="w")
        ttk.Label(container, text=TITLE, style="Hero.TLabel").pack(anchor="w", pady=(3, 8))
        links = ttk.Frame(container)
        links.pack(fill="x", pady=(0, 12))
        for label, var in (("GitHub", self.git_status), ("Adobe Browser", self.browser_status), ("Commit", self.commit)):
            ttk.Label(links, text=label + ":").pack(side="left", padx=(0, 4))
            ttk.Label(links, textvariable=var).pack(side="left", padx=(0, 18))
        stats = ttk.Frame(container)
        stats.pack(fill="x", pady=(0, 12))
        for index, key in enumerate(self.metrics):
            stats.columnconfigure(index, weight=1, uniform="stats")
            tile = ttk.Frame(stats, style="Card.TFrame", padding=(12, 8))
            tile.grid(row=0, column=index, sticky="nsew", padx=(0, 8 if index < 4 else 0))
            ttk.Label(tile, text=key.title(), style="Card.TLabel").pack(anchor="w")
            ttk.Label(tile, textvariable=self.metrics[key], style="Metric.TLabel").pack(anchor="w")

        progress = ttk.Frame(container, style="Card.TFrame", padding=(16, 12))
        progress.pack(fill="x", pady=(0, 12))
        ttk.Label(progress, textvariable=self.stage, style="Stage.TLabel").pack(anchor="w")
        ttk.Label(progress, textvariable=self.current, style="Card.TLabel", wraplength=650).pack(anchor="w", pady=(2, 5))
        self.bar = ttk.Progressbar(progress, maximum=1, value=0)
        self.bar.pack(fill="x")
        ttk.Label(progress, textvariable=self.count, style="Card.TLabel").pack(anchor="e", pady=(5, 0))

        controls = ttk.Frame(container)
        controls.pack(fill="x", pady=(0, 8))
        self.start_button = ttk.Button(controls, text="Start", command=self.start, style="Start.TButton")
        self.start_button.pack(side="left", padx=(0, 8))
        self.stop_button = ttk.Button(controls, text="Stop After Current Asset", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=(0, 8))
        self.retry_button = ttk.Button(controls, text="Retry Failed", command=self.start, state="disabled")
        self.retry_button.pack(side="left")
        ttk.Button(controls, text="Open Logs", command=lambda: self.open_path(
            self.run_dir or ROOT / "output" / "stock_manager")).pack(side="right")
        ttk.Button(controls, text="Open Output Folder", command=lambda: self.open_path(ROOT / "output")).pack(
            side="right", padx=(0, 8))
        settings = ttk.Frame(container)
        settings.pack(fill="x", pady=(0, 8))
        ttk.Label(settings, text="Adobe CDP").pack(side="left", padx=(0, 8))
        self.endpoint_entry = ttk.Entry(settings, textvariable=self.endpoint, width=28)
        self.endpoint_entry.pack(side="left")
        ttk.Label(settings, text="Leave blank to use the existing Edge profile.").pack(side="left", padx=10)
        options = ttk.Frame(container)
        options.pack(fill="x", pady=(0, 8))
        ttk.Label(options, text="Image model").pack(side="left", padx=(0, 8))
        self.model_picker = ttk.Combobox(options, values=tuple(IMAGE_MODELS),
                                         textvariable=self.image_model, state="readonly", width=19)
        self.model_picker.pack(side="left", padx=(0, 16))
        self.test_check = ttk.Checkbutton(options, text="Offline test: validate r02 only (no generation or upload)",
                                         variable=self.offline_test)
        self.test_check.pack(side="left")
        ttk.Label(container, text="Activity log").pack(anchor="w")
        logs = ttk.Frame(container)
        logs.pack(fill="both", expand=True)
        self.log_text = tk.Text(logs, wrap="word", background="#142331", foreground="#e6eff4", height=6,
                                font=("Consolas", 10), relief="flat", padx=10, pady=8, state="disabled")
        scroll = ttk.Scrollbar(logs, command=self.log_text.yview)
        scroll.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        window.after(75, self.poll)

    def append(self, message, stamp=None):
        if stamp:
            stamp = datetime.fromisoformat(stamp).astimezone().strftime("%H:%M:%S")
        else:
            stamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{stamp}  {message}\n")
        if int(self.log_text.index("end-1c").split(".")[0]) > 2500:
            self.log_text.delete("1.0", "301.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def start(self):
        if self.active:
            return
        try:
            testing = self.offline_test.get()
            cdp = validate_cdp(self.endpoint.get().strip()) if not testing and self.endpoint.get().strip() else None
            self.run_dir = ROOT / "output" / "stock_manager" / "runs" / uuid4().hex
            self.run_dir.mkdir(parents=True)
        except (ValueError, OSError) as exc:
            self.append(str(exc))
            return
        self.active = True
        self.start_button.state(["disabled"])
        self.retry_button.state(["disabled"])
        self.stop_button.state(["!disabled"])
        self.endpoint_entry.state(["disabled"])
        self.model_picker.state(["disabled"])
        self.test_check.state(["disabled"])
        for metric in self.metrics.values():
            metric.set("—")
        self.stage.set("Starting")
        self.current.set("Preparing offline queue check" if testing else "Waiting for Git sync")
        self.count.set("0 / 0 assets prepared")
        self.bar.configure(value=0)
        self.git_status.set("Checking")
        self.browser_status.set("Not checked")
        self.commit.set("—")
        python = Path(sys.executable)
        if os.name == "nt" and python.name.casefold() == "pythonw.exe":
            python = python.with_name("python.exe")
        args = [str(python), str(Path(__file__).resolve()), "--worker", str(self.run_dir)]
        if testing:
            args.append("--offline-test")
        else:
            args.extend(("--image-model", IMAGE_MODELS[self.image_model.get()]))
        if cdp:
            args.extend(("--cdp", cdp))
        elif not testing:
            profile = os.environ.get("ADOBE_USER_DATA_DIR")
            if profile:
                args.extend(("--profile", profile))
        threading.Thread(target=self.read_worker, args=(args,), daemon=True).start()

    def read_worker(self, args):
        redact = redactor(ROOT)
        try:
            with subprocess.Popen(args, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, encoding="utf-8", errors="replace",
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as child:
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

    def poll(self):
        for _ in range(100):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event.get("message"):
                self.append(event["message"], event.get("time"))
            for field, var in (("github", self.git_status), ("browser", self.browser_status), ("phase", self.stage)):
                if event.get(field):
                    var.set(event[field])
            if event.get("commit"):
                self.commit.set(event["commit"][:12])
            if event.get("source"):
                self.current.set(event["source"] + ("  •  " + event["asset"] if event.get("asset") else ""))
            if "total" in event:
                done, total = event.get("completed", 0), event["total"]
                self.bar.configure(maximum=max(total, 1), value=done)
                self.count.set(f"{done} / {total} assets prepared")
            for key, value in event.get("overview", {}).items():
                self.metrics[key].set(str(value))
            if event.get("report"):
                report = event["report"]
                self.stage.set(report["status"].replace("_", " ").title())
                if report["status"] == "TEST_PASSED":
                    self.bar.configure(maximum=report["total_assets"], value=report["total_assets"])
                    self.count.set(f"{report['total_assets']} assets validated; 0 generated")
                    self.current.set(TEST_QUEUE)
                else:
                    self.current.set(f"Generated {report.get('generated', 0)}  •  Failed {report.get('failed_assets', 0)}"
                                 f"  •  Drafts saved {report.get('uploaded', 0)}"
                                 f"  •  Already uploaded {report.get('already_uploaded', 0)}")
            if "exit" in event:
                self.active = False
                self.start_button.state(["!disabled"])
                self.retry_button.state(["!disabled"] if event["exit"] else ["disabled"])
                self.stop_button.state(["disabled"])
                self.endpoint_entry.state(["!disabled"])
                self.model_picker.state(["!disabled"])
                self.test_check.state(["!disabled"])
                if event["exit"]:
                    self.append(f"Worker stopped (exit {event['exit']}). See Open Logs.")
                if self.closing:
                    self.window.destroy()
                    return
        self.window.after(75, self.poll)

    def stop(self):
        if self.active and self.run_dir:
            try:
                (self.run_dir / "stop.request").touch()
            except OSError as exc:
                self.append(f"Could not request stop: {exc}")
                return
            self.stop_button.state(["disabled"])
            self.append("Stop requested. Waiting for the current operation to reach a safe boundary.")

    def close(self):
        if self.active:
            self.closing = True
            self.stop()
        else:
            self.window.destroy()

    def open_path(self, path):
        try:
            path.mkdir(parents=True, exist_ok=True)
            os.startfile(str(path))
        except OSError as exc:
            self.append(str(exc))


def main():
    parser = argparse.ArgumentParser(description=TITLE)
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--cdp", help=argparse.SUPPRESS)
    parser.add_argument("--profile", help=argparse.SUPPRESS)
    parser.add_argument("--offline-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--image-model", choices=tuple(IMAGE_MODELS.values()), help=argparse.SUPPRESS)
    parser.add_argument("--snapshot-baseline", action="store_true",
                        help="Skip the current incoming JSON snapshot in live runs, excluding the r02 test queue.")
    args = parser.parse_args()
    if args.snapshot_baseline:
        print(f"Snapshotted {snapshot_baseline(ROOT)} files; excluded {TEST_QUEUE}")
        return 0
    if args.worker:
        return worker(args.worker, args.cdp, args.profile, args.offline_test, args.image_model)
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
