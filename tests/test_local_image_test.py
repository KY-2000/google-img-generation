import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

import stock_batch_manager
from stock_pipeline.batch_manager import new_run_dir
from stock_pipeline.local_image_test import preview_size, run_sd_cli
from stock_pipeline.manager_baseline import TEST_QUEUE


class LocalImageTestTests(unittest.TestCase):
    def test_preview_dimensions_keep_exact_ratio(self):
        self.assertEqual(preview_size("16:9"), (2048, 1152))
        self.assertEqual(preview_size("9:16"), (1152, 2048))
        self.assertEqual(preview_size("1:1"), (2048, 2048))

    def test_reads_carriage_return_steps_and_stops_a_silent_process(self):
        with tempfile.TemporaryDirectory() as folder:
            stop = Event()
            lines = []

            def on_line(line):
                lines.append(line)
                stop.set()

            command = [sys.executable, "-c", 'import sys,time; sys.stdout.write(" |==>| 2/6 - 1.30s/it\\r"); '
                       'sys.stdout.flush(); time.sleep(60)']
            self.assertIsNone(run_sd_cli(command, Path(folder), stop.is_set, on_line))
            self.assertEqual(lines, [" |==>| 2/6 - 1.30s/it"])

    def test_local_worker_reuses_queue_prompts_and_isolates_output(self):
        project = Path(stock_batch_manager.__file__).parent
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copytree(project / "schemas", root / "schemas")
            shutil.copytree(project / "config/prompts", root / "config/prompts")
            source = json.loads((project / TEST_QUEUE).read_text(encoding="utf-8-sig"))
            source["assets"] = source["assets"][:2]
            for asset, ratio in zip(source["assets"], ("16:9", "9:16")):
                asset.update(aspect_ratio=ratio, prompt=f"Illustration for {ratio}", negative_prompt="No text")
            queue_path = root / TEST_QUEUE
            queue_path.parent.mkdir(parents=True)
            queue_path.write_text(json.dumps(source), encoding="utf-8")
            run_dir = new_run_dir(root)
            placeholder = root / "mock-model.gguf"
            placeholder.touch()
            settings = dict.fromkeys(("LOCAL_SD_CLI", "LOCAL_H3_DIT", "LOCAL_H3_TEXT_ENCODER", "LOCAL_H3_VAE"),
                                     str(placeholder))
            calls = []

            def generate(args, cwd, should_stop, on_line):
                self.assertEqual(args[args.index("--backend") + 1], "diffusion=cuda0,te=cpu,vae=cuda0")
                self.assertEqual(args[args.index("--max-vram") + 1], "5")
                self.assertIn("--mmap", args)
                self.assertIn("--sage-attn", args)
                self.assertEqual(args[args.index("--vae-tile-size") + 1], "1024x1024")
                width, height = (int(args[args.index(flag) + 1]) for flag in ("-W", "-H"))
                prompt = Path(args[args.index("--prompt-file") + 1]).read_text(encoding="utf-8")
                self.assertIn("Negative prompt:\nNo text", prompt)
                on_line(" |######| 6/6 - 1000.00MB/s\x1b[K")
                calls.append((width, height, prompt.splitlines()[0]))
                output = args[args.index("-o") + 1]
                script = ('import sys; from PIL import Image; '
                          'print(" |==>| 2/6 - 1s/it\\r |====>| 6/6 - 1s/it", flush=True); '
                          'Image.new("RGB", (int(sys.argv[1]), int(sys.argv[2]))).save(sys.argv[3])')
                return run_sd_cli([sys.executable, "-c", script, str(width), str(height), output], cwd,
                                  should_stop, on_line)

            output = io.StringIO()
            with (patch.object(stock_batch_manager, "ROOT", root), patch.dict("os.environ", settings),
                  patch("stock_pipeline.local_image_test.run_sd_cli", side_effect=generate),
                  patch("stock_batch_manager.git_sync") as git_sync,
                  patch("stock_pipeline.batch_manager.run_manager") as production, patch("sys.stdout", output)):
                code = stock_batch_manager.worker(run_dir, None, None, max_images=2, local_queue=queue_path)
            self.assertEqual(code, 0, output.getvalue())
            git_sync.assert_not_called()
            production.assert_not_called()
            self.assertEqual(calls, [(2048, 1152, "Illustration for 16:9"), (1152, 2048, "Illustration for 9:16")])
            self.assertEqual(len(list((run_dir / "local_images").glob("*.png"))), 2)
            from stock_pipeline.manager_gallery import saved_images

            previews = saved_images(root)
            self.assertEqual([(item.queue, item.lane, item.theme) for item in previews],
                             [(TEST_QUEUE, "Local", asset["theme"]) for asset in source["assets"]])
            self.assertFalse((root / "data").exists())
            self.assertFalse((root / "output/assets").exists())
            events = [json.loads(line) for line in output.getvalue().splitlines()]
            progress = [event["asset_progress"] for event in events if "asset_progress" in event]
            self.assertEqual([event["step"] for event in progress if event["stage"] == "local_sampling"], [2, 2])
            self.assertEqual([event["step"] for event in progress if event["stage"] == "local_complete"], [6, 6])
