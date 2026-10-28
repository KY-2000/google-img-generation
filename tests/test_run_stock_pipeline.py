import unittest
from unittest import mock
from pathlib import Path

import run_stock_pipeline


class CommandUsageIntegrationTests(unittest.TestCase):
    @mock.patch("run_stock_pipeline.upload_draft")
    @mock.patch("run_stock_pipeline.validate_batch_ready_for_upload")
    @mock.patch("run_stock_pipeline.StockPipeline")
    @mock.patch("run_stock_pipeline.Registry")
    @mock.patch("run_stock_pipeline.UsageLedger")
    @mock.patch("run_stock_pipeline.load_topic_queue")
    def test_failed_queue_reports_summary_without_attempting_upload(
        self, load_queue, ledger, registry, pipeline, validate_upload, upload,
    ):
        load_queue.return_value = self._queue()
        pipeline.return_value.run_queue.return_value = {
            "total": 8, "succeeded": 0, "failed": 8,
            "ready_to_upload": 0, "needs_review": 8,
        }
        self.assertEqual(run_stock_pipeline.main(["--queue", "unused.json", "--resume", "--upload-draft"]), 1)
        validate_upload.assert_not_called()
        upload.assert_not_called()

    def _queue(self):
        return {"run_id": "TF_2026W35", "assets": []}

    def test_queue_and_candidates_are_mutually_exclusive_and_one_is_required(self):
        with self.assertRaises(SystemExit):
            run_stock_pipeline.parse_args([])
        with self.assertRaises(SystemExit):
            run_stock_pipeline.parse_args(["--queue", "queue.json", "--candidates", "finder.json"])
        args = run_stock_pipeline.parse_args(["--candidates", "finder.json"])
        self.assertEqual(args.candidates, Path("finder.json"))

    def test_upload_resume_after_is_nonnegative(self):
        args = run_stock_pipeline.parse_args([
            "--queue", "queue.json", "--upload-resume-after", "3",
        ])
        self.assertEqual(args.upload_resume_after, 3)
        with self.assertRaises(SystemExit):
            run_stock_pipeline.parse_args([
                "--queue", "queue.json", "--upload-resume-after", "-1",
            ])

    @mock.patch("run_stock_pipeline.write_queue_atomic")
    @mock.patch("run_stock_pipeline.convert_candidates")
    @mock.patch("run_stock_pipeline.StockPipeline")
    @mock.patch("run_stock_pipeline.Registry")
    @mock.patch("run_stock_pipeline.UsageLedger")
    @mock.patch("run_stock_pipeline.BigQueryBillingReconciler")
    @mock.patch("run_stock_pipeline.load_topic_queue")
    def test_candidates_are_converted_persisted_and_run_without_queue_loader(
        self, load_queue, reconciler_class, usage_ledger, registry_class, pipeline_class,
        convert, write_queue,
    ):
        convert.return_value = self._queue()
        pipeline_class.return_value.run_queue.return_value = {
            "total": 0, "succeeded": 0, "failed": 0,
            "ready_to_upload": 0, "needs_review": 0,
        }

        exit_code = run_stock_pipeline.main([
            "--candidates", "queues/incoming/finder.json",
            "--database", "data/test.sqlite",
        ])

        root = Path(run_stock_pipeline.__file__).resolve().parent
        convert.assert_called_once_with(
            root / "queues/incoming/finder.json", root / "data/test.sqlite"
        )
        write_queue.assert_called_once_with(
            convert.return_value,
            root / "queues/production/topic_queue_2026w35.json",
        )
        load_queue.assert_not_called()
        pipeline_class.return_value.run_queue.assert_called_once()
        self.assertEqual(exit_code, 0)

    @mock.patch("run_stock_pipeline.StockPipeline")
    @mock.patch("run_stock_pipeline.Registry")
    @mock.patch("run_stock_pipeline.UsageLedger")
    @mock.patch("run_stock_pipeline.BigQueryBillingReconciler")
    @mock.patch("run_stock_pipeline.load_topic_queue")
    def test_command_wires_usage_ledger_to_pipeline_and_finalizes(
        self, load_queue, reconciler_class, usage_ledger, registry_class, pipeline_class
    ):
        load_queue.return_value = self._queue()
        registry = registry_class.return_value.__enter__.return_value
        ledger = usage_ledger.return_value
        pipeline_class.return_value.run_queue.return_value = {
            "total": 0,
            "succeeded": 0,
            "failed": 0,
            "ready_to_upload": 0,
            "needs_review": 0,
        }

        exit_code = run_stock_pipeline.main(["--queue", "unused.json"])

        self.assertEqual(exit_code, 0)
        self.assertIs(
            pipeline_class.call_args.kwargs["usage_recorder"],
            ledger.record,
        )
        reconciler_class.assert_called_once_with(
            table_path=(
                "adobe-stock-img.billing_export."
                "gcp_billing_export_v1_014122_B8FF5F_35AD2B"
            ),
            project_id="adobe-stock-img",
        )
        self.assertIs(
            usage_ledger.call_args.kwargs["official_cost_lookup"],
            reconciler_class.return_value.lookup,
        )
        ledger.finalize.assert_called_once_with()
        self.assertIsNotNone(registry)

    @mock.patch("run_stock_pipeline.StockPipeline")
    @mock.patch("run_stock_pipeline.Registry")
    @mock.patch("run_stock_pipeline.UsageLedger")
    @mock.patch("run_stock_pipeline.BigQueryBillingReconciler")
    @mock.patch("run_stock_pipeline.load_topic_queue")
    def test_command_finalizes_usage_summary_when_pipeline_fails(
        self, load_queue, reconciler_class, usage_ledger, registry_class, pipeline_class
    ):
        load_queue.return_value = self._queue()
        ledger = usage_ledger.return_value
        pipeline_class.return_value.run_queue.side_effect = RuntimeError("provider failed")

        with self.assertRaisesRegex(RuntimeError, "provider failed"):
            run_stock_pipeline.main(["--queue", "unused.json"])

        ledger.finalize.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
