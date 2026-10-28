import json
import tempfile
import unittest
from pathlib import Path

from stock_pipeline.candidate_import import CandidateContractError, STRATEGIES, load_finder_candidates
from stock_pipeline.registry import Registry


def candidate(candidate_id="RG_001", strategy="REGIONAL_COMMERCIAL", **changes):
    value = {
        "candidate_id": candidate_id, "strategy": strategy,
        "domain": "Small Business Tax", "region": "Malaysia",
        "theme": "Receipt to e-Invoice", "source_type": "EMERGING",
        "topic_score": 96, "buyer_use_case": "Editorial icon buyers",
        "differentiation_reason": "Regional workflow", "similarity_risk": "LOW",
        "icon_1": "receipt", "icon_2": "invoice", "icon_3": "request",
        "icon_4": "approval", "palette": "blue green", "status": "CANDIDATE",
    }
    value.update(changes)
    return value


class CandidateContractTests(unittest.TestCase):
    def load(self, value):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidates.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            return load_finder_candidates(path)

    def test_valid_array_is_normalized_and_preserves_optional_research(self):
        result = self.load([candidate(candidate_id=" RG_001 ", theme="  Receipt   workflow ")])
        self.assertEqual(result[0]["candidate_id"], "RG_001")
        self.assertEqual(result[0]["theme"], "Receipt workflow")
        self.assertEqual(result[0]["region"], "Malaysia")

    def test_object_duplicate_unknown_strategy_bad_risk_and_missing_are_rejected(self):
        cases = [
            (candidate(), "top-level JSON array"),
            ([candidate(), candidate()], "duplicate candidate identity"),
            ([candidate(strategy="NOPE")], "strategy"),
            ([candidate(similarity_risk="NOPE")], "similarity_risk"),
            ([{k: v for k, v in candidate().items() if k != "theme"}], "theme"),
            ([candidate(extra=True)], "unknown"),
        ]
        for value, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(CandidateContractError, message):
                self.load(value)

    def test_all_eight_finder_strategies_are_supported(self):
        values = [candidate(f"C_{index}", strategy) for index, strategy in enumerate(sorted(STRATEGIES))]
        self.assertEqual({item["strategy"] for item in self.load(values)}, STRATEGIES)


class CandidateRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.registry = Registry(Path(self.tmp.name) / "pipeline.sqlite")

    def tearDown(self):
        self.registry.close()
        self.tmp.cleanup()

    def test_import_and_allocation_are_idempotent_and_preserve_lineage(self):
        item = candidate()
        self.registry.import_candidate(item, finder_run_id="TF_REGIONAL_20260828", source_filename="one.json")
        first = self.registry.allocate_candidate_asset_id("REGIONAL_COMMERCIAL", "RG_001")
        self.registry.import_candidate(item, finder_run_id="TF_REGIONAL_20260828", source_filename="two.json")
        second = self.registry.allocate_candidate_asset_id("REGIONAL_COMMERCIAL", "RG_001")
        row = self.registry.get_candidate("REGIONAL_COMMERCIAL", "RG_001")
        self.assertEqual(first, "AST000001")
        self.assertEqual(second, first)
        self.assertEqual(row["production_asset_id"], first)
        self.assertEqual(row["buyer_use_case"], "Editorial icon buyers")
        self.assertEqual(row["source_filename"], "two.json")

    def test_allocation_is_unique_across_strategies_and_existing_assets(self):
        from tests.test_stock_pipeline import sample_queue
        self.registry.register_queue(sample_queue())
        for item in (candidate(), candidate("RG_001", "ROLE_SPECIFIC", region=None, target_role="Accountant")):
            self.registry.import_candidate(item, finder_run_id="TF_TEST", source_filename="x.json")
        ids = [
            self.registry.allocate_candidate_asset_id("REGIONAL_COMMERCIAL", "RG_001"),
            self.registry.allocate_candidate_asset_id("ROLE_SPECIFIC", "RG_001"),
        ]
        self.assertEqual(ids, ["AST000002", "AST000003"])


if __name__ == "__main__":
    unittest.main()
