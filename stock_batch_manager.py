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

from stock_pipeline.manager_runtime import (
    cdp_connected, git_sync, manager_lock, redactor, validate_cdp, verify_reviewed_checkout,
)
from stock_pipeline.manager_baseline import TEST_QUEUE, snapshot_baseline

ROOT = Path(__file__).resolve().parent
TITLE = "Adobe Stock Batch Manager"
IMAGE_MODELS = {
    "Nano Banana Pro": "gemini-3-pro-image",
    "Nano Banana 2.1": "gemini-nano-banana-2.1",
}
DEFAULT_METADATA_MODEL = "gemini-3.8-flash"


def worker(run_dir: Path, cdp: str | None, profile: str | None, offline_test: bool = False,
           image_model: str | None = None, metadata_model: str | None = None, *,
           temperature: float = 1.0, top_p: float = 0.95, max_images: int | None = None) -> int:
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
            changes = {}
            sha, commit_time = git_sync(ROOT, incoming_changes=changes)
            log(f"Git synced: {sha}", phase="Git sync", github="Connected", commit=sha)
            from stock_pipeline.batch_manager import preview_queues, run_manager
            rows, _ = preview_queues(ROOT, commit_time, changes)
            pending = [row["source_file"] for row in rows if row["selectable"]]
            request = run_dir / "start.request"
            disconnected = threading.Event()

            def watch_window():
                try:
                    os.read(sys.stdin.fileno(), 1)
                except (AttributeError, OSError, ValueError):
                    pass
                finally:
                    disconnected.set()

            threading.Thread(target=watch_window, daemon=True).start()
            log(f"{len(pending)} runnable queue(s) awaiting review", phase="Queue preview",
                preview={"queues": rows, "max_images": max_images})
            while not (stop.exists() or request.exists() or disconnected.wait(0.15)):
                pass
            if stop.exists() or disconnected.is_set():
                log("Queue preview closed.", phase="Preview closed")
                return 0
            count = int(request.read_text(encoding="ascii"))
            if not 1 <= count <= len(pending):
                raise ValueError("Choose a valid number of queues and check the preview again.")
            verify_reviewed_checkout(ROOT, sha)
            current_rows, sources = preview_queues(ROOT, commit_time, changes)
            if current_rows != rows:
                raise RuntimeError("Queues or local progress changed since preview. Check queues again.")
            selected = set(pending[:count])
            if cdp:
                connected = cdp_connected(cdp)
                log("Edge CDP is reachable" if connected else "Edge CDP is not reachable; upload will need Edge",
                    browser="Connected" if connected else "Disconnected")
            else:
                log("Adobe will use the persistent browser profile at the upload stage", browser="Profile mode")
            report = run_manager(ROOT, sha, commit_time, run_dir, should_stop=stop.exists, emit=emit,
                                 cdp=cdp, profile=profile, stop_file=stop, image_model=image_model,
                                 metadata_model=metadata_model, temperature=temperature,
                                 top_p=top_p, max_images=max_images,
                                 sources=[source for source in sources if source.source_file in selected])
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
        self.preview_window = None
        self.offline_test = tk.BooleanVar(value=True)
        self.image_model = tk.StringVar(value="Nano Banana Pro")
        self.metadata_model = tk.StringVar(value=DEFAULT_METADATA_MODEL)
        self.temperature = tk.StringVar(value="1.0")
        self.top_p = tk.StringVar(value="0.95")
        self.max_images = tk.StringVar()
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
        self.start_button = ttk.Button(controls, text="Run Offline Test", command=self.start, style="Start.TButton")
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
        ttk.Label(options, text="Metadata model").pack(side="left", padx=(0, 8))
        self.metadata_picker = ttk.Combobox(options, values=(DEFAULT_METADATA_MODEL,),
                                            textvariable=self.metadata_model, width=23)
        self.metadata_picker.pack(side="left")
        sampling = ttk.Frame(container)
        sampling.pack(fill="x", pady=(0, 8))
        self.generation_entries = []
        for label, value in (("Temperature", self.temperature), ("Top-p", self.top_p),
                             ("Max new images", self.max_images)):
            ttk.Label(sampling, text=label).pack(side="left", padx=(0, 6))
            entry = ttk.Entry(sampling, textvariable=value, width=7)
            entry.pack(side="left", padx=(0, 14))
            self.generation_entries.append(entry)
        ttk.Label(sampling, text="Blank = unlimited").pack(side="left")
        self.model_picker.bind("<<ComboboxSelected>>", lambda _event: self.refresh_generation_entries())
        self.test_check = ttk.Checkbutton(container, text="Offline test: validate r02 only (no generation or upload)",
                                         variable=self.offline_test, command=self.refresh_start_button)
        self.test_check.pack(anchor="w", pady=(0, 8))
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

    def refresh_start_button(self):
        self.start_button.configure(text="Run Offline Test" if self.offline_test.get() else "Check Queues")

    def refresh_generation_entries(self):
        pro = self.image_model.get() == "Nano Banana Pro"
        for index, entry in enumerate(self.generation_entries):
            entry.state(["disabled" if self.active or (index < 2 and not pro) else "!disabled"])

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
            metadata_model = self.metadata_model.get().strip()
            if not testing and not metadata_model:
                raise ValueError("Enter a Vertex AI metadata model ID.")
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
                if self.image_model.get() == "Nano Banana Pro":
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
            cdp = validate_cdp(self.endpoint.get().strip()) if not testing and self.endpoint.get().strip() else None
            self.run_dir = ROOT / "output" / "stock_manager" / "runs" / uuid4().hex
            self.run_dir.mkdir(parents=True)
        except (ValueError, OSError) as exc:
            self.append(str(exc))
            return
        self.active = True
        self.refresh_generation_entries()
        self.start_button.state(["disabled"])
        self.retry_button.state(["disabled"])
        self.stop_button.state(["!disabled"])
        self.endpoint_entry.state(["disabled"])
        self.model_picker.state(["disabled"])
        self.metadata_picker.state(["disabled"])
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
            args.extend(("--image-model", IMAGE_MODELS[self.image_model.get()], "--metadata-model", metadata_model))
            args.extend(generation_args)
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
            with subprocess.Popen(args, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
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

    def show_preview(self, preview):
        from tkinter import ttk

        rows = preview["queues"]
        pending = [row for row in rows if row["selectable"]]
        new = sum(row["git_change"] == "New" for row in rows)
        updated = sum(row["git_change"] == "Updated" for row in rows)
        invalid = sum(row["status"] == "Invalid" for row in rows)
        dialog = self.preview_window = self.tk.Toplevel(self.window)
        dialog.title("Queue Preview")
        dialog.geometry("1070x594")
        dialog.minsize(760, 449)
        dialog.transient(self.window)
        dialog.grab_set()
        panel = ttk.Frame(dialog, padding=16)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text=f"Pulled {new} new and {updated} updated JSON files  •  "
                             f"{len(pending)} runnable queues / {sum(row['total'] for row in pending)} images"
                             f"  •  {invalid} invalid").pack(anchor="w", pady=(0, 10))
        table_box = ttk.Frame(panel)
        table_box.pack(fill="both", expand=True)
        table = ttk.Treeview(table_box, show="headings", selectmode="browse",
                             columns=("file", "themes", "git", "status", "total", "remaining"))
        scroll = ttk.Scrollbar(table_box, orient="vertical", command=table.yview)
        table.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        table.pack(side="left", fill="both", expand=True)
        for key, title, width in (
            ("file", "JSON file", 275), ("themes", "Themes", 320), ("git", "Git", 70),
            ("status", "Status", 135), ("total", "Images", 65), ("remaining", "To generate", 95),
        ):
            table.heading(key, text=title)
            table.column(key, width=width, minwidth=55, stretch=key in {"file", "themes"})
        table.tag_configure("planned", background="#dcecf3")
        visible = [row for row in rows if row["selectable"] or row["git_change"] or row["error"]]
        for index, row in enumerate(visible):
            table.insert("", "end", iid=str(index), values=(
                row["source_file"].removeprefix("queues/incoming/"), ", ".join(row["themes"]),
                row["git_change"], row["status"], row["total"] if row["total"] else "—",
                row["new_images"] if row["selectable"] else "—",
            ))
        detail = self.tk.Text(panel, height=5, wrap="word", font=("Segoe UI", 10), state="disabled")
        detail.pack(fill="x", pady=(6, 6))

        def show_details(_event=None):
            selection = table.selection()
            detail.configure(state="normal")
            detail.delete("1.0", "end")
            if selection:
                row = visible[int(selection[0])]
                detail.insert("end", "\n".join(filter(None, [
                    row["source_file"], ", ".join(row["themes"]), row["error"],
                ])))
            detail.configure(state="disabled")

        table.bind("<<TreeviewSelect>>", show_details)
        controls = ttk.Frame(panel)
        controls.pack(fill="x")
        ttk.Label(controls, text="Run first").pack(side="left")
        count = self.tk.StringVar(value=str(len(pending)))
        picker = ttk.Spinbox(controls, from_=1, to=max(1, len(pending)), textvariable=count, width=6)
        picker.pack(side="left", padx=6)
        ttk.Label(controls, text="JSON files").pack(side="left")
        summary = ttk.Label(controls)
        summary.pack(side="left", padx=12)

        def chosen_count():
            try:
                return int(count.get())
            except ValueError:
                return 0

        def close_preview():
            self.stop()

        def confirm():
            value = chosen_count()
            if not 1 <= value <= len(pending):
                return
            try:
                temporary = self.run_dir / "start.tmp"
                temporary.write_text(str(value), encoding="ascii")
                temporary.replace(self.run_dir / "start.request")
            except OSError as exc:
                self.append(f"Could not start: {exc}")
                return
            self.dismiss_preview()
            self.stage.set("Starting selected queues")

        ttk.Button(controls, text="Cancel" if pending else "Close", command=close_preview).pack(side="right")
        start = ttk.Button(controls, text="Start Selected", command=confirm, style="Start.TButton")
        if pending:
            start.pack(side="right", padx=8)
        else:
            picker.state(["disabled"])

        def update_selection(*_args):
            value = chosen_count()
            valid = 1 <= value <= len(pending)
            start.state(["!disabled" if valid else "disabled"])
            chosen = pending[:value] if valid else []
            selected_paths = {row["source_file"] for row in chosen}
            for index, row in enumerate(visible):
                table.item(str(index), tags=("planned",) if row["source_file"] in selected_paths else ())
            if valid:
                images = sum(row["new_images"] for row in chosen)
                if preview["max_images"] is not None:
                    images = min(images, preview["max_images"])
                summary.configure(text=f"{sum(row['total'] for row in chosen)} images  •  "
                                       f"{images} estimated new")
            else:
                summary.configure(text="Choose a queue count" if pending else "No runnable queues")

        count.trace_add("write", update_selection)
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
            if event.get("preview") and not (self.closing or (self.run_dir / "stop.request").exists()):
                self.show_preview(event["preview"])
            if "exit" in event:
                self.dismiss_preview()
                self.active = False
                self.refresh_generation_entries()
                self.start_button.state(["!disabled"])
                self.retry_button.state(["!disabled"] if event["exit"] else ["disabled"])
                self.stop_button.state(["disabled"])
                self.endpoint_entry.state(["!disabled"])
                self.model_picker.state(["!disabled"])
                self.metadata_picker.state(["!disabled"])
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
            self.dismiss_preview()
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
    parser.add_argument("--metadata-model", help=argparse.SUPPRESS)
    parser.add_argument("--temperature", type=float, default=1.0, help=argparse.SUPPRESS)
    parser.add_argument("--top-p", type=float, default=0.95, help=argparse.SUPPRESS)
    parser.add_argument("--max-images", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--snapshot-baseline", action="store_true",
                        help="Skip the current incoming JSON snapshot in live runs, excluding the r02 test queue.")
    args = parser.parse_args()
    if args.snapshot_baseline:
        print(f"Snapshotted {snapshot_baseline(ROOT)} files; excluded {TEST_QUEUE}")
        return 0
    if args.worker:
        return worker(args.worker, args.cdp, args.profile, args.offline_test, args.image_model, args.metadata_model,
                      temperature=args.temperature, top_p=args.top_p, max_images=args.max_images)
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
