from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from .contracts import validate_topic_queue
from .filenames import map_upload_filename
from .image_generator import generate_original_image
from .image_qc import inspect_image
from .logging import AssetLogger
from .metadata_generator import generate_metadata_for_asset, parse_metadata_json
from .metadata_validator import parse_categories, validate_metadata
from .pipeline import process_batch
from .prompt_builder import render_image_prompt_file
from .registry import Registry, now_iso
from .staging import stage_ready_assets


class StockPipeline:
    def __init__(
        self, *, project_root: Path, registry: Registry,
        image_generate_bytes: Callable[..., bytes] | None = None,
        metadata_generate_text: Callable[..., str] | None = None,
        image_model: str = "gemini-3-pro-image-preview",
        metadata_provider: str = "google",
        metadata_model: str = "gemini-2.5-flash",
        minimum_resolution: int | None = None,
        temperature: float = 1.0, top_p: float = 0.95,
        max_attempts: int = 4, retry_initial_wait: float = 1.0,
        config: dict[str, Any] | None = None,
    ):
        self.project_root = project_root
        self.registry = registry
        self.image_generate_bytes = image_generate_bytes
        self.metadata_generate_text = metadata_generate_text
        self.image_model = image_model
        self.metadata_provider = metadata_provider
        self.metadata_model = metadata_model
        config = config or {}
        image_config = config.get("image", {})
        metadata_config = config.get("metadata", {})
        adobe_config = config.get("adobe", {})
        self.minimum_resolution = minimum_resolution or int(image_config.get("minimum_resolution", 2048))
        self.aspect_ratio = str(image_config.get("aspect_ratio", "1:1"))
        self.resolution = str(image_config.get("resolution", "2K"))
        self.max_title_chars = int(metadata_config.get("max_title_chars", 70))
        self.hard_max_keywords = int(metadata_config.get("hard_max_keywords", 49))
        self.max_filename_chars = int(adobe_config.get("max_filename_chars", 30))
        self.temperature = temperature
        self.top_p = top_p
        self.max_attempts = max_attempts
        self.retry_initial_wait = retry_initial_wait
        self._image_client: Any = None
        self._metadata_client: Any = None
        self.source_root = Path(__file__).resolve().parents[1]

    def _with_retries(self, operation: Callable[[], Any], logger: AssetLogger, step: str) -> Any:
        import main as legacy

        wait_seconds = self.retry_initial_wait
        for attempt in range(1, self.max_attempts + 1):
            try:
                return operation()
            except Exception as exc:
                retry = legacy.is_retryable_error(exc) and attempt < self.max_attempts
                logger.event(
                    f"{step}_attempt_failed", attempt=attempt, error=str(exc),
                    retry_wait_seconds=wait_seconds if retry else None,
                )
                if not retry:
                    raise
                if wait_seconds:
                    time.sleep(wait_seconds)
                wait_seconds *= 2
        raise RuntimeError(f"{step} failed after {self.max_attempts} attempts")

    def _asset_dir(self, asset_id: str) -> Path:
        return self.project_root / "output" / "assets" / asset_id

    def _resource(self, relative: str) -> Path:
        local = self.project_root / relative
        return local if local.exists() else self.source_root / relative

    def _get_image_client(self) -> Any:
        if self.image_generate_bytes is not None:
            return object()
        import main as legacy
        if self._image_client is None:
            mode = legacy.get_google_auth_mode(self.project_root)
            if mode == "adc":
                config = legacy.get_vertex_ai_config(self.project_root)
                self._image_client = legacy.build_google_client(project=config["project"], location=config["location"])
            elif mode == "api_key":
                self._image_client = legacy.build_google_client(api_key=legacy.get_google_api_key(self.project_root))
            else:
                raise ValueError(f"unsupported GOOGLE_AUTH_MODE: {mode}")
        return self._image_client

    def _get_metadata_client(self) -> Any:
        if self.metadata_generate_text is not None:
            return object()
        import main as legacy
        if self._metadata_client is None:
            if self.metadata_provider in {"google", "vertex"}:
                self._metadata_client = self._get_image_client()
            elif self.metadata_provider == "openrouter":
                self._metadata_client = legacy.build_openrouter_metadata_client(legacy.get_openrouter_api_key(self.project_root))
            elif self.metadata_provider == "nim":
                self._metadata_client = legacy.build_nim_metadata_client(legacy.get_nim_api_key(self.project_root))
            else:
                raise ValueError(f"unsupported metadata provider: {self.metadata_provider}")
        return self._metadata_client

    def _manifest(self, asset_id: str) -> None:
        asset = self.registry.get_asset(asset_id)
        fields = (
            "asset_id", "theme", "domain", "topic_score", "topic_bucket",
            "topic_finder_version", "image_prompt_version", "metadata_prompt_version",
            "image_model", "metadata_model", "status",
        )
        manifest = {field: asset[field] for field in fields}
        (self._asset_dir(asset_id) / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def refresh_manifests(self, asset_ids: list[str]) -> None:
        for asset_id in asset_ids:
            self._manifest(asset_id)

    def process_asset(
        self, asset: dict[str, Any], *, force_image: bool = False,
        force_metadata: bool = False, skip_image_generation: bool = False,
        skip_metadata: bool = False,
    ) -> None:
        asset_id = asset["asset_id"]
        asset_dir = self._asset_dir(asset_id)
        asset_dir.mkdir(parents=True, exist_ok=True)
        logger = AssetLogger(asset_dir)
        current = self.registry.get_asset(asset_id)
        if force_image and current["generation_prompt"]:
            self.registry.reset_for_image(asset_id)
            current = self.registry.get_asset(asset_id)

        if force_metadata and current["image_path"]:
            self.registry.reset_for_metadata(asset_id)
            current = self.registry.get_asset(asset_id)

        if current["status"] == "FAILED":
            if current["image_path"] and Path(current["image_path"]).exists():
                self.registry.reset_for_metadata(asset_id)
            elif current["generation_prompt"]:
                self.registry.reset_for_image(asset_id)
            current = self.registry.get_asset(asset_id)
        elif current["status"] == "METADATA_QC_FAILED":
            self.registry.transition(asset_id, "METADATA_PENDING")
            current = self.registry.get_asset(asset_id)
        elif current["status"] == "IMAGE_GENERATING":
            if current["image_path"] and Path(current["image_path"]).exists():
                self.registry.transition(asset_id, "IMAGE_READY")
            else:
                self.registry.reset_for_image(asset_id)
            current = self.registry.get_asset(asset_id)
        elif current["status"] == "STAGED":
            self.registry.transition(asset_id, "READY_TO_UPLOAD")
            current = self.registry.get_asset(asset_id)

        if skip_metadata and current["status"] == "METADATA_PENDING":
            self._manifest(asset_id)
            return

        if current["status"] in {"QUEUED", "FAILED"}:
            started = time.perf_counter()
            prompt = render_image_prompt_file(self._resource("config/prompts/image_prompt_v1.txt"), current)
            (asset_dir / "generation_prompt.txt").write_text(prompt + "\n", encoding="utf-8")
            self.registry.update_asset(
                asset_id, generation_prompt=prompt, image_prompt_version="IMG_V1",
                image_model=self.image_model, metadata_prompt_version="META_V2",
                metadata_model=self.metadata_model,
                upload_filename=map_upload_filename(asset_id, max_chars=self.max_filename_chars),
            )
            self.registry.transition(asset_id, "PROMPT_READY")
            logger.event("prompt_build", duration_seconds=round(time.perf_counter() - started, 3))
            current = self.registry.get_asset(asset_id)

        if skip_image_generation and current["status"] == "PROMPT_READY":
            self._manifest(asset_id)
            return

        if current["status"] == "PROMPT_READY":
            image_client = self._get_image_client()
            self.registry.transition(asset_id, "IMAGE_GENERATING")
            started = time.perf_counter()
            generator = self.image_generate_bytes
            if generator is None:
                import main as legacy
                generator = legacy.generate_image_bytes
            original_path = asset_dir / "original.png"
            self._with_retries(
                lambda: generate_original_image(
                    generate_bytes=generator, client=image_client,
                    prompt=current["generation_prompt"], model=self.image_model,
                    output_path=original_path, temperature=self.temperature, top_p=self.top_p,
                    aspect_ratio=self.aspect_ratio, resolution=self.resolution,
                ),
                logger, "image_generation",
            )
            self.registry.update_asset(asset_id, image_path=str(original_path), generated_at=now_iso())
            self.registry.transition(asset_id, "IMAGE_READY")
            logger.event("image_generation", duration_seconds=round(time.perf_counter() - started, 3))
            current = self.registry.get_asset(asset_id)

        if current["status"] == "IMAGE_READY":
            started = time.perf_counter()
            qc = inspect_image(asset_id, Path(current["image_path"]), self.minimum_resolution)
            (asset_dir / "qc.json").write_text(json.dumps(qc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            logger.event("image_qc", duration_seconds=round(time.perf_counter() - started, 3))
            if not all((qc["image_valid"], qc["square"], qc["resolution_valid"], qc["format_valid"])):
                self.registry.transition(asset_id, "IMAGE_QC_FAILED", error="deterministic image QC failed")
                raise ValueError(f"deterministic image QC failed for {asset_id}")
            self.registry.transition(asset_id, "METADATA_PENDING")
            current = self.registry.get_asset(asset_id)

        if skip_metadata and current["status"] == "METADATA_PENDING":
            self._manifest(asset_id)
            return

        if current["status"] == "METADATA_PENDING":
            metadata_client = self._get_metadata_client()
            category_text = self._resource("category-code.txt").read_text(encoding="utf-8").strip()
            system_prompt = self._resource("config/prompts/metadata_v2.txt").read_text(encoding="utf-8").strip()
            started = time.perf_counter()
            generator = self.metadata_generate_text or generate_metadata_for_asset
            raw = self._with_retries(
                lambda: generator(
                    client=metadata_client, provider=self.metadata_provider,
                    image_path=Path(current["image_path"]), theme=current["theme"],
                    generation_prompt=current["generation_prompt"], category_list=category_text,
                    system_prompt=system_prompt, model=self.metadata_model,
                    temperature=0.2, top_p=0.9, asset_id=asset_id,
                    project_root=self.project_root,
                ),
                logger, "metadata_generation",
            )
            (asset_dir / "metadata_raw.txt").write_text(raw.strip() + "\n", encoding="utf-8")
            logger.event("metadata_generation", duration_seconds=round(time.perf_counter() - started, 3))
            started = time.perf_counter()
            try:
                metadata = validate_metadata(
                    parse_metadata_json(raw), asset_id, parse_categories(category_text),
                    max_title_chars=self.max_title_chars,
                    hard_max_keywords=self.hard_max_keywords,
                )
            except Exception as exc:
                self.registry.transition(asset_id, "METADATA_QC_FAILED", error=str(exc))
                logger.event("metadata_validation_failed", error=str(exc))
                raise
            (asset_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            self.registry.update_asset(
                asset_id, title=metadata["title"], keywords_json=json.dumps(metadata["keywords"]),
                category_code=metadata["category_code"], category_name=metadata["category_name"],
                metadata_generated_at=now_iso(),
            )
            self.registry.transition(asset_id, "METADATA_READY")
            self.registry.transition(asset_id, "READY_TO_STAGE")
            logger.event("metadata_validation", duration_seconds=round(time.perf_counter() - started, 3))
        self._manifest(asset_id)

    def run_queue(
        self, queue: dict[str, Any], *, resume: bool = False,
        force_image: bool = False, force_metadata: bool = False,
        skip_image_generation: bool = False, skip_metadata: bool = False,
        skip_staging: bool = False, batch_id: str | None = None,
    ) -> dict[str, int]:
        queue = validate_topic_queue(queue)
        self.registry.register_queue(queue)
        queue_asset_ids = {asset["asset_id"] for asset in queue["assets"]}
        summary = process_batch(
            self.registry,
            lambda asset: self.process_asset(
                asset, force_image=force_image, force_metadata=force_metadata,
                skip_image_generation=skip_image_generation, skip_metadata=skip_metadata,
            ),
            resume=resume and not (force_image or force_metadata),
            asset_ids=queue_asset_ids,
        )
        for asset in self.registry.list_assets():
            if asset["asset_id"] not in queue_asset_ids:
                continue
            self._manifest(asset["asset_id"])
        ready_for_queue = [
            asset for asset in self.registry.list_assets("READY_TO_STAGE")
            if asset["asset_id"] in queue_asset_ids
        ]
        if not skip_staging and ready_for_queue:
            stage_ready_assets(
                self.registry, batch_id or queue["run_id"].replace("TF_", "BATCH_"),
                self.project_root / "output" / "adobe_batches",
                asset_ids=queue_asset_ids,
            )
        states = [asset for asset in self.registry.list_assets() if asset["asset_id"] in queue_asset_ids]
        summary["ready_to_upload"] = sum(a["status"] in {"READY_TO_UPLOAD", "UPLOADED_DRAFT"} for a in states)
        summary["needs_review"] = sum(a["status"] in {"IMAGE_QC_FAILED", "METADATA_QC_FAILED", "FAILED"} for a in states)
        return summary
