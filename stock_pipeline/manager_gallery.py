"""Read saved image provenance and display a paged queue gallery."""
from collections import OrderedDict, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
import json
import os
import sqlite3
from tkinter import StringVar, Toplevel, ttk

from PIL import Image, ImageTk

from .prompt_builder import render_image_prompt


@dataclass(frozen=True)
class GalleryImage:
    queue: str
    lane: str
    theme: str
    asset_id: str
    path: Path


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def saved_images(root):
    queues, prompts, images = {}, defaultdict(set), []
    template = (root / "config/prompts/image_prompt_v1.txt").read_text(encoding="utf-8")
    for path in (root / "queues").rglob("*.json"):
        data = read_json(path)
        source = path.relative_to(root).as_posix()
        if path.name.endswith(".example.json"):
            continue
        if isinstance(data, dict):
            queue_id = data.get("run_id")
            if isinstance(queue_id, str):
                queues.setdefault(queue_id, source)
            data = data.get("assets")
        if not isinstance(data, list):
            continue
        for asset in data:
            if not isinstance(asset, dict) or not isinstance(asset.get("theme"), str):
                continue
            try:
                prompt = render_image_prompt(template, asset).strip()
            except (KeyError, ValueError):
                continue
            prompts[prompt].add((source, asset["theme"], str(asset.get("asset_id") or "")))

    db = root / "data/stock_pipeline.sqlite"
    if db.is_file():
        from contextlib import closing

        with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "manager_queues" in tables:
                for key, value in connection.execute("SELECT queue_key, state_json FROM manager_queues"):
                    source = json.loads(value).get("source_file")
                    if source:
                        queues[key] = source
            if "assets" in tables:
                for aid, run_id, theme, filename in connection.execute(
                    "SELECT asset_id, run_id, theme, image_path FROM assets WHERE image_path IS NOT NULL"
                ):
                    path = root / filename
                    if path.is_file():
                        lane = "Local" if run_id.startswith("TF_LOCAL_") else "Cloud"
                        images.append(GalleryImage(queues.get(run_id, run_id), lane, theme, aid, path))

    for directory in (root / "output/stock_manager/runs").glob("*/local_images"):
        manifest = read_json(directory / "gallery.json")
        if isinstance(manifest, dict):
            for record in manifest["images"]:
                path = directory / record["file"]
                if path.is_file():
                    images.append(GalleryImage(manifest["queue"], "Local", record["theme"], record["asset_id"], path))
            continue
        # Older previews saved the exact rendered prompt alongside the PNG.
        for path in directory.glob("*.png"):
            try:
                prompt = path.with_name(path.stem + "_prompt.txt").read_text(encoding="utf-8").strip()
            except (OSError, UnicodeError):
                continue
            matches = prompts.get(prompt, ())
            if len(matches) == 1:
                source, theme, aid = next(iter(matches))
            else:
                source, theme, aid = "Unidentified local tests", "Theme unavailable", ""
            images.append(GalleryImage(source, "Local", theme, aid, path))
    return sorted(images, key=lambda item: (item.queue.casefold(), item.asset_id, str(item.path)))


class ImageGallery:
    PAGE_SIZE = 6
    CACHE_SIZE = 96

    def __init__(self, parent, root):
        self.root = root
        self.window = Toplevel(parent)
        self.window.title("Generated Images")
        self.window.geometry("860x740")
        self.window.minsize(750, 710)
        self.window.bind("<Escape>", lambda _event: self.window.destroy())
        self.window.bind("<Destroy>", self.on_destroy)
        layout = ttk.Frame(self.window, padding=16)
        layout.pack(expand=True, fill="both")
        filters = ttk.Frame(layout)
        filters.pack(fill="x", pady=(0, 10))
        ttk.Label(filters, text="Source JSON").pack(side="left", padx=(0, 8))
        self.queue = StringVar(self.window)
        self.picker = ttk.Combobox(filters, textvariable=self.queue, state="readonly")
        self.picker.pack(side="left", fill="x", expand=True)
        self.picker.bind("<<ComboboxSelected>>", self.reset_page)
        ttk.Button(filters, text="Refresh", command=self.refresh).pack(side="right", padx=(8, 0))
        self.tabs = ttk.Notebook(layout)
        self.tabs.pack(fill="both", expand=True)
        self.grids = {}
        for lane in ("Cloud", "Local"):
            grid = ttk.Frame(self.tabs, padding=8)
            self.tabs.add(grid, text=lane)
            self.grids[lane] = grid
        self.tabs.bind("<<NotebookTabChanged>>", self.reset_page)
        footer = ttk.Frame(layout)
        footer.pack(fill="x", pady=(8, 0))
        self.caption = ttk.Label(footer)
        self.caption.pack(side="left")
        self.next = ttk.Button(footer, text="Next", command=lambda: self.turn_page(1))
        self.next.pack(side="right")
        self.previous = ttk.Button(footer, text="Previous", command=lambda: self.turn_page(-1))
        self.previous.pack(side="right", padx=6)
        self.page = 0
        self.images = {}
        self.cache = OrderedDict()
        self.executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="gallery")
        self.index_job = None
        self.thumbnails = []
        self.poll_id = self.window.after(40, self.poll)
        self.refresh()

    def refresh(self):
        if self.index_job is None:
            self.caption.configure(text="Loading saved images…")
            self.index_job = self.executor.submit(saved_images, self.root)

    def update_index(self, job):
        try:
            images = job.result()
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.caption.configure(text=f"Could not read saved images: {exc}")
            return
        grouped = defaultdict(list)
        for item in images:
            grouped[item.queue, item.lane].append(item)
        self.images = grouped
        queues = sorted({item.queue for item in images}, key=str.casefold)
        self.picker.configure(values=queues)
        if self.queue.get() not in queues:
            self.queue.set(queues[0] if queues else "")
            self.page = 0
        self.show_page()

    def reset_page(self, _event=None):
        self.page = 0
        self.show_page()

    def turn_page(self, offset):
        self.page += offset
        self.show_page()

    def show_page(self):
        lane = self.tabs.tab(self.tabs.select(), "text")
        for job, _key, _button in self.thumbnails:
            job.cancel()
        self.thumbnails.clear()
        grid = self.grids[lane]
        for child in grid.winfo_children():
            child.destroy()
        rows = self.images.get((self.queue.get(), lane), ())
        self.page = min(self.page, max(0, (len(rows) - 1) // self.PAGE_SIZE))
        offset = self.page * self.PAGE_SIZE
        self.previous.state(["!disabled" if offset else "disabled"])
        self.next.state(["!disabled" if offset + self.PAGE_SIZE < len(rows) else "disabled"])
        if not rows:
            if self.index_job is None:
                self.caption.configure(text=f"No {lane.lower()} images saved for this queue.")
            return
        self.caption.configure(text=f"{offset + 1}–{min(offset + self.PAGE_SIZE, len(rows))} of {len(rows)} · Click a thumbnail to open")
        for column in range(3):
            grid.columnconfigure(column, weight=1, uniform="images")
        for index, item in enumerate(rows[offset:offset + self.PAGE_SIZE]):
            card = ttk.Frame(grid, padding=6)
            card.grid(row=index // 3, column=index % 3, sticky="nsew")
            thumbnail_frame = ttk.Frame(card, width=224, height=166)
            thumbnail_frame.pack(anchor="w")
            thumbnail_frame.pack_propagate(False)
            button = ttk.Button(thumbnail_frame, text="Open image", command=lambda path=item.path: self.open_image(path))
            button.pack(expand=True)
            try:
                stat = item.path.stat()
            except OSError:
                stat = None
            if stat is not None:
                key = (item.path, stat.st_mtime_ns, stat.st_size)
                if key in self.cache:
                    self.cache.move_to_end(key)
                    self.display_thumbnail(button, self.cache[key])
                else:
                    job = self.executor.submit(self.load_thumbnail, item.path)
                    self.thumbnails.append((job, key, button))
            ttk.Label(card, text=item.theme, wraplength=214, justify="left").pack(anchor="w", pady=(4, 0))
            ttk.Label(card, text=item.asset_id or item.path.name).pack(anchor="w")

    @staticmethod
    def load_thumbnail(path):
        with Image.open(path) as source:
            source.thumbnail((214, 156), Image.Resampling.BILINEAR, reducing_gap=2)
            return source.copy()

    def display_thumbnail(self, button, thumbnail):
        photo = ImageTk.PhotoImage(thumbnail, master=self.window)
        button.configure(image=photo, text="")
        button.thumbnail = photo

    def poll(self):
        if self.index_job is not None and self.index_job.done():
            job, self.index_job = self.index_job, None
            self.update_index(job)
        pending = []
        for job, key, button in self.thumbnails:
            if not job.done():
                pending.append((job, key, button))
                continue
            try:
                thumbnail = job.result()
            except (OSError, ValueError, Image.DecompressionBombError):
                continue
            self.cache[key] = thumbnail
            self.cache.move_to_end(key)
            if len(self.cache) > self.CACHE_SIZE:
                self.cache.popitem(last=False)
            self.display_thumbnail(button, thumbnail)
        self.thumbnails = pending
        self.poll_id = self.window.after(40, self.poll)

    def on_destroy(self, event):
        if event.widget is self.window:
            self.window.after_cancel(self.poll_id)
            self.executor.shutdown(wait=False, cancel_futures=True)

    def open_image(self, path):
        try:
            os.startfile(str(path))
        except OSError as exc:
            self.caption.configure(text=str(exc))
