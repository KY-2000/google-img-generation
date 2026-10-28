import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from stock_pipeline.dreamstime_rejections import (
    build_dreamstime_row,
    parse_adobe_rejections,
    prepare_manifest,
    resolve_local_asset,
    stage_manifest_images,
)


class DreamstimeRejectionTests(unittest.TestCase):
    def test_rejections_are_validated_and_filtered_by_cutoff(self):
        records = parse_adobe_rejections([
            {"title": "Recent icon set", "original_name": "AST000001.png",
             "submitted_at": "2026-08-25T00:00:00+00:00", "reason": "Quality"},
            {"title": "Old icon set", "original_name": "AST000002.png",
             "submitted_at": "2026-08-01T00:00:00+00:00", "reason": "Quality"},
        ], since=datetime(2026, 8, 21, tzinfo=timezone.utc))
        self.assertEqual([record.original_name for record in records], ["AST000001.png"])

    def test_local_asset_uses_original_image_and_metadata_keywords(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            asset = root / "output" / "assets" / "AST990001"
            asset.mkdir(parents=True)
            (asset / "original.png").write_bytes(b"png")
            (asset / "metadata.json").write_text(json.dumps({
                "title": "Local title", "keywords": ["phishing", "security", "icons"]
            }), encoding="utf-8")
            resolved = resolve_local_asset(root, "AST990001.png")
            self.assertEqual(resolved.image_path, asset / "original.png")
            self.assertEqual(resolved.keywords, ["phishing", "security", "icons"])

    def test_row_has_requested_license_values_and_categories(self):
        row = build_dreamstime_row(
            filename="AST990001.png", title="Phishing Awareness and Cybersecurity Icons",
            keywords=["phishing", "cybersecurity", "icon set"],
        )
        self.assertEqual(row["Category 1"], 212)
        self.assertEqual(row["Category 2"], 163)
        self.assertEqual(row["Category 3"], 166)
        self.assertEqual(row["Free"], 0)
        self.assertEqual(row["W-EL"], 1)
        self.assertEqual(row["P-EL"], 1)
        self.assertEqual(row["SR-EL"], 1)
        self.assertEqual(row["SR-Price"], 1)
        self.assertEqual(row["Editorial"], 0)
        self.assertEqual(row["MR doc Ids"], 0)
        self.assertEqual(row["Pr Docs"], 0)
        self.assertIn("phishing,cybersecurity,icon set", row["keywords"])

    def test_manifest_reports_missing_local_assets_without_stopping_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            scan = [{
                "title": "Missing", "original_name": "AST999999.png",
                "submitted_at": "2026-09-01T00:00:00+00:00", "reason": "Quality",
            }]
            manifest = prepare_manifest(
                Path(tmp), scan, since=datetime(2026, 8, 21, tzinfo=timezone.utc)
            )
            self.assertEqual(manifest["count"], 0)
            self.assertEqual(manifest["unresolved"][0]["original_name"], "AST999999.png")

    def test_manifest_images_are_staged_with_unique_ast_filenames(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "original.png"
            source.write_bytes(b"image")
            manifest = {"items": [{
                "asset_id": "AST000001", "image_path": str(source),
                "spreadsheet_row": {"Filename": "AST000001.png"},
            }]}
            stage_manifest_images(manifest, root / "staged")
            staged = Path(manifest["items"][0]["image_path"])
            self.assertEqual(staged.name, "AST000001.png")
            self.assertEqual(staged.read_bytes(), b"image")


if __name__ == "__main__":
    unittest.main()
