from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

import stock_batch_manager
from stock_pipeline.adobe_csv import read_adobe_csv
from stock_pipeline.batch_manager import new_run_dir, preview_queues, run_manager, upload_process, validate_test_queue
from stock_pipeline.manager_baseline import TEST_QUEUE, snapshot_baseline
from stock_pipeline.manager_runtime import git_sync, push_to_main, redactor
from stock_pipeline.orchestrator import StockPipeline
from stock_pipeline.registry import Registry
from tests.test_candidate_import import candidate
from tests.test_stock_pipeline import sample_asset, sample_model_metadata, sample_queue


SOURCE_ROOT = Path(__file__).resolve().parents[1]
COMMIT = "a" * 40
TIME = "2026-11-12T08:00:00+08:00"


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.incoming = self.root / "queues/incoming"
        self.incoming.mkdir(parents=True)
        shutil.copytree(SOURCE_ROOT / "schemas", self.root / "schemas")
        shutil.copy2(SOURCE_ROOT / "category-code.txt", self.root)
        (self.root / "config").mkdir()
        (self.root / "config/stock_icon_pipeline.json").write_text('{"image":{"minimum_resolution":32}}')
        self.calls, self.uploads, self.events = [], [], []
        self.stop_requested = False
        self.failure = None
        self.metadata_error = False
        self.stop_on_generate = False
        self.partial_upload = False
        self.incomplete_without_error = False

    def write(self, filename, value):
        path = self.incoming / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def production(self, name, timestamp=TIME, *, unnumbered=True, asset_id="AST000001", two=False):
        assets = [sample_asset(asset_id)]
        if two:
            assets.append(sample_asset("AST000002"))
        for asset in assets:
            asset["theme"] = name
            if unnumbered:
                asset.pop("asset_id")
        queue = sample_queue(*assets)
        queue.update(run_id=f"TF_{name}", generated_at=timestamp)
        return queue

    def generate(self, **kwargs):
        self.calls.append(kwargs["prompt"])
        if self.stop_on_generate:
            self.stop_requested = True
        if self.failure and self.failure in kwargs["prompt"]:
            if self.failure == "BadQC":
                size = (16, 32)
            else:
                raise TimeoutError("mock provider timeout")
        else:
            size = (32, 32)
        data = io.BytesIO()
        Image.new("RGB", size).save(data, format="PNG")
        return data.getvalue()

    def metadata(self, **_kwargs):
        if self.metadata_error:
            raise RuntimeError("metadata failed")
        return json.dumps(sample_model_metadata())

    def pipeline(self, **kwargs):
        return StockPipeline(**kwargs, image_generate_bytes=self.generate, metadata_generate_text=self.metadata,
                             max_attempts=1)

    def uploader(self, root, batch, receipts, *, log, **_kwargs):
        manifest = json.loads((batch / "batch_manifest.json").read_text())
        self.uploads.append(manifest)
        self.assertEqual([row["Filename"] for row in read_adobe_csv(batch / "adobe_stock_metadata.csv")],
                         [a["filename"] for a in manifest["items"]])
        if self.incomplete_without_error:
            return
        for index, item in enumerate(manifest["items"]):
            receipt = receipts / (item["filename"] + ".saved.json")
            if not receipt.exists():
                receipt.write_text(json.dumps({**item, "status": "saved_draft"}), encoding="utf-8")
            log("", upload_progress={"stage": "draft_saved", "filename": item["filename"]})
            if self.partial_upload and index == 0:
                raise RuntimeError("mock Adobe interrupted")

    def run_batch(self, **kwargs):
        self.report_dir = new_run_dir(self.root)
        return run_manager(self.root, COMMIT, TIME, self.report_dir, pipeline_factory=self.pipeline,
                           uploader=self.uploader, should_stop=lambda: self.stop_requested,
                           emit=self.events.append, **kwargs)

    def test_error_log_button_copies_redacted_failure_and_opens_exact_chat(self):
        run_dir = new_run_dir(self.root)
        (run_dir / "report.json").write_text(json.dumps({
            "status": "FAILED", "last_error": "API request failed: Bearer private-token",
        }), encoding="utf-8")
        (run_dir / "activity.log").write_text("Provider rejected request\n", encoding="utf-8")
        later_run = new_run_dir(self.root)
        (later_run / "report.json").write_text('{"status":"STOPPED","last_error":"Stopped by user"}', encoding="utf-8")
        chat = "codex://threads/01a00000-0000-0000-0000-000000000001"
        (run_dir.parent.parent / "support_chat.txt").write_text(chat, encoding="utf-8")
        desktop = SimpleNamespace(run_dir=None, window=Mock(), append=Mock())
        with (patch.object(stock_batch_manager, "ROOT", self.root),
              patch.object(os, "startfile") as open_chat):
            stock_batch_manager.Desktop.open_error_in_chatgpt(desktop)
        open_chat.assert_called_once_with(chat)
        copied = desktop.window.clipboard_append.call_args.args[0]
        self.assertIn("Provider rejected request", copied)
        self.assertIn("Bearer [REDACTED]", copied)
        self.assertNotIn("private-token", copied)
        self.assertNotIn("Stopped by user", copied)

    def test_error_log_button_recopies_the_current_run_before_a_report_exists(self):
        run_dir = new_run_dir(self.root)
        activity = run_dir / "activity.log"
        activity.write_text("metadata generation attempt 1 failed: 503 UNAVAILABLE\n", encoding="utf-8")
        desktop = SimpleNamespace(run_dir=run_dir, window=Mock(), append=Mock())
        with (patch.object(stock_batch_manager, "ROOT", self.root), patch.object(os, "startfile")):
            stock_batch_manager.Desktop.open_error_in_chatgpt(desktop)
            with activity.open("a", encoding="utf-8") as log:
                log.write("metadata generation attempt 2 failed: new error\n")
            stock_batch_manager.Desktop.open_error_in_chatgpt(desktop)
        first, second = [call.args[0] for call in desktop.window.clipboard_append.call_args_list]
        self.assertIn("503 UNAVAILABLE", first)
        self.assertNotIn("new error", first)
        self.assertIn("new error", second)

    def test_published_commit_opens_exact_github_diff(self):
        desktop = SimpleNamespace(commit_sha=COMMIT, commit_on_github=True)
        with patch.object(os, "startfile") as open_link:
            stock_batch_manager.Desktop.open_commit(desktop)
        open_link.assert_called_once_with(
            "https://github.com/KY-2000/google-img-generation/commit/" + COMMIT,
        )

    def test_upload_only_launches_edge_if_cdp_is_unreachable(self):
        endpoint = "http://127.0.0.1:9222"
        profile = self.root / "edge automation"
        batch = self.root / "batch"
        receipts = self.root / "receipts"
        edge = self.root / "msedge.exe"
        edge.touch()
        progress = {"stage": "checking", "detail": "Page 1"}
        upload_log = Mock()
        uploader = Mock()
        uploader.__enter__ = Mock(return_value=SimpleNamespace(
            stdout=["step 2 action: opening uploads\n", f"ADOBE_PROGRESS {json.dumps(progress)}\n"], wait=lambda: 0,
        ))
        uploader.__exit__ = Mock(return_value=False)
        runtime = "stock_pipeline.manager_runtime"
        with (patch(runtime + ".cdp_connected", side_effect=[True, False, True]),
              patch("shutil.which", return_value=str(edge)),
              patch(runtime + ".subprocess.Popen", side_effect=[uploader, Mock(), uploader]) as launch):
            upload_process(self.root, batch, receipts, cdp=endpoint, profile=str(profile), log=upload_log)
            upload_process(self.root, batch, receipts, cdp=endpoint, profile=str(profile), log=Mock())
        edge_command = launch.call_args_list[1].args[0]
        self.assertEqual(edge_command[0], str(edge))
        self.assertIn("--remote-debugging-port=9222", edge_command)
        self.assertIn(f"--user-data-dir={profile}", edge_command)
        self.assertTrue(launch.call_args_list[0].args[0][0].startswith("node"))
        self.assertTrue(launch.call_args_list[2].args[0][0].startswith("node"))
        upload_log.assert_any_call("step 2 action: opening uploads")
        upload_log.assert_any_call("", upload_progress=progress)

    def test_selected_image_model_overrides_config_and_reaches_generator(self):
        self.write("model.json", self.production("Model"))
        (self.root / ".env").write_text("IMAGE_MODEL=gemini-3-pro-image\n")
        generate = Mock(side_effect=self.generate)
        self.generate = generate
        report = self.run_batch(image_model="gemini-nano-banana-2.1")
        self.assertEqual(report["status"], "COMPLETE", report)
        self.assertEqual(generate.call_args.kwargs["model"], "gemini-nano-banana-2.1")
        self.assertEqual(report["image_model"], "gemini-nano-banana-2.1")

    def test_metadata_model_selection_reaches_vertex_and_records_actual_model(self):
        self.write("metadata.json", self.production("Metadata"))
        (self.root / ".env").write_text("METADATA_PROVIDER=openrouter\nMETADATA_MODEL=previous-model\n")
        self.metadata = Mock(side_effect=self.metadata)
        report = self.run_batch(metadata_model="gemini-3.8-flash")
        self.assertEqual(report["status"], "COMPLETE", report)
        self.assertEqual(self.metadata.call_args.kwargs["provider"], "vertex")
        self.assertEqual(self.metadata.call_args.kwargs["model"], "gemini-3.8-flash")
        self.assertEqual(report["metadata_model"], "gemini-3.8-flash")
        with Registry(self.root / "data/stock_pipeline.sqlite") as registry:
            self.assertEqual(registry.list_assets()[0]["metadata_model"], "gemini-3.8-flash")

    def test_hydrogen_metadata_uses_developer_key_and_a_separate_client(self):
        self.write("metadata.json", self.production("Metadata"))
        (self.root / ".env").write_text("GEMINI_API_KEY=offline-test-key\n", encoding="utf-8")
        self.pipeline = lambda **kwargs: StockPipeline(**kwargs, image_generate_bytes=self.generate, max_attempts=1)
        client = Mock()
        client.models.generate_content.return_value = SimpleNamespace(text=json.dumps(sample_model_metadata()))
        with patch("google.genai.Client", return_value=client) as build_client:
            report = self.run_batch(metadata_model="gemini-4-hydrogen")
        self.assertEqual(report["status"], "COMPLETE", report)
        self.assertEqual(report["metadata_provider"], "gemini-developer")
        build_client.assert_called_once_with(vertexai=False, api_key="offline-test-key")
        request = client.models.generate_content.call_args.kwargs
        self.assertEqual(request["model"], "gemini-4-hydrogen")
        self.assertEqual(request["contents"][0].inline_data.mime_type, "image/png")
        self.assertEqual(request["config"].response_mime_type, "application/json")
        self.assertIsNone(request["config"].temperature)
        self.assertIsNone(request["config"].top_p)

    def test_hydrogen_503_switches_remaining_metadata_to_vertex_flash(self):
        from google.genai.errors import ServerError

        self.write("metadata.json", self.production("Metadata", two=True))
        (self.root / ".env").write_text("GEMINI_API_KEY=offline-test-key\n", encoding="utf-8")
        attempts = []

        def metadata(**kwargs):
            attempts.append((kwargs["provider"], kwargs["model"]))
            if kwargs["provider"] == "gemini-developer":
                raise ServerError(503, {"error": {"status": "UNAVAILABLE", "message": "High demand"}})
            kwargs["usage_callback"]({"prompt_tokens": 1, "output_tokens": 2})
            return json.dumps(sample_model_metadata())

        self.metadata = metadata
        report = self.run_batch(metadata_model="gemini-4-hydrogen")
        self.assertEqual(report["status"], "COMPLETE", report)
        self.assertEqual(attempts, [
            ("gemini-developer", "gemini-4-hydrogen"),
            ("vertex", "gemini-3.8-flash"), ("vertex", "gemini-3.8-flash"),
        ])
        self.assertEqual(len(self.calls), 2)
        with Registry(self.root / "data/stock_pipeline.sqlite") as registry:
            self.assertEqual({row["metadata_model"] for row in registry.list_assets()}, {"gemini-3.8-flash"})

    def test_hydrogen_without_developer_key_stops_before_generation(self):
        self.write("metadata.json", self.production("Metadata"))
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            report = self.run_batch(metadata_model="gemini-4-hydrogen")
        self.assertEqual(report["status"], "FAILED")
        self.assertIn("GEMINI_API_KEY is missing", report["last_error"])
        self.assertEqual(self.calls, [])

    def test_sampling_and_generation_limit_across_queues_then_resume(self):
        self.write("a.json", self.production("First", two=True))
        self.write("b.json", self.production("Second"))
        self.generate = Mock(side_effect=self.generate)
        first = self.run_batch(temperature=0.6, top_p=0.8, max_images=2)
        self.assertEqual(first["status"], "LIMIT_REACHED", first)
        self.assertEqual(self.generate.call_count, 2)
        self.assertEqual(self.generate.call_args.kwargs["temperature"], 0.6)
        self.assertEqual(self.generate.call_args.kwargs["top_p"], 0.8)
        self.assertEqual(first["failed_assets"], 0)
        self.assertFalse(self.uploads)
        second = self.run_batch(max_images=1)
        self.assertEqual(second["status"], "COMPLETE", second)
        self.assertEqual(self.generate.call_count, 3)
        self.assertEqual(len(self.uploads[0]["items"]), 3)

    def test_parallel_generation_overlaps_calls_and_honors_image_limit(self):
        queue = self.production("Parallel", two=True)
        queue["assets"].append(dict(queue["assets"][0], theme="Third"))
        self.write("parallel.json", queue)
        simultaneous = Barrier(2, timeout=4)
        generate = self.generate

        def synchronized_generation(**kwargs):
            simultaneous.wait()
            return generate(**kwargs)

        self.generate = synchronized_generation
        first = self.run_batch(max_workers=2, max_images=2)
        self.assertEqual(first["status"], "LIMIT_REACHED", first)
        self.assertEqual((first["generation_started"], first["generated"], first["generation_deferred"]), (2, 2, 1))
        self.assertFalse(self.uploads)
        progress = [event["asset_progress"] for event in self.events if "asset_progress" in event]
        stages = {}
        active = set()
        peak = 0
        positions = []
        for event in progress:
            aid, stage = event["asset_id"], event["stage"]
            if stage == "preparing":
                self.assertNotIn(aid, active)
                active.add(aid)
                peak = max(peak, len(active))
                positions.append(event["index"])
                self.assertEqual(event["total"], 3)
                self.assertTrue(event["theme"])
            self.assertIn(aid, active)
            stages.setdefault(aid, []).append(stage)
            if stage in {"ready", "deferred"}:
                active.remove(aid)
        self.assertFalse(active)
        self.assertEqual(peak, 2)
        self.assertCountEqual(positions, [1, 2, 3])
        self.assertCountEqual(list(stages.values()), [
            ["preparing", "image", "qc", "metadata", "metadata_check", "ready"],
            ["preparing", "image", "qc", "metadata", "metadata_check", "ready"],
            ["preparing", "deferred"],
        ])
        self.generate = generate
        second = self.run_batch(max_workers=3)
        self.assertEqual(second["status"], "COMPLETE", second)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(len(self.uploads[0]["items"]), 3)

    def test_parallel_stop_finishes_active_assets_without_starting_another(self):
        queue = self.production("Parallel", two=True)
        queue["assets"].append(dict(queue["assets"][0], theme="Third"))
        self.write("parallel.json", queue)
        simultaneous = Barrier(2, action=lambda: setattr(self, "stop_requested", True), timeout=4)
        generate = self.generate

        def stop_when_both_are_generating(**kwargs):
            simultaneous.wait()
            return generate(**kwargs)

        self.generate = stop_when_both_are_generating
        report = self.run_batch(max_workers=2)
        self.assertEqual(report["status"], "STOPPED", report)
        self.assertEqual((report["generated"], report["completed_assets"]), (2, 2))
        self.assertFalse(self.uploads)

    def test_image_429_stops_queued_assets_and_resume_reuses_active_success(self):
        from google.genai.errors import ClientError

        queue = self.production("RateLimited", two=True)
        queue["assets"][1]["theme"] = "ActivePeer"
        queue["assets"].append(dict(queue["assets"][0], theme="Third"))
        self.write("parallel.json", queue)
        simultaneous = Barrier(2, timeout=4)
        auto_stop = Event()
        attempted = []
        generate = self.generate

        class ProgressEvents(list):
            def append(self, event):
                super().append(event)
                if "stopping after active assets" in event.get("message", ""):
                    auto_stop.set()

        def rate_limited_generation(**kwargs):
            prompt = kwargs["prompt"]
            attempted.append(prompt)
            simultaneous.wait()
            if "RateLimited" in prompt:
                raise ClientError(429, {"error": {"status": "RESOURCE_EXHAUSTED", "message": "capacity"}})
            if not auto_stop.wait(timeout=4):
                raise AssertionError("429 did not trigger automatic stop")
            return generate(**kwargs)

        self.events = ProgressEvents()
        self.generate = rate_limited_generation
        pipeline = self.pipeline

        def multi_attempt_pipeline(**kwargs):
            worker = pipeline(**kwargs)
            worker.max_attempts = 4
            worker.retry_initial_wait = 0
            return worker

        self.pipeline = multi_attempt_pipeline
        first = self.run_batch(max_workers=2)
        self.assertEqual(first["status"], "RATE_LIMITED", first)
        self.assertEqual((first["generated"], first["failed_assets"]), (1, 1))
        self.assertEqual(len(attempted), 2)
        self.assertFalse(self.uploads)

        self.generate = generate
        second = self.run_batch(max_workers=2)
        self.assertEqual(second["status"], "COMPLETE", second)
        self.assertEqual(second["generation_started"], 2)
        self.assertEqual(sum("ActivePeer" in prompt for prompt in self.calls), 1)
        self.assertEqual(len(self.uploads[0]["items"]), 3)

    def test_parallel_hydrogen_overload_routes_queued_assets_to_flash(self):
        from google.genai.errors import ServerError

        queue = self.production("Parallel", two=True)
        queue["assets"].append(dict(queue["assets"][0], theme="Third"))
        self.write("parallel.json", queue)
        (self.root / ".env").write_text("GEMINI_API_KEY=offline-test-key\n", encoding="utf-8")
        attempts = []

        def metadata(**kwargs):
            attempts.append((kwargs["theme"], kwargs["provider"], kwargs["model"]))
            if kwargs["provider"] == "gemini-developer":
                raise ServerError(503, {"error": {"status": "UNAVAILABLE", "message": "High demand"}})
            return json.dumps(sample_model_metadata())

        self.metadata = metadata
        report = self.run_batch(max_workers=2, metadata_model="gemini-4-hydrogen")
        self.assertEqual(report["status"], "COMPLETE", report)
        self.assertTrue(any(provider == "gemini-developer" for _, provider, _ in attempts))
        self.assertEqual([(provider, model) for theme, provider, model in attempts if theme == "Third"],
                         [("vertex", "gemini-3.8-flash")])

    def test_preview_is_read_only_and_only_confirmed_queues_run(self):
        self.write("a.json", self.production("Coffee", two=True))
        self.write("b.json", self.production("Spring"))
        self.write("broken.json", None)
        preview, sources = preview_queues(self.root, TIME)
        self.assertFalse((self.root / "data").exists())
        self.assertFalse(self.calls)
        self.assertEqual([(row["themes"], row["total"], row["status"]) for row in preview], [
            (["Coffee"], 2, "Not started"), (["Spring"], 1, "Not started"), ([], 0, "Invalid"),
        ])
        first = self.run_batch(sources=sources[:1])
        self.assertEqual(first["status"], "COMPLETE", first)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(self.uploads[0]["items"]), 2)
        preview, _ = preview_queues(self.root, TIME)
        self.assertEqual(preview[0]["status"], "Uploaded draft")
        self.assertEqual(preview[1]["status"], "Not started")

    def test_preview_reports_images_already_generated(self):
        self.write("a.json", self.production("Coffee", two=True))
        self.run_batch(max_images=1)
        preview, _ = preview_queues(self.root, TIME)
        self.assertEqual(preview[0]["status"], "In progress")
        self.assertEqual(preview[0]["new_images"], 1)

    def test_image_overview_counts_selected_assets_as_their_status_changes(self):
        self.write("a.json", self.production("Coffee", two=True))
        self.write("b.json", self.production("Spring"))
        _, sources = preview_queues(self.root, TIME)
        self.run_batch(sources=sources[:1], max_images=1)
        self.assertIn(
            {"total": 2, "pending": 1, "generated": 1, "uploaded": 0, "failed": 0},
            [event["overview"] for event in self.events if "image 1/1 completed" in event.get("message", "")],
        )
        self.events.clear()
        self.run_batch(sources=sources[:1])
        self.assertEqual(self.events[-1]["overview"],
                         {"total": 2, "pending": 0, "generated": 0, "uploaded": 2, "failed": 0})

    def test_worker_waits_for_confirmation_and_rejects_changed_queue(self):
        self.write("a.json", self.production("Coffee"))
        run_dir = new_run_dir(self.root)
        preview_ready = Event()

        def capture(line, **_kwargs):
            event = json.loads(line)
            if "preview" in event:
                preview_ready.set()

        read_fd, write_fd = os.pipe()
        with os.fdopen(read_fd, "r", encoding="ascii") as read_pipe, os.fdopen(write_fd, "w", encoding="ascii") as write_pipe:
            with (patch.object(stock_batch_manager, "ROOT", self.root),
                  patch.object(stock_batch_manager, "git_sync", return_value=(COMMIT, TIME)),
                  patch.object(stock_batch_manager, "verify_reviewed_checkout"),
                  patch.object(stock_batch_manager.sys, "stdin", read_pipe),
                  patch.object(stock_batch_manager, "print", side_effect=capture, create=True),
                  patch("stock_pipeline.batch_manager.run_manager") as production_run,
                  ThreadPoolExecutor(max_workers=1) as pool):
                future = pool.submit(stock_batch_manager.worker, run_dir, None, None)
                try:
                    self.assertTrue(preview_ready.wait(timeout=10))
                    production_run.assert_not_called()
                    self.write("a.json", self.production("Changed"))
                    write_pipe.write("1\n")
                    write_pipe.flush()
                    self.assertEqual(future.result(timeout=10), 1)
                    production_run.assert_not_called()
                    self.assertIn("changed since preview", (run_dir / "report.json").read_text(encoding="utf-8"))
                finally:
                    write_pipe.close()

    def test_failed_generation_consumes_one_slot_and_defers_the_next_asset(self):
        self.write("a.json", self.production("Timeout", two=True))
        self.failure = "Timeout"
        first = self.run_batch(max_images=1)
        self.assertEqual(first["status"], "BLOCKED", first)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(first["failed_assets"], 1)
        self.assertEqual(first["generation_deferred"], 1)
        self.assertFalse(self.uploads)

    def test_baseline_skips_old_files_but_not_new_or_changed_inputs(self):
        from stock_pipeline.manager_queues import discover

        old = self.write("old.json", self.production("Old"))
        self.write(Path(TEST_QUEUE).name, self.production("Test"))
        self.assertEqual(snapshot_baseline(self.root), 1)
        self.write("new.json", self.production("New"))
        records = {s.path.name: s for s in discover(self.root, TIME)}
        self.assertTrue(records[old.name].baseline)
        self.assertFalse(records["new.json"].baseline)
        self.assertFalse(records[Path(TEST_QUEUE).name].baseline)
        self.write(old.name, self.production("Changed"))
        self.assertFalse(next(s for s in discover(self.root, TIME) if s.path == old).baseline)
        with self.assertRaises(FileExistsError):
            snapshot_baseline(self.root)

    def test_offline_test_checks_only_r02_without_production_state(self):
        source = self.write(Path(TEST_QUEUE).name, self.production("Test", two=True))
        (self.incoming / "broken.json").write_text("{")
        before = source.read_bytes()
        run_dir = new_run_dir(self.root)
        report = validate_test_queue(self.root, run_dir)
        self.assertEqual((report["status"], report["scanned"], report["total_assets"],
                          report["generated"], report["uploaded"]), ("TEST_PASSED", 1, 2, 0, 0))
        self.assertEqual(source.read_bytes(), before)
        self.assertFalse((self.root / "data").exists())
        self.assertFalse((self.root / "output/assets").exists())

    def test_offline_worker_simulates_selected_parallel_layout(self):
        self.write(Path(TEST_QUEUE).name, self.production("Test"))
        for workers in (1, 2, 3):
            with self.subTest(workers=workers):
                output = io.StringIO()
                run_dir = new_run_dir(self.root)
                with (patch.object(stock_batch_manager, "ROOT", self.root),
                      patch.object(stock_batch_manager.time, "sleep"),
                      patch.object(stock_batch_manager, "git_sync") as git_sync_mock,
                      patch("stock_pipeline.batch_manager.run_manager") as production_worker,
                      patch("sys.stdout", output)):
                    result = stock_batch_manager.worker(run_dir, None, None, offline_test=True, max_workers=workers)
                self.assertEqual(result, 0)
                git_sync_mock.assert_not_called()
                production_worker.assert_not_called()
                events = [json.loads(line) for line in output.getvalue().splitlines()]
                active, peak = {}, 0
                for event in events:
                    if "asset_progress" not in event:
                        continue
                    progress = event["asset_progress"]
                    aid, stage = progress["asset_id"], progress["stage"]
                    if stage == "preparing":
                        self.assertEqual(progress["total"], workers)
                        active[aid] = []
                        peak = max(peak, len(active))
                    active[aid].append(stage)
                    if stage == "simulated_ready":
                        self.assertEqual(active.pop(aid), ["preparing", "image", "qc", "metadata",
                                                          "metadata_check", "simulated_ready"])
                self.assertEqual(peak, workers)
                self.assertFalse(active)
                upload_events = [event["upload_progress"] for event in events if "upload_progress" in event]
                self.assertGreater(next(i for i, event in enumerate(events) if "upload_progress" in event),
                                   max(i for i, event in enumerate(events) if "asset_progress" in event))
                self.assertTrue(all(event["simulated"] for event in upload_events))
                self.assertEqual(upload_events[-1], {"stage": "complete", "detail": "", "done": workers,
                                                     "total": workers, "simulated": True})
                report = events[-1]["report"]
                self.assertEqual((report["status"], report["total_assets"], report["generated"], report["uploaded"]),
                                 ("TEST_PASSED", 1, 0, 0))
        self.assertFalse((self.root / "data").exists())
        self.assertFalse((self.root / "output/assets").exists())

    def test_production_finder_order_combined_batch_and_idempotency(self):
        production = self.write("b.json", self.production("Production", "2026-11-11T09:00:00+08:00"))
        duplicate = self.write("c.json", json.loads(production.read_text()))
        self.write("finder/20261112_a.json", [candidate("C1", theme="Finder Alpha")])
        self.write("finder/20261112_b.json", [candidate("C2", theme="Finder Beta")])
        # A reserved legacy production file must not be reused by either allocator.
        reserved = self.root / "queues/production"
        reserved.mkdir()
        (reserved / "reserved.json").write_text(json.dumps(self.production("Reserved", unnumbered=False, asset_id="AST000500")))
        original = production.read_bytes()
        first = self.run_batch()
        self.assertEqual(first["status"], "COMPLETE", first)
        self.assertEqual((first["scanned"], first["new_queues"], first["generated"], first["uploaded"]), (4, 3, 3, 3))
        self.assertEqual(len(self.uploads), 1)
        items = self.uploads[0]["items"]
        self.assertTrue(all(int(a["asset_id"][3:]) > 500 for a in items))
        self.assertEqual(len({a["run_id"] for a in items}), 3)
        self.assertTrue(all(a["source_commit_sha"] == COMMIT for a in items))
        self.assertIn("Production", self.calls[0])
        self.assertEqual(production.read_bytes(), original)
        self.assertEqual(duplicate.read_bytes(), original)
        self.assertEqual(len(list((self.root / "work_io/topic_finder/runtime_queues").glob("*.json"))), 3)
        self.assertEqual(json.loads((self.report_dir / "report.json").read_text())["uploaded"], 3)
        self.assertTrue((self.report_dir / "report.md").exists())
        second = self.run_batch()
        self.assertEqual(second["status"], "NO_WORK", second)
        self.assertEqual((second["already_uploaded"], len(self.calls), len(self.uploads)), (3, 3, 1))

    def test_partial_save_then_retry_upload_only_missing(self):
        self.write("one.json", self.production("Two", two=True))
        self.partial_upload = True
        first = self.run_batch()
        self.assertEqual(first["status"], "UPLOAD_INCOMPLETE", first)
        self.assertEqual(first["uploaded"], 1)
        progress = [event["upload_progress"] for event in self.events if "upload_progress" in event]
        self.assertEqual([(step["stage"], step["done"], step["total"]) for step in progress],
                         [("browser", 0, 2), ("draft_saved", 1, 2), ("finalizing", 1, 2), ("incomplete", 1, 2)])
        self.assertEqual(self.events[-1]["overview"],
                         {"total": 2, "pending": 0, "generated": 1, "uploaded": 1, "failed": 0})
        self.partial_upload = False
        self.events.clear()
        second = self.run_batch()
        self.assertEqual(second["status"], "COMPLETE", second)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(self.uploads[1]["items"]), 1)
        self.assertEqual(second["already_uploaded"], 1)
        progress = [event["upload_progress"] for event in self.events if "upload_progress" in event]
        self.assertEqual((progress[-1]["stage"], progress[-1]["done"], progress[-1]["total"]), ("complete", 1, 1))

    def test_legacy_finder_reuses_original_run_and_example_does_not_claim_ids(self):
        from stock_pipeline.candidate_conversion import convert_candidates

        source = self.write("finder_20261112.json", [candidate("LEGACY")])
        db = self.root / "data/stock_pipeline.sqlite"
        previous = convert_candidates(source, db)
        asset_id = previous["assets"][0]["asset_id"]
        self.write("template.example.json", self.production("Template", unnumbered=False, asset_id=asset_id))
        with Registry(db) as registry:
            self.pipeline(project_root=self.root, registry=registry, minimum_resolution=32).run_queue(previous)
        result = self.run_batch()
        self.assertEqual(result["status"], "COMPLETE", result)
        self.assertEqual(result["generated"], 0)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(result["skipped_queues"], 1)
        self.assertEqual(self.uploads[0]["items"][0]["asset_id"], asset_id)
        self.assertEqual(self.uploads[0]["items"][0]["run_id"], previous["run_id"])

    def test_exit_success_without_receipt_does_not_mark_uploaded(self):
        self.write("one.json", self.production("One"))
        self.incomplete_without_error = True
        result = self.run_batch()
        self.assertEqual(result["status"], "UPLOAD_INCOMPLETE", result)
        self.assertEqual(result["uploaded"], 0)
        with Registry(self.root / "data/stock_pipeline.sqlite") as registry:
            self.assertEqual(registry.list_assets()[0]["status"], "READY_TO_UPLOAD")

    def test_database_failure_after_browser_receipt_is_recovered_without_reupload(self):
        from unittest.mock import patch
        from stock_pipeline.managed_adobe import reconcile_receipts

        self.write("one.json", self.production("One"))
        real_reconcile = reconcile_receipts

        def fail_after_upload(registry, batch, receipt_dir):
            if self.uploads:
                raise OSError("mock registry write failure")
            return real_reconcile(registry, batch, receipt_dir)

        with patch("stock_pipeline.batch_manager.reconcile_receipts", side_effect=fail_after_upload):
            self.assertEqual(self.run_batch()["status"], "FAILED")
        second = self.run_batch()
        self.assertEqual(second["status"], "COMPLETE", second)
        self.assertEqual(second["already_uploaded"], 1)
        self.assertEqual(len(self.uploads), 1)

    def test_qc_or_timeout_blocks_upload_but_other_queue_completes(self):
        self.write("q1.json", self.production("BadQC"))
        self.write("q2.json", self.production("Good"))
        self.failure = "BadQC"
        result = self.run_batch()
        self.assertEqual(result["status"], "BLOCKED", result)
        self.assertEqual(result["failed_assets"], 1)
        self.assertEqual(self.events[-1]["overview"],
                         {"total": 2, "pending": 0, "generated": 1, "uploaded": 0, "failed": 1})
        self.assertEqual(result["prepared"], 1)
        self.assertEqual(len(self.uploads), 0)
        self.assertTrue((self.root / "output/adobe_batches" / result["batch_id"] / "batch_manifest.json").exists())
        self.write("q3.json", self.production("Timeout"))
        self.failure = "Timeout"
        result = self.run_batch()
        self.assertEqual(result["status"], "BLOCKED", result)
        self.assertEqual(result["failed_assets"], 2)
        self.assertEqual(len(self.uploads), 0)

    def test_metadata_failure_resumes_saved_image(self):
        self.write("one.json", self.production("One"))
        self.metadata_error = True
        self.assertEqual(self.run_batch()["status"], "BLOCKED")
        self.metadata_error = False
        result = self.run_batch()
        self.assertEqual(result["status"], "COMPLETE", result)
        self.assertEqual(len(self.calls), 1)

    def test_interruption_after_atomic_image_write_reuses_image(self):
        from unittest.mock import patch

        self.write("one.json", self.production("One"))
        update = Registry.update_asset

        def interrupted(registry, aid, **fields):
            if fields.get("generated_at"):
                raise KeyboardInterrupt("simulate shutdown before committing image-ready state")
            return update(registry, aid, **fields)

        with patch.object(Registry, "update_asset", interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.run_batch()
        self.assertEqual(len(self.calls), 1)
        result = self.run_batch()
        self.assertEqual(result["status"], "COMPLETE", result)
        self.assertEqual(len(self.calls), 1)

    def test_cooperative_stop_completes_current_asset_then_resumes(self):
        self.write("one.json", self.production("Two", two=True))
        self.stop_on_generate = True
        self.assertEqual(self.run_batch()["status"], "STOPPED")
        self.assertEqual((len(self.calls), len(self.uploads)), (1, 0))
        self.stop_on_generate = self.stop_requested = False
        self.assertEqual(self.run_batch()["status"], "COMPLETE")
        self.assertEqual(len(self.calls), 2)

    def test_invalid_json_and_incompatible_same_run_block_upload(self):
        self.write("good.json", self.production("Good"))
        (self.incoming / "broken.json").write_text("{")
        first = self.production("Conflict")
        second = self.production("Conflict")
        second["assets"][0]["theme"] = "Different"
        self.write("first.json", first)
        self.write("second.json", second)
        result = self.run_batch()
        self.assertEqual(result["status"], "BLOCKED", result)
        self.assertEqual(len([q for q in result["queues"] if q["queue_status"] == "INVALID"]), 3)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.uploads), 0)

    def test_existing_run_change_is_invalid_without_regeneration(self):
        source = self.write("one.json", self.production("One"))
        self.assertEqual(self.run_batch()["status"], "COMPLETE")
        q = json.loads(source.read_text())
        q["assets"][0]["theme"] = "Changed"
        self.write(source.name, q)
        report = self.run_batch()
        self.assertEqual(report["status"], "BLOCKED")
        self.assertIn("Content changed", report["queues"][0]["last_error"])
        self.assertEqual(len(self.calls), 1)

    def test_invalid_duplicate_asset_number_across_runs(self):
        self.write("one.json", self.production("One", unnumbered=False))
        self.write("two.json", self.production("Two", unnumbered=False))
        result = self.run_batch()
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual([q["queue_status"] for q in result["queues"]], ["INVALID", "INVALID"])
        self.assertFalse(self.calls)

    def test_schema_rejects_non_4k_background_before_allocation(self):
        q = self.production("Background")
        a = q["assets"][0]
        for key in ("icon_1", "icon_2", "icon_3", "icon_4"):
            del a[key]
        a.update(asset_type="commercial_background", aspect_ratio="16:9", resolution="1K",
                 remove_background=False, prompt="art", negative_prompt="text")
        self.write("invalid.json", q)
        result = self.run_batch()
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("Schema violation", result["queues"][0]["last_error"])
        self.assertFalse(self.calls)


class GitAndPrivacyTests(unittest.TestCase):
    def divergent_sync(self, root, remote_diff, *, merge=False):
        base, upstream, merged = "b" * 40, "c" * 40, "d" * 40
        outputs = [str(root), "", "main", "https://github.com/KY-2000/google-img-generation.git",
                   COMMIT, "", upstream, "", "main"]
        replies = [SimpleNamespace(returncode=0, stdout=value) for value in outputs]
        replies.append(SimpleNamespace(returncode=128, stderr="Not possible to fast-forward"))
        replies.extend(SimpleNamespace(returncode=0, stdout=value) for value in (COMMIT, base, remote_diff))
        if merge:
            replies.extend(SimpleNamespace(returncode=0, stdout=value)
                           for value in ("", merged, "", "", "main", remote_diff, TIME))
        return Mock(side_effect=replies), merged, upstream

    def test_diverged_sync_merges_new_incoming_json_without_rewriting_local_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            queue = "queues/incoming/holiday.json"
            runner, merged, upstream = self.divergent_sync(root, "A\0" + queue + "\0", merge=True)
            changes = {}
            self.assertEqual(git_sync(root, run=runner, incoming_changes=changes), (merged, TIME))
            self.assertEqual(changes, {queue: "New"})
            self.assertIn(["git", "merge", "--no-ff", "--no-edit", "--no-overwrite-ignore", "-Xno-renames", upstream],
                          [c.args[0] for c in runner.call_args_list])

    def test_diverged_sync_refuses_remote_code_and_overwriting_local_queue_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            queue = "queues/incoming/holiday.json"
            (root / queue).parent.mkdir(parents=True)
            (root / queue).write_text("my local queue", encoding="utf-8")
            for diff, message in (
                ("A\0README.md\0", "changes other than new incoming JSON queues"),
                ("M\0" + queue + "\0", "changes other than new incoming JSON queues"),
                ("A\0" + queue + "\0", "A local path already exists"),
            ):
                with self.subTest(diff=diff):
                    runner, _, _ = self.divergent_sync(root, diff)
                    with self.assertRaisesRegex(RuntimeError, message):
                        git_sync(root, run=runner, incoming_changes={})
                    self.assertFalse(any("--no-ff" in call.args[0] for call in runner.call_args_list))
            self.assertEqual((root / queue).read_text(encoding="utf-8"), "my local queue")

    def test_push_only_sends_committed_main_to_remote_main(self):
        runner = Mock(side_effect=[
            SimpleNamespace(returncode=0, stdout="git@github.com:KY-2000/google-img-generation.git\n", stderr=""),
            SimpleNamespace(returncode=0, stdout=COMMIT + "\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
        ])
        self.assertEqual(push_to_main(SOURCE_ROOT, run=runner), COMMIT)
        self.assertIn("--push", runner.call_args_list[0].args[0])
        command = runner.call_args.args[0]
        self.assertIn("--no-follow-tags", command)
        self.assertNotIn("--force", command)
        self.assertEqual(command[-2:], ["origin", f"{COMMIT}:refs/heads/main"])

    def test_push_rejects_unexpected_push_url_before_sending(self):
        runner = Mock(return_value=SimpleNamespace(
            returncode=0, stdout="https://example.test/unrelated/project.git\n", stderr="",
        ))
        with self.assertRaisesRegex(RuntimeError, "origin push URL"):
            push_to_main(SOURCE_ROOT, run=runner)
        self.assertEqual(runner.call_count, 1)

    def test_push_reports_non_fast_forward_without_retrying(self):
        runner = Mock(side_effect=[
            SimpleNamespace(returncode=0, stdout="https://github.com/KY-2000/google-img-generation", stderr=""),
            SimpleNamespace(returncode=0, stdout=COMMIT, stderr=""),
            SimpleNamespace(returncode=1, stdout="! [rejected] (fetch first)", stderr=""),
        ])
        with self.assertRaisesRegex(RuntimeError, "origin/main advanced"):
            push_to_main(SOURCE_ROOT, run=runner)
        self.assertEqual(runner.call_count, 3)

    def test_sync_counts_only_new_or_updated_jsons_at_the_applied_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            old = "b" * 40
            diff = "A\0queues/incoming/New queue.json\0M\0queues/incoming/updated.json\0D\0queues/incoming/deleted.json\0"
            outputs = [str(root), "", "main", "https://github.com/KY-2000/google-img-generation.git",
                       old, "", COMMIT, "", "main", "", COMMIT, "", "", "main", diff, TIME]
            runner = Mock(side_effect=[SimpleNamespace(returncode=0, stdout=value) for value in outputs])
            changes = {}
            commit_state = {}
            self.assertEqual(git_sync(root, run=runner, incoming_changes=changes, commit_state=commit_state),
                             (COMMIT, TIME))
            self.assertEqual(commit_state, {"on_github": True})
            self.assertEqual(changes, {"queues/incoming/New queue.json": "New",
                                       "queues/incoming/updated.json": "Updated"})

    def test_clean_local_main_may_already_contain_fetched_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            upstream = "b" * 40
            outputs = [str(root), "", "main", "https://github.com/KY-2000/google-img-generation.git",
                       "", upstream, "", "main", "", COMMIT, "", "", "main"]
            runner = Mock(side_effect=[
                *(SimpleNamespace(returncode=0, stdout=value) for value in outputs),
                SimpleNamespace(returncode=0, stdout=TIME),
            ])
            commit_state = {}
            self.assertEqual(git_sync(root, run=runner, commit_state=commit_state), (COMMIT, TIME))
            self.assertEqual(commit_state, {"on_github": False})
            self.assertIn(["git", "merge", "--ff-only", "--no-overwrite-ignore", upstream],
                          [c.args[0] for c in runner.call_args_list])
            self.assertIn(["git", "merge-base", "--is-ancestor", upstream, COMMIT],
                          [c.args[0] for c in runner.call_args_list])

    def test_dirty_worktree_never_fetches_or_resets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            runner = Mock(side_effect=[SimpleNamespace(returncode=0, stdout=str(root)),
                                       SimpleNamespace(returncode=0, stdout=" M README.md")])
            with self.assertRaisesRegex(RuntimeError, "dirty"):
                git_sync(root, run=runner)
            self.assertEqual([call.args[0][1] for call in runner.call_args_list], ["rev-parse", "status"])

    def test_network_error_stops_before_merge_and_does_not_log_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            runner = Mock(side_effect=[
                SimpleNamespace(returncode=0, stdout=str(root)),
                SimpleNamespace(returncode=0, stdout=""),
                SimpleNamespace(returncode=0, stdout="main"),
                SimpleNamespace(returncode=0, stdout="https://github.com/KY-2000/google-img-generation.git"),
                SimpleNamespace(returncode=128, stderr="secret-token"),
            ])
            with self.assertRaisesRegex(RuntimeError, "^Git fetch failed"):
                git_sync(root, run=runner)
            self.assertNotIn("merge", [call.args[0][1] for call in runner.call_args_list])

    def test_log_redaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("OPENROUTER_API_KEY=unique-test-key\n")
            cleaned = redactor(root)("provider unique-test-key; authorization: Bearer other-key; token=last-secret")
            for secret in ("unique-test-key", "other-key", "last-secret"):
                self.assertNotIn(secret, cleaned)
