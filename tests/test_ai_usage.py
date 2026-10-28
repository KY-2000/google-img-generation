import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from stock_pipeline.ai_usage import (
    UsageLedger,
    estimate_cost_usd,
    extract_google_usage,
)


class GoogleUsageTests(unittest.TestCase):
    def test_extracts_provider_reported_token_counts(self):
        response = SimpleNamespace(
            usage_metadata=SimpleNamespace(
                prompt_token_count=1000,
                candidates_token_count=200,
                thoughts_token_count=50,
                cached_content_token_count=100,
                total_token_count=1250,
            )
        )

        self.assertEqual(
            extract_google_usage(response),
            {
                "prompt_tokens": 1000,
                "output_tokens": 200,
                "thoughts_tokens": 50,
                "cached_tokens": 100,
                "total_tokens": 1250,
            },
        )

    def test_estimates_gemini_25_flash_standard_cost(self):
        cost = estimate_cost_usd(
            provider="google",
            model="gemini-2.5-flash",
            operation="metadata_generation",
            usage={
                "prompt_tokens": 1000,
                "output_tokens": 200,
                "thoughts_tokens": 50,
                "cached_tokens": 100,
            },
        )

        self.assertAlmostEqual(cost, 0.000898)

    def test_estimates_4k_gemini_3_pro_image_cost(self):
        cost = estimate_cost_usd(
            provider="google",
            model="gemini-3-pro-image-preview",
            operation="image_generation",
            resolution="4K",
            usage={"prompt_tokens": 500, "thoughts_tokens": 0},
        )

        self.assertAlmostEqual(cost, 0.241)


class UsageLedgerTests(unittest.TestCase):
    def test_persists_each_request_and_command_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            usage_dir = Path(tmpdir) / "output" / "usage"
            printed = []
            ledger = UsageLedger(
                usage_dir=usage_dir,
                command_id="TF_2026W35_20260824T120000Z",
                progress=printed.append,
            )

            ledger.record(
                asset_id="AST000006",
                operation="image_generation",
                provider="google",
                model="gemini-3-pro-image-preview",
                resolution="4K",
                usage={"prompt_tokens": 500, "output_tokens": 2000, "total_tokens": 2500},
            )
            ledger.record(
                asset_id="AST000006",
                operation="metadata_generation",
                provider="google",
                model="gemini-2.5-flash",
                usage={"prompt_tokens": 1000, "output_tokens": 200, "total_tokens": 1200},
            )
            summary = ledger.finalize()

            events = [
                json.loads(line)
                for line in (usage_dir / "ai_usage.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            persisted_summary = json.loads(
                (usage_dir / "commands" / "TF_2026W35_20260824T120000Z.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(len(events), 2)
            self.assertEqual(events[0]["asset_id"], "AST000006")
            self.assertEqual(events[0]["pricing"]["currency"], "USD")
            self.assertAlmostEqual(summary["estimated_cost_usd"], 0.2418)
            self.assertEqual(persisted_summary, summary)
            self.assertTrue(any("AST000006" in line and "image_generation" in line for line in printed))
            self.assertTrue(any("image_generation total" in line for line in printed))
            self.assertTrue(any("metadata_generation total" in line for line in printed))
            self.assertTrue(any("command total" in line for line in printed))

    def test_finalize_persists_and_prints_official_billing_result(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            printed = []
            lookup_calls = []

            def official_lookup(started_at, ended_at):
                lookup_calls.append((started_at, ended_at))
                return {
                    "status": "available",
                    "currency": "MYR",
                    "gross_cost": 4.001458,
                    "credits": -4.001458,
                    "net_cost": 0.0,
                    "scope": "project_vertex_ai_hourly_billing_window",
                }

            ledger = UsageLedger(
                usage_dir=Path(tmpdir),
                command_id="command-1",
                progress=printed.append,
                official_cost_lookup=official_lookup,
                started_at="2026-08-24T07:58:00+00:00",
            )
            ledger.record(
                asset_id="AST000001",
                operation="image_generation",
                provider="vertex",
                model="gemini-3-pro-image-preview",
                resolution="4K",
                usage={"prompt_tokens": 100, "output_tokens": 2000},
            )
            summary = ledger.finalize(ended_at="2026-08-24T08:12:00+00:00")

            self.assertEqual(
                lookup_calls,
                [("2026-08-24T07:58:00+00:00", "2026-08-24T08:12:00+00:00")],
            )
            self.assertEqual(summary["official_billing"]["currency"], "MYR")
            self.assertEqual(summary["official_billing"]["net_cost"], 0.0)
            self.assertTrue(any("official gross=MYR 4.001458" in line for line in printed))
            self.assertTrue(any("official net=MYR 0.000000" in line for line in printed))

    def test_finalize_does_not_query_billing_when_command_made_no_api_requests(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            lookup_calls = []
            ledger = UsageLedger(
                usage_dir=Path(tmpdir),
                command_id="command-1",
                official_cost_lookup=lambda start, end: lookup_calls.append((start, end)),
            )

            summary = ledger.finalize()

            self.assertEqual(lookup_calls, [])
            self.assertEqual(summary["official_billing"]["status"], "not_applicable")


if __name__ == "__main__":
    unittest.main()
