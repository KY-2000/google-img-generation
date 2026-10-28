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
from stock_pipeline.metadata_validator import (
    MetadataValidationError,
    enrich_metadata,
    validate_metadata,
)
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
        "email protection",
        "security awareness",
        "threat detection",
        "digital fraud",
        "identity theft",
        "credential security",
        "cyber attack",
        "spam email",
        "scam prevention",
        "account protection",
        "privacy protection",
        "network security",
        "information security",
        "security education",
        "employee training",
        "business security",
        "warning symbol",
        "security icons",
        "vector style",
        "isolated icons",
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


def sample_model_metadata():
    metadata = sample_metadata()
    for field in ("asset_id", "keyword_count", "metadata_prompt_version"):
        metadata.pop(field)
    return metadata


class CommercialBackgroundTests(unittest.TestCase):
    def background_queue(self, ratio="16:9"):
        asset = sample_asset()
        for field in ("icon_1", "icon_2", "icon_3", "icon_4"):
            asset.pop(field)
        asset.update(asset_type="commercial_background", prompt="One full-frame winter illustration.",
                     negative_prompt="text, watermark", aspect_ratio=ratio,
                     resolution="4K", remove_background=False, research={"original_asset_id": "SCB-001"})
        return sample_queue(asset)

    def test_background_contract_and_invalid_settings(self):
        queue = self.background_queue()
        self.assertEqual(validate_topic_queue(queue), queue)
        for field, value in (("asset_type", "unknown"), ("aspect_ratio", "1:1"),
                             ("resolution", "2K"), ("remove_background", True), ("prompt", " ")):
            invalid = self.background_queue()
            invalid["assets"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ContractError):
                validate_topic_queue(invalid)

    def test_direct_prompt_does_not_use_icon_template(self):
        asset = self.background_queue()["assets"][0]
        self.assertEqual(render_image_prompt("FOUR ICON TEMPLATE", asset),
                         asset["prompt"] + "\n\nNegative prompt:\n" + asset["negative_prompt"])

    def test_background_generation_resume_staging_and_draft(self):
        import io
        from unittest.mock import patch
        for ratio, size in (("16:9", (160, 90)), ("9:16", (90, 160))):
            with self.subTest(ratio=ratio), tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                queue = self.background_queue(ratio)
                calls = []
                def generate(**kwargs):
                    calls.append(kwargs)
                    buffer = io.BytesIO()
                    Image.new("RGB", size, "ivory").save(buffer, format="PNG")
                    return buffer.getvalue()
                with Registry(root / "registry.sqlite") as registry:
                    pipeline = StockPipeline(project_root=root, registry=registry,
                        image_generate_bytes=generate,
                        metadata_generate_text=lambda **kw: json.dumps(sample_model_metadata()),
                        minimum_resolution=160)
                    with patch("main.remove_background_ffmpeg", side_effect=AssertionError("must not remove background")):
                        self.assertEqual(pipeline.run_queue(queue)["failed"], 0)
                    self.assertEqual(calls[0]["aspect_ratio"], ratio)
                    self.assertEqual(calls[0]["resolution"], "4K")
                    self.assertIn("Negative prompt:\ntext, watermark", calls[0]["prompt"])
                    self.assertNotIn("2x2", calls[0]["prompt"])
                    self.assertEqual(registry.get_asset("AST000001")["status"], "READY_TO_UPLOAD")
                # Reopen the DB to verify settings and completion survive a new process.
                with Registry(root / "registry.sqlite") as registry:
                    pipeline = StockPipeline(project_root=root, registry=registry,
                        image_generate_bytes=generate, minimum_resolution=160)
                    self.assertEqual(pipeline.run_queue(queue, resume=True)["failed"], 0)
                    self.assertEqual(len(calls), 1)
                    mark_batch_uploaded_draft(registry, root / "output/adobe_batches/BATCH_2026W34")
                    self.assertEqual(pipeline.run_queue(queue, resume=True)["failed"], 0)
                    self.assertEqual(len(calls), 1)
                    changed = self.background_queue(ratio)
                    changed["assets"][0]["prompt"] = "A different image"
                    with self.assertRaisesRegex(ValueError, "conflicts"):
                        registry.register_queue(changed)

    def test_background_qc_rejects_wrong_shape(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "image.png"
            Image.new("RGB", (160, 160)).save(path)
            qc = inspect_image("AST000001", path, 160, aspect_ratio="16:9")
            self.assertFalse(qc["aspect_ratio_valid"])

    def test_small_background_is_blocked_on_first_run_and_resume(self):
        import io
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            buffer = io.BytesIO()
            Image.new("RGB", (160, 90)).save(buffer, format="PNG")
            image = Mock(return_value=buffer.getvalue())
            metadata = Mock(side_effect=AssertionError("QC must block metadata"))
            with Registry(root / "registry.sqlite") as registry:
                pipeline = StockPipeline(project_root=root, registry=registry,
                    image_generate_bytes=image, metadata_generate_text=metadata,
                    minimum_resolution=4096)
                queue = self.background_queue()
                self.assertEqual(pipeline.run_queue(queue)["failed"], 1)
                self.assertEqual(registry.get_asset("AST000001")["status"], "IMAGE_QC_FAILED")
                self.assertIn("160x90", registry.get_asset("AST000001")["error"])
                self.assertEqual(pipeline.run_queue(queue, resume=True)["failed"], 1)
                self.assertEqual(image.call_count, 1)
                metadata.assert_not_called()
                # Also recover the FAILED state left by the former invalid transition.
                registry.mark_failed("AST000001", "invalid status transition IMAGE_READY -> IMAGE_QC_FAILED")
                self.assertEqual(pipeline.run_queue(queue, resume=True)["failed"], 1)
                self.assertEqual(registry.get_asset("AST000001")["status"], "IMAGE_QC_FAILED")
                self.assertEqual(image.call_count, 1)
                metadata.assert_not_called()

    def test_partial_resume_reuses_image_after_metadata_failure(self):
        import io
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            queue = self.background_queue()
            calls = []
            def generate(**kwargs):
                calls.append(kwargs)
                buffer = io.BytesIO()
                Image.new("RGB", (160, 90)).save(buffer, format="PNG")
                return buffer.getvalue()
            def fail_metadata(**kwargs):
                raise ValueError("offline metadata failure")
            with Registry(root / "registry.sqlite") as registry:
                pipeline = StockPipeline(project_root=root, registry=registry)
                pipeline.run_queue(queue, skip_image_generation=True, skip_staging=True)
                self.assertEqual(registry.get_asset("AST000001")["status"], "PROMPT_READY")
            with Registry(root / "registry.sqlite") as registry:
                pipeline = StockPipeline(project_root=root, registry=registry,
                    image_generate_bytes=generate, metadata_generate_text=fail_metadata,
                    minimum_resolution=160)
                self.assertEqual(pipeline.run_queue(queue, resume=True)["failed"], 1)
            with Registry(root / "registry.sqlite") as registry:
                pipeline = StockPipeline(project_root=root, registry=registry,
                    image_generate_bytes=generate,
                    metadata_generate_text=lambda **kw: json.dumps(sample_model_metadata()),
                    minimum_resolution=160)
                self.assertEqual(pipeline.run_queue(queue, resume=True)["failed"], 0)
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0]["aspect_ratio"], "16:9")

    def test_old_registry_is_migrated_and_explicit_icon_type_is_equivalent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "registry.sqlite"
            with Registry(path) as registry:
                registry.register_queue(sample_queue())
                registry.connection.execute("ALTER TABLE assets DROP COLUMN asset_config_json")
                registry.connection.commit()
            with Registry(path) as registry:
                explicit = sample_queue()
                explicit["assets"][0]["asset_type"] = "icon_set"
                registry.register_queue(explicit)
                self.assertEqual(registry.get_asset("AST000001")["status"], "QUEUED")
                self.assertEqual(registry.get_asset("AST000001")["asset_config_json"], "{}")

    def test_final_commercial_queue_validates(self):
        from stock_pipeline.contracts import load_topic_queue
        root = Path(__file__).resolve().parents[1]
        queue = load_topic_queue(root / "queues/incoming/commercial_background_queue_2026W44.json")
        self.assertEqual(len(queue["assets"]), 8)
        for asset in queue["assets"]:
            self.assertEqual(asset["asset_type"], "commercial_background")
            self.assertIn(asset["aspect_ratio"], asset["prompt"])


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

    def canonicalize(self, model_output, asset_id="AST000001"):
        enriched = enrich_metadata(model_output, asset_id)
        return validate_metadata(enriched, asset_id, self.categories)

    def test_user_prompt_contains_hybrid_context(self):
        prompt = build_metadata_user_prompt("Phishing Awareness", "GENERATION", "8. Graphic Resources")
        self.assertIn("Theme:\nPhishing Awareness", prompt)
        self.assertIn("Generation prompt:\nGENERATION", prompt)
        self.assertIn("final image is the primary source of truth", prompt.lower())

    def test_metadata_v2_prompt_defines_exact_model_output_contract(self):
        prompt = (
            Path(__file__).resolve().parents[1] / "config" / "prompts" / "metadata_v2.txt"
        ).read_text(encoding="utf-8")
        for field in (
            '"title"', '"keywords"', '"category_code"', '"category_name"',
            '"qc"', '"image_grounded"', '"top10_defensible"',
            '"contains_speculation"', '"contains_redundancy"', '"commercial_ready"',
        ):
            with self.subTest(field=field):
                self.assertIn(field, prompt)
        for requirement in (
            "valid JSON only", "no markdown fences", "no explanatory prose",
            "70 characters", "ordered by relevance", "35-49",
            "supplied category list", "boolean",
        ):
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, prompt)
        for pipeline_field in ("asset_id", "keyword_count", "metadata_prompt_version"):
            with self.subTest(pipeline_field=pipeline_field):
                self.assertNotIn(f'"{pipeline_field}"', prompt)

    def test_fenced_json_is_parsed(self):
        metadata = sample_metadata()
        parsed = parse_metadata_json("```json\n" + json.dumps(metadata) + "\n```")
        self.assertEqual(parsed["asset_id"], "AST000001")

    def test_valid_metadata_passes(self):
        result = validate_metadata(sample_metadata(), "AST000001", self.categories)
        self.assertEqual(result["keyword_count"], 35)

    def test_model_output_is_enriched_with_deterministic_pipeline_fields(self):
        result = self.canonicalize(sample_model_metadata(), "AST000777")
        self.assertEqual(result["asset_id"], "AST000777")
        self.assertEqual(result["keyword_count"], len(result["keywords"]))
        self.assertEqual(result["metadata_prompt_version"], "META_V2")

    def test_model_output_missing_required_semantic_fields_is_rejected(self):
        for field in ("title", "keywords", "category_code", "category_name"):
            with self.subTest(field=field):
                metadata = sample_model_metadata()
                metadata.pop(field)
                with self.assertRaisesRegex(MetadataValidationError, field):
                    self.canonicalize(metadata)

    def test_model_output_with_malformed_qc_is_rejected(self):
        metadata = sample_model_metadata()
        metadata["qc"]["commercial_ready"] = "yes"
        with self.assertRaisesRegex(MetadataValidationError, "qc"):
            self.canonicalize(metadata)

    def test_model_output_with_invalid_category_mismatch_is_rejected(self):
        metadata = sample_model_metadata()
        metadata["category_name"] = "Technology"
        with self.assertRaisesRegex(MetadataValidationError, "category"):
            self.canonicalize(metadata)

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

    def test_fewer_than_35_keywords_is_rejected(self):
        metadata = sample_metadata()
        metadata["keywords"] = [f"keyword {i}" for i in range(34)]
        metadata["keyword_count"] = 34
        with self.assertRaisesRegex(MetadataValidationError, "minimum of 35"):
            validate_metadata(metadata, "AST000001", self.categories)

    def test_canonical_schema_requires_35_to_49_keywords(self):
        schema = json.loads(
            (Path(__file__).resolve().parents[1] / "schemas" / "metadata.schema.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(schema["properties"]["keywords"]["minItems"], 35)
        self.assertEqual(schema["properties"]["keywords"]["maxItems"], 49)
        self.assertEqual(schema["properties"]["keyword_count"]["minimum"], 35)

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
            cdp="http://127.0.0.1:9222", resume_after=3, dry_run=True,
        )
        self.assertIn("--file-type", command)
        self.assertIn("illustrations", command)
        self.assertIn("--mark-ai", command)
        self.assertIn("--save-work", command)
        self.assertIn("--dry-run", command)
        self.assertEqual(command[command.index("--resume-after") + 1], "3")
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
            progress_messages = []

            def generate_image(**kwargs):
                image_calls.append(kwargs["prompt"])
                source = root / "source.png"
                Image.new("RGB", (2048, 2048), "white").save(source)
                return source.read_bytes()

            def generate_metadata(**kwargs):
                metadata_calls.append(kwargs["theme"])
                return " \n" + json.dumps(sample_model_metadata()) + "\n\n"

            pipeline = StockPipeline(
                project_root=root,
                registry=registry,
                image_generate_bytes=generate_image,
                metadata_generate_text=generate_metadata,
                minimum_resolution=2048,
            )
            pipeline.progress = progress_messages.append
            first = pipeline.run_queue(sample_queue(), skip_staging=True)
            second = pipeline.run_queue(sample_queue(), skip_staging=True, resume=True)
            asset_dir = root / "output" / "assets" / "AST000001"
            self.assertEqual(first["failed"], 0)
            self.assertEqual(second["failed"], 0)
            self.assertEqual(len(image_calls), 1)
            self.assertEqual(len(metadata_calls), 1)
            self.assertTrue(any("prompt 1/1 started" in message for message in progress_messages))
            self.assertTrue(any("prompt 1/1 completed" in message for message in progress_messages))
            self.assertTrue(any("image 1/1 started" in message for message in progress_messages))
            self.assertTrue(any("image 1/1 completed" in message for message in progress_messages))
            self.assertTrue(any("metadata prompt 1/1 started" in message for message in progress_messages))
            self.assertTrue(any("metadata prompt 1/1 completed" in message for message in progress_messages))
            self.assertTrue(any("ready to stage" in message for message in progress_messages))
            self.assertEqual(registry.get_asset("AST000001")["status"], "READY_TO_STAGE")
            expected_raw = " \n" + json.dumps(sample_model_metadata()) + "\n\n"
            self.assertEqual(
                (asset_dir / "metadata_raw.txt").read_bytes(),
                expected_raw.encode("utf-8"),
            )
            persisted = json.loads((asset_dir / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["asset_id"], "AST000001")
            self.assertEqual(persisted["keyword_count"], len(persisted["keywords"]))
            self.assertEqual(persisted["metadata_prompt_version"], "META_V2")
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

    def test_orchestrator_records_image_and_metadata_api_usage_per_asset(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            registry = Registry(root / "pipeline.sqlite")
            usage_events = []

            def generate_image(**kwargs):
                kwargs["usage_callback"](
                    {"prompt_tokens": 500, "output_tokens": 2000, "total_tokens": 2500}
                )
                source = root / "source.png"
                Image.new("RGB", (2048, 2048), "white").save(source)
                return source.read_bytes()

            def generate_metadata(**kwargs):
                kwargs["usage_callback"](
                    {"prompt_tokens": 1000, "output_tokens": 200, "total_tokens": 1200}
                )
                return json.dumps(sample_model_metadata())

            pipeline = StockPipeline(
                project_root=root,
                registry=registry,
                image_generate_bytes=generate_image,
                metadata_generate_text=generate_metadata,
                usage_recorder=lambda **event: usage_events.append(event),
            )

            summary = pipeline.run_queue(sample_queue(), skip_staging=True)

            self.assertEqual(summary["failed"], 0)
            self.assertEqual(
                [event["operation"] for event in usage_events],
                ["image_generation", "metadata_generation"],
            )
            self.assertEqual([event["asset_id"] for event in usage_events], ["AST000001"] * 2)
            self.assertEqual(usage_events[0]["resolution"], "2K")
            self.assertEqual(usage_events[1]["provider"], "google")
            registry.close()

    def test_retryable_generation_error_is_retried(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            registry = Registry(root / "pipeline.sqlite")
            attempts = []
            progress_messages = []

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
                metadata_generate_text=lambda **kwargs: json.dumps(sample_model_metadata()),
                retry_initial_wait=0,
            )
            pipeline.progress = progress_messages.append
            summary = pipeline.run_queue(sample_queue(), skip_staging=True)
            self.assertEqual(summary["failed"], 0)
            self.assertEqual(len(attempts), 2)
            self.assertTrue(any(
                "image generation attempt 1 failed: 429 RESOURCE_EXHAUSTED" in message
                for message in progress_messages
            ))
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
                metadata_generate_text=lambda **kwargs: json.dumps(sample_model_metadata()),
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
                return json.dumps(sample_model_metadata())

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

    def test_smoke_assets_resume_metadata_from_existing_images_without_regeneration(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            registry = Registry(root / "pipeline.sqlite")
            assets = [sample_asset(f"AST99000{number}") for number in range(1, 4)]
            queue = sample_queue(*assets)
            registry.register_queue(queue)
            for asset in assets:
                asset_id = asset["asset_id"]
                image_path = root / "existing_images" / asset_id / "original.png"
                image_path.parent.mkdir(parents=True)
                Image.new("RGB", (32, 32), "white").save(image_path)
                registry.update_asset(
                    asset_id,
                    image_path=str(image_path),
                    generation_prompt=f"existing prompt for {asset_id}",
                    upload_filename=f"{asset_id}.png",
                )
                registry.set_status_for_test(asset_id, "METADATA_QC_FAILED")

            metadata_calls = []

            def generate_metadata(**kwargs):
                metadata_calls.append(kwargs["asset_id"])
                return json.dumps(sample_model_metadata())

            pipeline = StockPipeline(
                project_root=root,
                registry=registry,
                image_generate_bytes=lambda **kwargs: self.fail("existing images must not be regenerated"),
                metadata_generate_text=generate_metadata,
            )
            summary = pipeline.run_queue(queue, resume=True)

            self.assertEqual(summary["failed"], 0)
            self.assertEqual(metadata_calls, ["AST990001", "AST990002", "AST990003"])
            self.assertEqual(
                [registry.get_asset(asset["asset_id"])["status"] for asset in assets],
                ["READY_TO_UPLOAD", "READY_TO_UPLOAD", "READY_TO_UPLOAD"],
            )
            registry.close()


if __name__ == "__main__":
    unittest.main()
