"""Offline tests for automatic AST IDs in user-supplied queue JSON files."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stock_pipeline.contracts import ContractError, load_topic_queue
from stock_pipeline.queue_ids import prepare_queue_asset_ids
from stock_pipeline.registry import Registry


def background(theme: str, **overrides):
    asset = {
        "asset_type": "commercial_background",
        "domain": "Commercial Background",
        "theme": theme,
        "source_type": "EVERGREEN_COMMERCIAL",
        "topic_score": 91,
        "topic_bucket": "APPROVED",
        "palette": "sage green, cream, charcoal",
        "status": "APPROVED",
        "topic_finder_version": "commercial_background_v1",
        "prompt": "One commercial service banner with wide clean copy-safe space.",
        "negative_prompt": "text, logos",
        "aspect_ratio": "16:9",
        "resolution": "4K",
        "remove_background": False,
        "research": {"buyer_use_case": "website hero"},
    }
    asset.update(overrides)
    return asset


def queue(*assets, run_id="TF_auto_id_example"):
    return {
        "run_id": run_id,
        "topic_finder_version": "commercial_background_v1",
        "generated_at": "2026-11-05T14:00:00+08:00",
        "research": {},
        "assets": list(assets),
    }


class QueueIdAllocationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "queues" / "incoming" / "new.json"
        self.path.parent.mkdir(parents=True)
        self.database = self.root / "data" / "stock_pipeline.sqlite"

    def write(self, value):
        self.path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def allocate(self):
        return prepare_queue_asset_ids(self.path, self.root, self.database)

    def test_missing_ids_are_persisted_consecutively_and_rerun_is_stable(self):
        self.write(queue(background("Museum catalogue"), background("Rain garden")))
        self.assertEqual(self.allocate(), ["AST000001", "AST000002"])
        full = load_topic_queue(self.path)
        self.assertEqual([asset["asset_id"] for asset in full["assets"]], ["AST000001", "AST000002"])
        previous_bytes = self.path.read_bytes()
        self.assertEqual(self.allocate(), [])
        self.assertEqual(previous_bytes, self.path.read_bytes())
        with Registry(self.database) as registry:
            self.assertEqual(registry.allocate_asset_id(), "AST000003")

    def test_uses_highest_queue_asset_folder_candidate_and_reserved_allocator(self):
        old = self.root / "queues" / "production" / "old.json"
        old.parent.mkdir(parents=True)
        old.write_text(json.dumps(queue(background("Old", asset_id="AST990071"), run_id="TF_old")), encoding="utf-8")
        (self.root / "output" / "assets" / "AST990074").mkdir(parents=True)
        with Registry(self.database) as registry:
            registry.connection.execute(
                "UPDATE id_allocator SET next_number=990073 WHERE singleton=1"
            )
            registry.connection.execute(
                "INSERT INTO candidate_topics (strategy,candidate_id,domain,theme,source_type,topic_score,buyer_use_case,differentiation_reason,similarity_risk,icon_1,icon_2,icon_3,icon_4,palette,finder_run_id,source_filename,imported_at,production_asset_id) "
                "VALUES ('TEST','C1','a','b','EVERGREEN',90,'c','d','LOW','a','b','c','d','blue','TF_old','source.json','today','AST990075')"
            )
            registry.connection.commit()
        self.write(queue(background("New 1"), background("New 2")))
        self.assertEqual(self.allocate(), ["AST990076", "AST990077"])
        with Registry(self.database) as registry:
            self.assertEqual(registry.allocate_asset_id(), "AST990078")

    def test_existing_numbered_json_is_unchanged_and_strict(self):
        self.write(queue(background("Fixed", asset_id="AST990010")))
        before = self.path.read_bytes()
        self.assertEqual(self.allocate(), [])
        self.assertEqual(self.path.read_bytes(), before)
        with Registry(self.database) as registry:
            registry.register_queue(load_topic_queue(self.path))
        self.write(queue(background("Different", asset_id="AST990010"), run_id="TF_new"))
        self.assertEqual(self.allocate(), [])
        with Registry(self.database) as registry, self.assertRaisesRegex(ValueError, "conflicts"):
            registry.register_queue(load_topic_queue(self.path))

    def test_manual_ids_conflicting_with_another_queue_fail_even_without_db_row(self):
        old = self.root / "queues" / "production" / "old.json"
        old.parent.mkdir(parents=True)
        old.write_text(json.dumps(queue(background("Other", asset_id="AST990100"), run_id="TF_other")), encoding="utf-8")
        self.write(queue(background("Manual", asset_id="AST990100")))
        with self.assertRaisesRegex(ContractError, "already appears"):
            self.allocate()
        self.assertFalse(self.database.exists())

    def test_partial_numbering_fails_without_modifying_file(self):
        self.write(queue(background("Manual", asset_id="AST990100"), background("Blank")))
        previous = self.path.read_bytes()
        with self.assertRaisesRegex(ContractError, "mixed asset_id"):
            self.allocate()
        self.assertEqual(self.path.read_bytes(), previous)

    def test_invalid_draft_is_rejected_before_allocator_changes(self):
        self.write(queue(background("Bad", aspect_ratio="1:1")))
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ContractError, "aspect_ratio"):
            self.allocate()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.database.exists())

    def test_existing_run_id_does_not_get_new_ids_if_original_was_lost(self):
        existing = queue(background("One", asset_id="AST000007"))
        with Registry(self.database) as registry:
            registry.register_queue(existing)
        self.write(queue(background("One")))
        with self.assertRaisesRegex(ContractError, "already exists"):
            self.allocate()

    def test_failed_atomic_write_does_not_consume_reservation(self):
        self.write(queue(background("One")))
        with mock.patch("stock_pipeline.queue_ids._write_queue_atomic", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.allocate()
        with Registry(self.database) as registry:
            self.assertEqual(registry.allocate_asset_id(), "AST000001")
        self.assertNotIn("asset_id", json.loads(self.path.read_text())["assets"][0])

    def test_rejects_unreadable_queue_files_for_safe_high_watermark(self):
        self.write(queue(background("New")))
        broken = self.root / "queues" / "incoming" / "broken.json"
        broken.write_text("{oops", encoding="utf-8")
        with self.assertRaisesRegex(ContractError, "cannot scan queue file"):
            self.allocate()


if __name__ == "__main__":
    unittest.main()
