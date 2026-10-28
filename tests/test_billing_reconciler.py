import json
import unittest
from types import SimpleNamespace

from stock_pipeline.billing_reconciler import (
    BigQueryBillingReconciler,
    reconcile_usage_directory,
    reconcile_summary_file,
    resolve_bq_command,
)


TABLE = (
    "adobe-stock-img.billing_export."
    "gcp_billing_export_v1_014122_B8FF5F_35AD2B"
)


class BigQueryBillingReconcilerTests(unittest.TestCase):
    def test_resolves_full_bq_wrapper_path_for_windows_subprocess(self):
        self.assertEqual(
            resolve_bq_command(lambda name: "C:/Cloud SDK/bin/bq.CMD"),
            "C:/Cloud SDK/bin/bq.CMD",
        )

    def test_returns_official_cost_credits_and_currency(self):
        calls = []

        def runner(command, **kwargs):
            calls.append(command)
            return SimpleNamespace(
                stdout=json.dumps(
                    [
                        {
                            "currency": "MYR",
                            "gross_cost": "4.001458",
                            "credits": "-4.001458",
                            "net_cost": "0.0",
                            "first_usage": "2026-08-24 07:00:00+00",
                            "last_usage": "2026-08-24 09:00:00+00",
                            "skus": ["Gemini image output", "Gemini text input"],
                        }
                    ]
                ),
                stderr="",
                returncode=0,
            )

        reconciler = BigQueryBillingReconciler(
            table_path=TABLE,
            project_id="adobe-stock-img",
            runner=runner,
        )
        result = reconciler.lookup(
            "2026-08-24T07:58:00+00:00",
            "2026-08-24T08:12:00+00:00",
        )

        self.assertEqual(result["status"], "available")
        self.assertEqual(result["currency"], "MYR")
        self.assertAlmostEqual(result["gross_cost"], 4.001458)
        self.assertAlmostEqual(result["credits"], -4.001458)
        self.assertEqual(result["net_cost"], 0.0)
        self.assertEqual(result["scope"], "project_vertex_ai_hourly_billing_window")
        command = calls[0]
        self.assertTrue(command[0].lower().endswith(("bq", "bq.cmd")))
        self.assertNotIn("\n", command[-1])
        self.assertTrue(any("start_time:TIMESTAMP:2026-08-24T07:58:00+00:00" in arg for arg in command))
        self.assertIn(f"`{TABLE}`", command[-1])

    def test_returns_pending_when_billing_rows_have_not_arrived(self):
        def runner(command, **kwargs):
            return SimpleNamespace(stdout="[]", stderr="", returncode=0)

        result = BigQueryBillingReconciler(
            table_path=TABLE,
            project_id="adobe-stock-img",
            runner=runner,
        ).lookup("2026-08-26T01:00:00+00:00", "2026-08-26T01:10:00+00:00")

        self.assertEqual(result["status"], "pending")
        self.assertIn("billing export", result["reason"])

    def test_rejects_unsafe_table_path(self):
        with self.assertRaisesRegex(ValueError, "billing table path"):
            BigQueryBillingReconciler(
                table_path="table`; DROP TABLE x",
                project_id="adobe-stock-img",
            )

    def test_reconciles_and_updates_a_saved_command_summary(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "command.json"
            path.write_text(
                json.dumps(
                    {
                        "command_id": "command-1",
                        "started_at": "2026-08-24T07:58:00+00:00",
                        "ended_at": "2026-08-24T08:12:00+00:00",
                        "request_count": 8,
                        "official_billing": {"status": "pending"},
                    }
                ),
                encoding="utf-8",
            )

            result = reconcile_summary_file(
                path,
                lambda start, end: {
                    "status": "available",
                    "currency": "MYR",
                    "gross_cost": 4.001458,
                    "credits": -4.001458,
                    "net_cost": 0.0,
                },
            )

            persisted = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "available")
            self.assertEqual(persisted["official_billing"], result)
            self.assertIn("official_reconciled_at", persisted)

    def test_reconciles_pending_command_summaries_in_directory(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            command_dir = Path(tmpdir)
            pending = command_dir / "pending.json"
            complete = command_dir / "complete.json"
            pending.write_text(
                json.dumps(
                    {
                        "command_id": "pending",
                        "started_at": "2026-08-24T07:58:00+00:00",
                        "ended_at": "2026-08-24T08:12:00+00:00",
                        "request_count": 8,
                        "official_billing": {"status": "pending"},
                    }
                ),
                encoding="utf-8",
            )
            complete.write_text(
                json.dumps(
                    {
                        "command_id": "complete",
                        "started_at": "2026-08-23T16:00:00+00:00",
                        "ended_at": "2026-08-23T17:00:00+00:00",
                        "request_count": 6,
                        "official_billing": {"status": "available"},
                    }
                ),
                encoding="utf-8",
            )
            calls = []

            summary = reconcile_usage_directory(
                command_dir,
                lambda start, end: calls.append((start, end)) or {"status": "available"},
            )

            self.assertEqual(summary, {"checked": 1, "available": 1, "pending": 0, "unavailable": 0})
            self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
