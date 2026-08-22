import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from stock_pipeline.adobe_csv import build_adobe_csv, validate_csv_images
from stock_pipeline.adobe_uploader import build_upload_command, mark_batch_uploaded_draft
from stock_pipeline.contracts import ContractError, validate_topic_queue
from stock_pipeline.filenames import FilenameError, map_upload_filename
from stock_pipeline.image_qc import inspect_image
from stock_pipeline.image_generator import generate_original_image
from stock_pipeline.metadata_generator import build_metadata_user_prompt, parse_metadata_json
from stock_pipeline.metadata_validator import MetadataValidationError, validate_metadata
from stock_pipeline.orchestrator import StockPipeline
from stock_pipeline.pipeline import process_batch
from stock_pipeline.prompt_builder import render_image_prompt
from stock_pipeline.registry import InvalidTransition, Registry
from stock_pipeline.staging import stage_ready_assets


def sample_asset(asset_id="AST000001"):
    return {
        "asset_id": asset_id,
        "domain": "Cybersecurity",
        "theme": "Phishing Awareness",
        "source_type": "EVERGREEN",
        "topic_score": 92,
        "topic_bucket": "APPROVED",
        "icon_1": "Suspicious sender",
        "icon_2": "Malicious attachment",
        "icon_3": "Fake login page",
        "icon_4": "Report phishing",
        "palette": "blue teal",
        "status": "APPROVED",
        "topic_finder_version": "TF_V1",
    }


def sample_queue(*assets):
    return {
        "run_id": "TF_2026W34",
        "topic_finder_version": "TF_V1",
        "generated_at": "2026-08-24T09:00:00+08:00",
        "assets": list(assets or [sample_asset()]),
    }


def sample_metadata(asset_id="AST000001"):
    keywords = [
        "phishing awareness",
        "phishing prevention",
        "cybersecurity",
        "email security",
        "suspicious sender",
        "malicious attachment",
        "fake login page",
        "report phishing",
        "online safety",
        "security training",
        "data protection",
        "internet security",
        "fraud prevention",
        "icon set",
        "flat illustration",
    ]
    return {
        "asset_id": asset_id,
        "title": "Phishing awareness icon set with email security concepts",
        "keywords": keywords,
        "category_code": 8,
        "category_name": "Graphic Resources",
        "keyword_count": len(keywords),
        "metadata_prompt_version": "META_V2",
        "qc": {
            "image_grounded": True,
            "top10_defensible": True,
            "contains_speculation": False,
            "contains_redundancy": False,
            "commercial_ready": True,
        },
    }


class ContractTests(unittest.TestCase):
    def test_valid_queue_is_normalized(self):
        validated = validate_topic_queue(sample_queue())
        self.assertEqual(validated["assets"][0]["asset_id"], "AST000001")

    def test_duplicate_asset_ids_are_rejected(self):
        with self.assertRaisesRegex(ContractError, "duplicate asset_id"):
            validate_topic_queue(sample_queue(sample_asset(), sample_asset()))

    def test_invalid_asset_id_is_rejected(self):
        queue = sample_queue(sample_asset("123"))
        with self.assertRaisesRegex(ContractError, "asset_id"):
            validate_topic_queue(queue)

    def test_queue_schema_rejects_unknown_fields_and_bad_timestamp(self):
        queue = sample_queue()
        queue["unexpected"] = True
        with self.assertRaisesRegex(ContractError, "unexpected"):
            validate_topic_queue(queue)
        queue = sample_queue()
        queue["generated_at"] = "not-a-timestamp"
        with self.assertRaisesRegex(ContractError, "generated_at"):
            validate_topic_queue(queue)

    def test_queue_rejects_unsafe_run_id_and_boolean_score(self):
        queue = sample_queue()
        queue["run_id"] = "../../escape"
        with self.assertRaisesRegex(ContractError, "run_id"):
            validate_topic_queue(queue)
        queue = sample_queue()
        queue["assets"][0]["topic_score"] = True
        with self.assertRaisesRegex(ContractError, "topic_score"):
            validate_topic_queue(queue)


class PromptTests(unittest.TestCase):
    def test_prompt_rendering_is_deterministic_and_complete(self):
        template = "{{THEME}}|{{ICON_1}}|{{ICON_2}}|{{ICON_3}}|{{ICON_4}}|{{PALETTE}}|{{STYLE}}"
        rendered = render_image_prompt(template, sample_asset())
        self.assertEqual(
            rendered,
            "Phishing Awareness|Suspicious sender|Malicious attachment|Fake login page|Report phishing|blue teal|clean 2D flat minimalist commercial illustration",
        )

    def test_prompt_rejects_unresolved_placeholders(self):
        with self.assertRaisesRegex(ValueError, "unresolved"):
            render_image_prompt("{{THEME}} {{UNKNOWN}}", sample_asset())


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.registry = Registry(Path(self.tempdir.name) / "pipeline.sqlite")

    def tearDown(self):
        self.registry.close()
        self.tempdir.cleanup()

    def test_registration_is_idempotent(self):
        queue = sample_queue()
        self.registry.register_queue(queue)
        self.registry.register_queue(queue)
        self.assertEqual(len(self.registry.list_assets()), 1)

    def test_conflicting_duplicate_identity_is_rejected(self):
        self.registry.register_queue(sample_queue())
        changed = sample_asset()
        changed["theme"] = "Different theme"
        with self.assertRaisesRegex(ValueError, "conflicts"):
            self.registry.register_queue(sample_queue(changed))

    def test_conflicting_icon_definition_is_rejected_atomically(self):
        self.registry.register_queue(sample_queue())
        changed = sample_asset()
        changed["icon_4"] = "Different icon"
        with self.assertRaisesRegex(ValueError, "conflicts"):
            self.registry.register_queue(sample_queue(sample_asset("AST000002"), changed))
        self.assertIsNone(self.registry.get_asset("AST000002"))

    def test_asset_id_allocation_is_deterministic(self):
        self.assertEqual(self.registry.allocate_asset_id(), "AST000001")
        self.registry.register_queue(sample_queue())
        self.assertEqual(self.registry.allocate_asset_id(), "AST000002")

    def test_status_transitions_are_explicit(self):
        self.registry.register_queue(sample_queue())
        self.registry.transition("AST000001", "PROMPT_READY")
        self.assertEqual(self.registry.get_asset("AST000001")["status"], "PROMPT_READY")
        with self.assertRaises(InvalidTransition):
            self.registry.transition("AST000001", "UPLOADED_DRAFT")

    def test_performance_fields_can_be_updated(self):
        self.registry.register_queue(sample_queue())
        self.registry.update_performance(
            "AST000001", downloads=3, revenue=1.25, snapshot_date="2026-09-01"
        )
        asset = self.registry.get_asset("AST000001")
        self.assertEqual(asset["downloads"], 3)
        self.assertEqual(asset["revenue"], 1.25)


class ImageQcTests(unittest.TestCase):
    def test_generator_preserves_original_png_and_uses_stock_defaults(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            destination = Path(tmpdir) / "original.png"
            seen = {}

            def fake_generate(**kwargs):
                seen.update(kwargs)
                image_path = Path(tmpdir) / "source.png"
                Image.new("RGB", (32, 32), "white").save(image_path)
                return image_path.read_bytes()

            generate_original_image(
                generate_bytes=fake_generate,
                client=object(),
                prompt="prompt",
                model="model",
                output_path=destination,
            )
            self.assertEqual(seen["aspect_ratio"], "1:1")
            self.assertEqual(seen["resolution"], "2K")
            self.assertTrue(destination.exists())

    def test_valid_square_png_passes_deterministic_qc(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "original.png"
            Image.new("RGB", (2048, 2048), "white").save(path)
            result = inspect_image("AST000001", path, minimum_resolution=2048)
        self.assertTrue(result["image_valid"])
        self.assertTrue(result["square"])
        self.assertTrue(result["resolution_valid"])
        self.assertIsNone(result["exactly_four_icons"])
        self.assertEqual(result["semantic_qc_status"], "NOT_RUN")

    def test_corrupt_image_fails_without_raising(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "original.png"
            path.write_bytes(b"not an image")
            result = inspect_image("AST000001", path)
        self.assertFalse(result["image_valid"])
        self.assertIn("error", result)


class MetadataTests(unittest.TestCase):
    categories = {8: "Graphic Resources", 19: "Technology"}

    def test_user_prompt_contains_hybrid_context(self):
        prompt = build_metadata_user_prompt("Phishing Awareness", "GENERATION", "8. Graphic Resources")
        self.assertIn("Theme:\nPhishing Awareness", prompt)
        self.assertIn("Generation prompt:\nGENERATION", prompt)
        self.assertIn("final image is the primary source of truth", prompt.lower())

    def test_fenced_json_is_parsed(self):
        metadata = sample_metadata()
        parsed = parse_metadata_json("```json\n" + json.dumps(metadata) + "\n```")
        self.assertEqual(parsed["asset_id"], "AST000001")

    def test_valid_metadata_passes(self):
        result = validate_metadata(sample_metadata(), "AST000001", self.categories)
        self.assertEqual(result["keyword_count"], 15)

    def test_title_over_70_is_rejected(self):
        metadata = sample_metadata()
        metadata["title"] = "x" * 71
        with self.assertRaisesRegex(MetadataValidationError, "70"):
            validate_metadata(metadata, "AST000001", self.categories)

    def test_more_than_49_keywords_is_rejected(self):
        metadata = sample_metadata()
        metadata["keywords"] = [f"keyword {i}" for i in range(50)]
        metadata["keyword_count"] = 50
        with self.assertRaisesRegex(MetadataValidationError, "49"):
            validate_metadata(metadata, "AST000001", self.categories)

    def test_empty_keywords_are_rejected(self):
        metadata = sample_metadata()
        metadata["keywords"] = []
        metadata["keyword_count"] = 0
        with self.assertRaisesRegex(MetadataValidationError, "non-empty"):
            validate_metadata(metadata, "AST000001", self.categories)

    def test_normalized_duplicate_keywords_are_rejected(self):
        metadata = sample_metadata()
        metadata["keywords"][1] = " Phishing-Awareness "
        with self.assertRaisesRegex(MetadataValidationError, "duplicate"):
            validate_metadata(metadata, "AST000001", self.categories)

    def test_category_mismatch_is_rejected(self):
        metadata = sample_metadata()
        metadata["category_name"] = "Technology"
        with self.assertRaisesRegex(MetadataValidationError, "category"):
            validate_metadata(metadata, "AST000001", self.categories)

    def test_metadata_schema_rejects_wrong_version_and_malformed_qc(self):
        metadata = sample_metadata()
        metadata["metadata_prompt_version"] = "OLD"
        with self.assertRaisesRegex(MetadataValidationError, "META_V2"):
            validate_metadata(metadata, "AST000001", self.categories)
        metadata = sample_metadata()
        metadata["qc"]["commercial_ready"] = "yes"
        with self.assertRaisesRegex(MetadataValidationError, "qc"):
            validate_metadata(metadata, "AST000001", self.categories)

    def test_metadata_qc_rejects_noncommercial_model_assessment(self):
        metadata = sample_metadata()
        metadata["qc"]["contains_speculation"] = True
        metadata["qc"]["commercial_ready"] = False
        with self.assertRaisesRegex(MetadataValidationError, "commercial"):
            validate_metadata(metadata, "AST000001", self.categories)


class FilenameAndCsvTests(unittest.TestCase):
    def test_filename_is_stable_and_short(self):
        self.assertEqual(map_upload_filename("AST000001"), "AST000001.png")

    def test_filename_length_is_enforced(self):
        with self.assertRaises(FilenameError):
            map_upload_filename("AST000001", extension=".verylongextension", max_chars=12)

    def test_csv_is_built_from_structured_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "adobe.csv"
            build_adobe_csv(
                [{"upload_filename": "AST000001.png", "metadata": sample_metadata()}], path
            )
            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        self.assertEqual(rows[0]["Filename"], "AST000001.png")
        self.assertEqual(rows[0]["Category"], "8")
        self.assertEqual(rows[0]["Releases"], "")

    def test_csv_image_consistency_detects_missing_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            csv_path = root / "adobe.csv"
            build_adobe_csv(
                [{"upload_filename": "AST000001.png", "metadata": sample_metadata()}], csv_path
            )
            with self.assertRaisesRegex(ValueError, "missing"):
                validate_csv_images(csv_path, root)

    def test_upload_command_is_conservative(self):
        command = build_upload_command(
            Path("batch/adobe_stock_metadata.csv"), Path("batch"),
            cdp="http://127.0.0.1:9222", dry_run=True,
        )
        self.assertIn("--file-type", command)
        self.assertIn("illustrations", command)
        self.assertIn("--mark-ai", command)
        self.assertIn("--save-work", command)
        self.assertIn("--dry-run", command)
        self.assertNotIn("--submit", command)

    def test_upload_completion_only_marks_assets_in_that_batch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            registry = Registry(root / "pipeline.sqlite")
            registry.register_queue(sample_queue(sample_asset(), sample_asset("AST000002")))
            registry.set_status_for_test("AST000001", "READY_TO_UPLOAD")
            registry.set_status_for_test("AST000002", "READY_TO_UPLOAD")
            batch = root / "batch"
            batch.mkdir()
            (batch / "batch_manifest.json").write_text(
                json.dumps({"batch_id": "BATCH_1", "asset_ids": ["AST000001"], "count": 1}),
                encoding="utf-8",
            )
            mark_batch_uploaded_draft(registry, batch)
            self.assertEqual(registry.get_asset("AST000001")["status"], "UPLOADED_DRAFT")
            self.assertEqual(registry.get_asset("AST000002")["status"], "READY_TO_UPLOAD")
            registry.close()


class StagingTests(unittest.TestCase):
    def test_only_ready_assets_are_staged_reproducibly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            registry = Registry(root / "pipeline.sqlite")
            registry.register_queue(sample_queue(sample_asset(), sample_asset("AST000002")))
            for asset_id in ("AST000001", "AST000002"):
                asset_dir = root / "assets" / asset_id
                asset_dir.mkdir(parents=True)
                Image.new("RGB", (32, 32), "white").save(asset_dir / "original.png")
                metadata = sample_metadata(asset_id)
                (asset_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
                registry.update_asset(
                    asset_id,
                    image_path=str(asset_dir / "original.png"),
                    upload_filename=f"{asset_id}.png",
                    title=metadata["title"],
                    keywords_json=json.dumps(metadata["keywords"]),
                    category_code=8,
                    category_name="Graphic Resources",
                )
            registry.set_status_for_test("AST000001", "READY_TO_STAGE")
            registry.set_status_for_test("AST000002", "METADATA_PENDING")
            batch_dir = stage_ready_assets(registry, "BATCH_2026W34", root / "batches")
            first_manifest = (batch_dir / "batch_manifest.json").read_text(encoding="utf-8")
            batch_dir = stage_ready_assets(registry, "BATCH_2026W34", root / "batches")
            second_manifest = (batch_dir / "batch_manifest.json").read_text(encoding="utf-8")
            self.assertTrue((batch_dir / "AST000001.png").exists())
            self.assertFalse((batch_dir / "AST000002.png").exists())
            self.assertEqual(first_manifest, second_manifest)
            registry.set_status_for_test("AST000002", "READY_TO_STAGE")
            batch_dir = stage_ready_assets(registry, "BATCH_2026W34", root / "batches")
            manifest = json.loads((batch_dir / "batch_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["asset_ids"], ["AST000001", "AST000002"])
            self.assertTrue((batch_dir / "AST000001.png").exists())
            self.assertTrue((batch_dir / "AST000002.png").exists())
            registry.close()

    def test_unsafe_batch_id_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = Registry(Path(tmpdir) / "pipeline.sqlite")
            with self.assertRaisesRegex(ValueError, "batch_id"):
                stage_ready_assets(registry, "../../escape", Path(tmpdir) / "batches")
            registry.close()


class PipelineIsolationTests(unittest.TestCase):
    def test_failed_asset_does_not_stop_batch_and_resume_skips_completed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = Registry(Path(tmpdir) / "pipeline.sqlite")
            registry.register_queue(sample_queue(sample_asset(), sample_asset("AST000002")))
            calls = []

            def processor(asset):
                calls.append(asset["asset_id"])
                if asset["asset_id"] == "AST000001":
                    raise RuntimeError("boom")
                registry.set_status_for_test(asset["asset_id"], "METADATA_READY")

            summary = process_batch(registry, processor)
            self.assertEqual(summary["total"], 2)
            self.assertEqual(summary["failed"], 1)
            self.assertEqual(calls, ["AST000001", "AST000002"])
            calls.clear()
            process_batch(registry, processor, resume=True)
            self.assertEqual(calls, ["AST000001"])
            registry.close()

    def test_orchestrator_is_idempotent_and_writes_asset_contract(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            registry = Registry(root / "data" / "stock_pipeline.sqlite")
            image_calls = []
            metadata_calls = []

            def generate_image(**kwargs):
                image_calls.append(kwargs["prompt"])
                source = root / "source.png"
                Image.new("RGB", (2048, 2048), "white").save(source)
                return source.read_bytes()

            def generate_metadata(**kwargs):
                metadata_calls.append(kwargs["theme"])
                return json.dumps(sample_metadata(kwargs["asset_id"]))

            pipeline = StockPipeline(
                project_root=root,
                registry=registry,
                image_generate_bytes=generate_image,
                metadata_generate_text=generate_metadata,
                minimum_resolution=2048,
            )
            first = pipeline.run_queue(sample_queue(), skip_staging=True)
            second = pipeline.run_queue(sample_queue(), skip_staging=True, resume=True)
            asset_dir = root / "output" / "assets" / "AST000001"
            self.assertEqual(first["failed"], 0)
            self.assertEqual(second["failed"], 0)
            self.assertEqual(len(image_calls), 1)
            self.assertEqual(len(metadata_calls), 1)
            self.assertEqual(registry.get_asset("AST000001")["status"], "READY_TO_STAGE")
            for filename in ("manifest.json", "generation_prompt.txt", "original.png", "metadata.json", "metadata_raw.txt", "qc.json", "run_log.json"):
                self.assertTrue((asset_dir / filename).exists(), filename)
            registry.close()

    def test_dry_run_stops_after_prompt_without_building_clients(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            registry = Registry(root / "pipeline.sqlite")
            pipeline = StockPipeline(project_root=root, registry=registry)
            summary = pipeline.run_queue(
                sample_queue(), skip_image_generation=True,
                skip_metadata=True, skip_staging=True,
            )
            self.assertEqual(summary["failed"], 0)
            self.assertEqual(registry.get_asset("AST000001")["status"], "PROMPT_READY")
            registry.close()

    def test_retryable_generation_error_is_retried(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            registry = Registry(root / "pipeline.sqlite")
            attempts = []

            def flaky_image(**kwargs):
                attempts.append(1)
                if len(attempts) == 1:
                    raise RuntimeError("429 RESOURCE_EXHAUSTED")
                source = root / "source.png"
                Image.new("RGB", (2048, 2048), "white").save(source)
                return source.read_bytes()

            pipeline = StockPipeline(
                project_root=root, registry=registry,
                image_generate_bytes=flaky_image,
                metadata_generate_text=lambda **kwargs: json.dumps(sample_metadata(kwargs["asset_id"])),
                retry_initial_wait=0,
            )
            summary = pipeline.run_queue(sample_queue(), skip_staging=True)
            self.assertEqual(summary["failed"], 0)
            self.assertEqual(len(attempts), 2)
            registry.close()

    def test_skip_metadata_stops_at_metadata_pending(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            registry = Registry(root / "pipeline.sqlite")

            def generate_image(**kwargs):
                source = root / "source.png"
                Image.new("RGB", (2048, 2048), "white").save(source)
                return source.read_bytes()

            pipeline = StockPipeline(
                project_root=root, registry=registry,
                image_generate_bytes=generate_image,
                metadata_generate_text=lambda **kwargs: self.fail("metadata must be skipped"),
            )
            summary = pipeline.run_queue(sample_queue(), skip_metadata=True, skip_staging=True)
            self.assertEqual(summary["failed"], 0)
            self.assertEqual(registry.get_asset("AST000001")["status"], "METADATA_PENDING")
            registry.close()

    def test_run_only_processes_assets_in_current_queue(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            registry = Registry(root / "pipeline.sqlite")
            registry.register_queue(sample_queue(sample_asset("AST000002")))
            calls = []

            def generate_image(**kwargs):
                calls.append(kwargs["prompt"])
                source = root / "source.png"
                Image.new("RGB", (2048, 2048), "white").save(source)
                return source.read_bytes()

            pipeline = StockPipeline(
                project_root=root, registry=registry,
                image_generate_bytes=generate_image,
                metadata_generate_text=lambda **kwargs: json.dumps(sample_metadata(kwargs["asset_id"])),
            )
            pipeline.run_queue(sample_queue(), skip_staging=True)
            self.assertEqual(len(calls), 1)
            self.assertEqual(registry.get_asset("AST000002")["status"], "QUEUED")
            registry.close()

    def test_metadata_provider_failure_resumes_without_regenerating_image(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            registry = Registry(root / "pipeline.sqlite")
            image_calls = []
            metadata_calls = []

            def generate_image(**kwargs):
                image_calls.append(1)
                source = root / "source.png"
                Image.new("RGB", (2048, 2048), "white").save(source)
                return source.read_bytes()

            def generate_metadata(**kwargs):
                metadata_calls.append(1)
                if len(metadata_calls) == 1:
                    raise RuntimeError("provider unavailable")
                return json.dumps(sample_metadata(kwargs["asset_id"]))

            pipeline = StockPipeline(
                project_root=root, registry=registry,
                image_generate_bytes=generate_image,
                metadata_generate_text=generate_metadata,
            )
            first = pipeline.run_queue(sample_queue(), skip_staging=True)
            second = pipeline.run_queue(sample_queue(), resume=True, skip_staging=True)
            self.assertEqual(first["failed"], 1)
            self.assertEqual(second["failed"], 0)
            self.assertEqual(len(image_calls), 1)
            self.assertEqual(len(metadata_calls), 2)
            self.assertEqual(registry.get_asset("AST000001")["status"], "READY_TO_STAGE")
            registry.close()


if __name__ == "__main__":
    unittest.main()
