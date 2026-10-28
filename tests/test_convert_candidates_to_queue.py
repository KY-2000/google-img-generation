import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfoNotFoundError

import stock_pipeline.candidate_conversion as candidate_conversion
from stock_pipeline.candidate_conversion import convert_candidates
from stock_pipeline.contracts import ContractError, validate_topic_queue
from tests.test_candidate_import import candidate


class CandidateConversionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / "regional_commercial_20260828.json"
        self.database = self.root / "pipeline.sqlite"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, values):
        self.source.write_text(json.dumps(values), encoding="utf-8")

    def test_existing_production_queue_contract_still_rejects_candidate_arrays(self):
        with self.assertRaisesRegex(ContractError, "JSON object"):
            validate_topic_queue([candidate()])

    def test_default_mapping_is_strict_and_repeat_conversion_keeps_ids(self):
        self.write([candidate(), candidate("RG_002", topic_score=70, similarity_risk="HIGH")])
        first = convert_candidates(self.source, self.database)
        second = convert_candidates(self.source, self.database)
        validate_topic_queue(first)
        self.assertEqual([a["asset_id"] for a in first["assets"]], ["AST000001", "AST000002"])
        self.assertEqual([a["asset_id"] for a in second["assets"]], ["AST000001", "AST000002"])
        self.assertEqual(first["run_id"], "TF_REGIONAL_COMMERCIAL_20260828")
        self.assertEqual(first["topic_finder_version"], "TF_V1_1")
        self.assertEqual(first["assets"][0]["status"], "APPROVED")
        self.assertEqual(first["assets"][0]["topic_bucket"], "APPROVED")
        self.assertFalse({"candidate_id", "strategy", "buyer_use_case", "similarity_risk"} & set(first["assets"][0]))

    def test_filters_are_explicit_and_empty_selection_fails(self):
        self.write([candidate(), candidate("RG_002", topic_score=70, similarity_risk="HIGH")])
        queue = convert_candidates(self.source, self.database, min_score=80, include_risk=("LOW",))
        self.assertEqual(len(queue["assets"]), 1)
        with self.assertRaisesRegex(ValueError, "no candidates"):
            convert_candidates(self.source, self.database, strategy="ROLE_SPECIFIC")

    def test_multiple_strategy_files_share_allocator_without_identity_collisions(self):
        self.write([candidate("C_001", "REGIONAL_COMMERCIAL")])
        regional = convert_candidates(self.source, self.database)
        other = self.root / "workflow_process_20260828.json"
        other.write_text(json.dumps([candidate("C_001", "WORKFLOW_PROCESS", region=None)]), encoding="utf-8")
        workflow = convert_candidates(other, self.database)
        self.assertEqual(regional["assets"][0]["asset_id"], "AST000001")
        self.assertEqual(workflow["assets"][0]["asset_id"], "AST000002")

    def test_missing_system_tzdata_falls_back_to_utc(self):
        self.write([candidate()])
        with mock.patch.object(
            candidate_conversion, "ZoneInfo",
            side_effect=ZoneInfoNotFoundError("Asia/Kuala_Lumpur"),
        ):
            queue = convert_candidates(self.source, self.database)
        self.assertTrue(queue["generated_at"].endswith("+00:00"))

    def test_cli_writes_only_valid_final_output(self):
        self.write([candidate()])
        output = self.root / "nested" / "queue.json"
        tool = Path(__file__).parents[1] / "tools" / "convert_candidates_to_queue.py"
        result = subprocess.run(
            [sys.executable, str(tool), "--input", str(self.source), "--output", str(output),
             "--database", str(self.database), "--run-id", "TF_EXPLICIT"],
            text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["run_id"], "TF_EXPLICIT")
        output.write_text("unchanged", encoding="utf-8")
        self.write([candidate(extra=True)])
        result = subprocess.run(
            [sys.executable, str(tool), "--input", str(self.source), "--output", str(output),
             "--database", str(self.database)], text=True, capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output.read_text(encoding="utf-8"), "unchanged")


if __name__ == "__main__":
    unittest.main()
