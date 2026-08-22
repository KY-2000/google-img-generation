import csv
import tempfile
import unittest
from pathlib import Path

from stock_pipeline.performance import import_performance_csv
from stock_pipeline.registry import Registry


def sample_queue():
    return {
        "run_id": "TF_2026W34",
        "topic_finder_version": "TF_V1",
        "generated_at": "2026-08-24T09:00:00+08:00",
        "assets": [{
            "asset_id": "AST000001", "domain": "Cybersecurity",
            "theme": "Phishing Awareness", "source_type": "EVERGREEN",
            "topic_score": 92, "topic_bucket": "APPROVED",
            "icon_1": "Suspicious sender", "icon_2": "Malicious attachment",
            "icon_3": "Fake login page", "icon_4": "Report phishing",
            "palette": "blue teal", "status": "APPROVED",
            "topic_finder_version": "TF_V1",
        }],
    }


class PerformanceImportTests(unittest.TestCase):
    def test_import_updates_known_assets_and_rejects_unknown_assets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            registry = Registry(root / "pipeline.sqlite")
            registry.register_queue(sample_queue())
            path = root / "performance.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["asset_id", "status", "accepted_at", "downloads", "revenue", "snapshot_date"])
                writer.writeheader()
                writer.writerow({"asset_id": "AST000001", "status": "ACCEPTED", "accepted_at": "2026-08-31", "downloads": "4", "revenue": "2.50", "snapshot_date": "2026-09-01"})
            self.assertEqual(import_performance_csv(registry, path), 1)
            self.assertEqual(registry.get_asset("AST000001")["downloads"], 4)
            registry.close()
