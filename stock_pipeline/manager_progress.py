"""Independent progress cards and Adobe upload state for one generation lane."""
import time
import tkinter as tk
from tkinter import ttk


class ProgressPanel:
    def __init__(self, parent, count, asset_stages, upload_stages, finished_stages):
        self.tk = tk
        self.count = count
        self.asset_stages = asset_stages
        self.upload_stages = upload_stages
        self.finished_stages = finished_stages
        self.asset_cards, self.asset_slots, self.asset_timers = [], {}, {}
        self.frame = ttk.Frame(parent)
        self.frame.pack(fill="x")
        self.summary = ttk.Label(self.frame, text="Waiting")
        self.summary.pack(anchor="w")
        self.progress_heading = ttk.Label(self.frame, text="Asset progress")
        self.progress_heading.pack(anchor="w", pady=(0, 5))
        self.asset_panel = ttk.Frame(self.frame)
        self.upload_panel = ttk.Frame(self.frame, style="Card.TFrame", padding=(12, 8))
        header = ttk.Frame(self.upload_panel, style="Card.TFrame")
        header.pack(fill="x")
        self.upload_step = ttk.Label(header, style="AssetTitle.TLabel")
        self.upload_step.pack(side="left")
        self.upload_count = ttk.Label(header, style="Card.TLabel")
        self.upload_count.pack(side="right")
        self.upload_detail = ttk.Label(self.upload_panel, style="Card.TLabel", wraplength=650)
        self.upload_detail.pack(anchor="w", pady=(4, 6))
        self.upload_bar = ttk.Progressbar(self.upload_panel)
        self.upload_bar.pack(fill="x")
        self.reset_asset_cards()

    def tick(self):
        for aid, (started, label) in self.asset_timers.items():
            status = self.asset_cards[self.asset_slots[aid]][3]
            text = self.timed_label(label, time.monotonic() - started)
            if status.cget("text") != text:
                status.configure(text=text)

    def finish(self, status, error=""):
        for aid in list(self.asset_slots):
            self.update_asset_card({"asset_id": aid, "stage": "interrupted"})
        self.summary.configure(text=status.replace("_", " ").title())
        if self.upload_panel.winfo_manager() and status not in {"COMPLETE", "TEST_PASSED"}:
            self.upload_step.configure(text=self.upload_stages["stopped" if status == "STOPPED" else "incomplete"])
            self.upload_detail.configure(text=error)

    def reset_asset_cards(self):
        from tkinter import ttk

        self.upload_panel.pack_forget()
        self.progress_heading.configure(text="Asset progress")
        self.asset_panel.pack(fill="x")
        for index, (tile, *_) in enumerate(self.asset_cards):
            tile.destroy()
            self.asset_panel.columnconfigure(index, weight=0, uniform="")
        self.asset_cards.clear()
        self.asset_slots.clear()
        self.asset_timers.clear()
        count = self.count
        for index in range(count):
            self.asset_panel.columnconfigure(index, weight=1, uniform="assets")
            tile = ttk.Frame(self.asset_panel, style="Card.TFrame", padding=(12, 8))
            tile.grid(row=0, column=index, sticky="nsew", padx=(0, 8 if index < count - 1 else 0))
            title = ttk.Label(tile, text="Waiting for an asset", style="AssetTitle.TLabel")
            title.pack(anchor="w")
            theme = self.tk.Label(tile, text="", height=2, bg="#ffffff", fg="#162230",
                                  font=("Segoe UI", 10), justify="left", anchor="nw")
            theme.pack(fill="x")
            tile.bind("<Configure>", lambda event, label=theme: label.configure(wraplength=max(1, event.width - 24)))
            status = ttk.Label(tile, text="", style="Card.TLabel")
            status.pack(anchor="w", pady=(2, 4))
            bar = ttk.Progressbar(tile, maximum=3, value=0)
            bar.pack(fill="x")
            self.asset_cards.append((tile, title, theme, status, bar))

    def update_asset_card(self, event):
        asset_id, stage = event["asset_id"], event["stage"]
        if stage == "preparing":
            used = self.asset_slots.values()
            slot = next((index for index in range(len(self.asset_cards)) if index not in used), None)
            if slot is None:
                return
            self.asset_slots[asset_id] = slot
            _, title, theme, _, _ = self.asset_cards[slot]
            title.configure(text=f"Image {event['index']} of {event['total']}  ·  {asset_id}")
            theme.configure(text=event["theme"])
        slot = self.asset_slots.get(asset_id)
        if slot is None:
            return
        _, _, _, status, bar = self.asset_cards[slot]
        label, value, color = self.asset_stages[stage]
        if stage in self.finished_stages and not event.get("local") and asset_id in self.asset_timers:
            started, _ = self.asset_timers[asset_id]
            label = self.timed_label(label, time.monotonic() - started)
        if event.get("local"):
            step, steps = event["step"], event["steps"]
            label = f"{label}  ·  Step {step}/{steps}"
            elapsed = event["elapsed_seconds"]
            if stage not in self.finished_stages:
                self.asset_timers[asset_id] = (time.monotonic() - elapsed, label)
            label = self.timed_label(label, elapsed)
            if stage == "local_sampling":
                value = 2.5 * step / steps
        status.configure(text=label, foreground=color)
        if value is not None:
            bar.configure(value=value)
        if stage in self.finished_stages:
            self.asset_timers.pop(asset_id, None)
            del self.asset_slots[asset_id]

    @staticmethod
    def timed_label(label, elapsed):
        minutes, seconds = divmod(int(elapsed), 60)
        return f"{label}  ·  {minutes:02d}:{seconds:02d} elapsed"

    def update_upload_panel(self, event):
        if not self.upload_panel.winfo_manager():
            self.asset_panel.pack_forget()
            heading = "Adobe draft upload"
            if event.get("simulated"):
                heading += " · Offline simulation"
            self.progress_heading.configure(text=heading)
            self.upload_panel.pack(fill="x")
        self.upload_step.configure(text=self.upload_stages.get(event["stage"], "Processing Adobe drafts"))
        self.upload_detail.configure(text=event["detail"])
        done, total = event["done"], event["total"]
        self.upload_count.configure(text=f"{done} / {total} drafts verified")
        self.upload_bar.configure(maximum=max(total, 1), value=done)

