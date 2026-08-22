from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STATUSES = (
    "QUEUED", "PROMPT_READY", "IMAGE_GENERATING", "IMAGE_READY", "IMAGE_QC_FAILED",
    "METADATA_PENDING", "METADATA_READY", "METADATA_QC_FAILED", "READY_TO_STAGE",
    "STAGED", "READY_TO_UPLOAD", "UPLOADED_DRAFT", "FAILED",
)
TRANSITIONS = {
    "QUEUED": {"PROMPT_READY", "FAILED"},
    "PROMPT_READY": {"IMAGE_GENERATING", "IMAGE_READY", "FAILED"},
    "IMAGE_GENERATING": {"IMAGE_READY", "IMAGE_QC_FAILED", "FAILED"},
    "IMAGE_READY": {"METADATA_PENDING", "FAILED"},
    "IMAGE_QC_FAILED": {"IMAGE_GENERATING", "FAILED"},
    "METADATA_PENDING": {"METADATA_READY", "METADATA_QC_FAILED", "FAILED"},
    "METADATA_READY": {"READY_TO_STAGE", "METADATA_PENDING", "FAILED"},
    "METADATA_QC_FAILED": {"METADATA_PENDING", "FAILED"},
    "READY_TO_STAGE": {"STAGED", "FAILED"},
    "STAGED": {"READY_TO_UPLOAD", "FAILED"},
    "READY_TO_UPLOAD": {"UPLOADED_DRAFT", "FAILED"},
    "UPLOADED_DRAFT": set(),
    "FAILED": {"PROMPT_READY", "IMAGE_GENERATING", "METADATA_PENDING"},
}


class InvalidTransition(ValueError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Registry:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self._create_schema()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS assets (
                asset_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, domain TEXT NOT NULL,
                theme TEXT NOT NULL, source_type TEXT NOT NULL, topic_score REAL NOT NULL,
                topic_bucket TEXT NOT NULL, status TEXT NOT NULL, topic_finder_version TEXT NOT NULL,
                icon_1 TEXT NOT NULL, icon_2 TEXT NOT NULL, icon_3 TEXT NOT NULL, icon_4 TEXT NOT NULL,
                palette TEXT NOT NULL, image_prompt_version TEXT, image_model TEXT,
                metadata_prompt_version TEXT, metadata_model TEXT, generation_prompt TEXT,
                image_path TEXT, upload_filename TEXT UNIQUE, title TEXT, keywords_json TEXT,
                category_code INTEGER, category_name TEXT, created_at TEXT NOT NULL,
                generated_at TEXT, metadata_generated_at TEXT, staged_at TEXT, uploaded_at TEXT,
                error TEXT, retry_count INTEGER NOT NULL DEFAULT 0, accepted_at TEXT,
                rejected_at TEXT, rejection_reason TEXT, downloads INTEGER,
                revenue REAL, snapshot_date TEXT
            );
            CREATE TABLE IF NOT EXISTS id_allocator (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1), next_number INTEGER NOT NULL
            );
            INSERT OR IGNORE INTO id_allocator(singleton, next_number) VALUES(1, 1);
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def allocate_asset_id(self) -> str:
        maximum = self.connection.execute(
            "SELECT MAX(CAST(SUBSTR(asset_id, 4) AS INTEGER)) FROM assets"
        ).fetchone()[0] or 0
        row = self.connection.execute("SELECT next_number FROM id_allocator WHERE singleton=1").fetchone()
        number = max(maximum + 1, row[0])
        self.connection.execute("UPDATE id_allocator SET next_number=? WHERE singleton=1", (number + 1,))
        self.connection.commit()
        return f"AST{number:06d}"

    def register_queue(self, queue: dict[str, Any]) -> None:
        from .contracts import validate_topic_queue
        queue = validate_topic_queue(queue)
        identity_fields = (
            "run_id", "domain", "theme", "source_type", "topic_score", "topic_bucket",
            "topic_finder_version", "icon_1", "icon_2", "icon_3", "icon_4", "palette",
        )
        pending: list[dict[str, Any]] = []
        for item in queue["assets"]:
            existing = self.get_asset(item["asset_id"])
            incoming = {**item, "run_id": queue["run_id"]}
            if existing:
                if any(existing[field] != incoming[field] for field in identity_fields):
                    raise ValueError(f"asset_id {item['asset_id']} conflicts with existing identity")
                continue
            pending.append(item)
        with self.connection:
            for item in pending:
                self.connection.execute(
                    """INSERT INTO assets (
                        asset_id, run_id, domain, theme, source_type, topic_score, topic_bucket,
                        status, topic_finder_version, icon_1, icon_2, icon_3, icon_4, palette, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'QUEUED', ?, ?, ?, ?, ?, ?, ?)""",
                    (item["asset_id"], queue["run_id"], item["domain"], item["theme"],
                     item["source_type"], item["topic_score"], item["topic_bucket"],
                     item["topic_finder_version"], item["icon_1"], item["icon_2"],
                     item["icon_3"], item["icon_4"], item["palette"], now_iso()),
                )

    def get_asset(self, asset_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM assets WHERE asset_id=?", (asset_id,)).fetchone()
        return dict(row) if row else None

    def list_assets(self, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = self.connection.execute("SELECT * FROM assets WHERE status=? ORDER BY asset_id", (status,))
        else:
            rows = self.connection.execute("SELECT * FROM assets ORDER BY asset_id")
        return [dict(row) for row in rows]

    def transition(self, asset_id: str, new_status: str, *, error: str | None = None) -> None:
        asset = self.get_asset(asset_id)
        if not asset:
            raise KeyError(asset_id)
        if new_status not in STATUSES or new_status not in TRANSITIONS[asset["status"]]:
            raise InvalidTransition(f"invalid status transition {asset['status']} -> {new_status}")
        self.connection.execute(
            "UPDATE assets SET status=?, error=?, retry_count=retry_count + ? WHERE asset_id=?",
            (new_status, error, 1 if new_status == "FAILED" else 0, asset_id),
        )
        self.connection.commit()

    def set_status_for_test(self, asset_id: str, status: str) -> None:
        if status not in STATUSES:
            raise ValueError(status)
        self.connection.execute("UPDATE assets SET status=? WHERE asset_id=?", (status, asset_id))
        self.connection.commit()

    def update_asset(self, asset_id: str, **fields: Any) -> None:
        allowed = {
            "image_prompt_version", "image_model", "metadata_prompt_version", "metadata_model",
            "generation_prompt", "image_path", "upload_filename", "title", "keywords_json",
            "category_code", "category_name", "generated_at", "metadata_generated_at", "staged_at",
            "uploaded_at", "error", "retry_count",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unsupported asset fields: {sorted(unknown)}")
        if not fields:
            return
        assignments = ", ".join(f"{key}=?" for key in fields)
        self.connection.execute(
            f"UPDATE assets SET {assignments} WHERE asset_id=?",
            (*fields.values(), asset_id),
        )
        self.connection.commit()

    def update_performance(self, asset_id: str, **fields: Any) -> None:
        allowed = {"accepted_at", "rejected_at", "rejection_reason", "downloads", "revenue", "snapshot_date"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unsupported performance fields: {sorted(unknown)}")
        assignments = ", ".join(f"{key}=?" for key in fields)
        self.connection.execute(f"UPDATE assets SET {assignments} WHERE asset_id=?", (*fields.values(), asset_id))
        self.connection.commit()

    def mark_failed(self, asset_id: str, error: str) -> None:
        self.connection.execute(
            "UPDATE assets SET status='FAILED', error=?, retry_count=retry_count+1 WHERE asset_id=?",
            (error, asset_id),
        )
        self.connection.commit()

    def reset_for_image(self, asset_id: str) -> None:
        self.connection.execute(
            """UPDATE assets SET status='PROMPT_READY', image_path=NULL, generated_at=NULL,
            metadata_generated_at=NULL, title=NULL, keywords_json=NULL, category_code=NULL,
            category_name=NULL, staged_at=NULL, uploaded_at=NULL, error=NULL WHERE asset_id=?""",
            (asset_id,),
        )
        self.connection.commit()

    def reset_for_metadata(self, asset_id: str) -> None:
        asset = self.get_asset(asset_id)
        if not asset or not asset["image_path"]:
            raise ValueError(f"cannot reset metadata without an image: {asset_id}")
        self.connection.execute(
            """UPDATE assets SET status='METADATA_PENDING', metadata_generated_at=NULL,
            title=NULL, keywords_json=NULL, category_code=NULL, category_name=NULL,
            staged_at=NULL, uploaded_at=NULL, error=NULL WHERE asset_id=?""",
            (asset_id,),
        )
        self.connection.commit()
