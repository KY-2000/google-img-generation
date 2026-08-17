import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import xhs_wallpaper_workflow as xhs


class XhsInputTests(unittest.TestCase):
    def test_load_xhs_metadata_reads_expected_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target_dir = Path(tmpdir)
            (target_dir / "metadata.json").write_text(
                json.dumps(
                    {
                        "title": "梦幻花朵壁纸",
                        "description": "粉色花朵和柔和光影",
                        "hashtags": ["壁纸", "花朵"],
                        "ignored": "value",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            metadata = xhs.load_xhs_metadata(target_dir)

            self.assertEqual(metadata["title"], "梦幻花朵壁纸")
            self.assertEqual(metadata["description"], "粉色花朵和柔和光影")
            self.assertEqual(metadata["hashtags"], ["壁纸", "花朵"])

    def test_load_xhs_metadata_defaults_missing_optional_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target_dir = Path(tmpdir)
            (target_dir / "metadata.json").write_text("{}", encoding="utf-8")

            metadata = xhs.load_xhs_metadata(target_dir)

            self.assertEqual(metadata, {"title": "", "description": "", "hashtags": []})

    def test_list_xhs_input_images_returns_supported_images_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target_dir = Path(tmpdir)
            (target_dir / "b.jpg").write_bytes(b"jpg")
            (target_dir / "a.png").write_bytes(b"png")
            (target_dir / "c.webp").write_bytes(b"webp")
            (target_dir / "metadata.json").write_text("{}", encoding="utf-8")
            (target_dir / "notes.txt").write_text("ignore", encoding="utf-8")

            images = xhs.list_xhs_input_images(target_dir)

            self.assertEqual([image.name for image in images], ["a.png", "b.jpg", "c.webp"])

    def test_get_target_output_name_uses_folder_name(self):
        self.assertEqual(
            xhs.get_target_output_name(Path(r"C:\Users\tanke\xhs-files-downloads\abc")),
            "abc",
        )


class XhsPromptTests(unittest.TestCase):
    def test_build_wallpaper_reverse_user_prompt_includes_metadata_and_noise_rules(self):
        prompt = xhs.build_wallpaper_reverse_user_prompt(
            {
                "title": "星空壁纸",
                "description": "蓝色夜空",
                "hashtags": ["星空", "壁纸"],
            },
            [Path("one.png"), Path("two.jpg")],
        )

        self.assertIn("星空壁纸", prompt)
        self.assertIn("蓝色夜空", prompt)
        self.assertIn("星空, 壁纸", prompt)
        self.assertIn("one.png", prompt)
        self.assertIn("two.jpg", prompt)
        self.assertIn("忽略", prompt)
        self.assertIn("手机", prompt)
        self.assertIn("通知", prompt)
        self.assertIn("中文", prompt)


    def test_build_wallpaper_reverse_user_prompt_uses_reference_style_template(self):
        prompt = xhs.build_wallpaper_reverse_user_prompt(
            {
                "title": "cotton candy sea",
                "description": "soft pastel wallpaper",
                "hashtags": ["wallpaper", "pastel"],
            },
            [Path("one.png")],
        )

        self.assertIn("\u8bf7\u628a\u6211\u4e0a\u4f20\u7684\u56fe\u7247\u4f5c\u4e3a\u552f\u4e00\u98ce\u683c\u53c2\u8003", prompt)
        self.assertIn("\u753b\u98ce\u7c7b\u578b", prompt)
        self.assertIn("\u914d\u8272\u65b9\u5f0f", prompt)
        self.assertIn("\u5149\u5f71\u6c1b\u56f4", prompt)
        self.assertIn("\u7b14\u89e6/\u7ebf\u6761\u7279\u5f81", prompt)
        self.assertIn("\u8d28\u611f\u4e0e\u6e32\u67d3\u65b9\u5f0f", prompt)
        self.assertIn("\u6784\u56fe\u7279\u70b9", prompt)
        self.assertIn("\u60c5\u7eea\u4e0e\u6c1b\u56f4\u8868\u8fbe", prompt)
        self.assertIn("\u65b0\u56fe\u7247\u5185\u5bb9\u66ff\u6362\u4e3a", prompt)
        self.assertIn("title: cotton candy sea", prompt)
        self.assertIn("description: soft pastel wallpaper", prompt)
        self.assertIn("hashtags: wallpaper, pastel", prompt)
        self.assertIn("\u4e0d\u8981\u76f4\u63a5\u590d\u5236\u539f\u56fe\u4e2d\u7684\u4e3b\u4f53\u3001\u573a\u666f\u3001\u80cc\u666f\u6216\u5177\u4f53\u5143\u7d20", prompt)
        self.assertIn("\u53ea\u590d\u523b\u539f\u56fe\u7684\u98ce\u683c\u3001\u8d28\u611f\u3001\u8272\u5f69\u3001\u5149\u5f71\u548c\u6574\u4f53\u89c6\u89c9\u8bed\u8a00", prompt)
        self.assertIn("\u753b\u9762\u5b8c\u6574\u3001\u7f8e\u89c2\u3001\u81ea\u7136\uff0c\u7ec6\u8282\u4e30\u5bcc\uff0c\u8d28\u91cf\u9ad8", prompt)


class XhsConfigTests(unittest.TestCase):
    def test_get_generation_defaults_uses_wallpaper_defaults(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = xhs.get_generation_defaults(Path(tmpdir))

            self.assertEqual(config["count"], 4)
            self.assertEqual(config["resolution"], "4K")
            self.assertEqual(config["aspect_ratio"], "9:16")

    def test_parse_args_cli_overrides_env_defaults(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            (project_root / ".env").write_text(
                "IMAGE_COUNT=2\nIMAGE_RESOLUTION=2K\nIMAGE_ASPECT_RATIO=1:1\n",
                encoding="utf-8",
            )

            args = xhs.parse_args(
                ["input-folder", "--count", "6", "--resolution", "4K", "--aspect-ratio", "9:16"],
                project_root=project_root,
            )

            self.assertEqual(args.count, 6)
            self.assertEqual(args.resolution, "4K")
            self.assertEqual(args.aspect_ratio, "9:16")

    def test_parse_args_uses_vertex_user_facing_provider_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            args = xhs.parse_args(["input-folder"], project_root=Path(tmpdir))

            self.assertEqual(args.prompt_provider, "vertex")
            self.assertEqual(args.metadata_provider, "vertex")

    def test_parse_args_normalizes_legacy_google_provider_env_to_vertex(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            (project_root / ".env").write_text(
                "PROMPT_PROVIDER=google\nMETADATA_PROVIDER=google\n",
                encoding="utf-8",
            )

            args = xhs.parse_args(["input-folder"], project_root=project_root)

            self.assertEqual(args.prompt_provider, "vertex")
            self.assertEqual(args.metadata_provider, "vertex")

    def test_map_provider_for_main_maps_vertex_to_google_internal_provider(self):
        self.assertEqual(xhs.map_provider_for_main("vertex"), "google")
        self.assertEqual(xhs.map_provider_for_main("openrouter"), "openrouter")
        self.assertEqual(xhs.map_provider_for_main("nim"), "nim")


class XhsWorkflowTests(unittest.TestCase):
    @mock.patch("xhs_wallpaper_workflow.run_generation_pipeline")
    @mock.patch("xhs_wallpaper_workflow.generate_chinese_wallpaper_prompt")
    def test_run_xhs_workflow_saves_prompt_and_passes_configurable_generation_settings(
        self,
        mock_generate_prompt,
        mock_run_generation,
    ):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "repo"
            target_dir = Path(tmpdir) / "source-folder"
            project_root.mkdir()
            target_dir.mkdir()
            (target_dir / "metadata.json").write_text("{}", encoding="utf-8")
            (target_dir / "img.png").write_bytes(b"png")
            mock_generate_prompt.return_value = "中文壁纸提示词"
            expected_run_dir = project_root / "output" / "source-folder"
            mock_run_generation.return_value = expected_run_dir

            run_dir = xhs.run_xhs_workflow(
                target_dir=target_dir,
                project_root=project_root,
                prompt_provider="vertex",
                prompt_model="gemini-2.5-flash",
                image_model="gemini-3-pro-image-preview",
                metadata_provider="vertex",
                metadata_model="gemini-2.5-flash",
                count=5,
                aspect_ratio="9:16",
                resolution="4K",
                temperature=1.0,
                top_p=0.95,
                overwrite=False,
            )

            self.assertEqual(run_dir, expected_run_dir)
            self.assertEqual(
                (project_root / "output" / "source-folder" / "chinese_prompt.txt").read_text(
                    encoding="utf-8"
                ),
                "中文壁纸提示词",
            )
            call_kwargs = mock_run_generation.call_args.kwargs
            self.assertEqual(call_kwargs["prompt"], "中文壁纸提示词")
            self.assertEqual(call_kwargs["count"], 5)
            self.assertEqual(call_kwargs["aspect_ratio"], "9:16")
            self.assertEqual(call_kwargs["resolution"], "4K")
            self.assertEqual(call_kwargs["metadata_provider"], "google")
            self.assertFalse(call_kwargs["remove_background"])

    @mock.patch("xhs_wallpaper_workflow.run_generation_pipeline")
    @mock.patch("xhs_wallpaper_workflow.generate_chinese_wallpaper_prompt")
    def test_run_xhs_workflow_writes_stage_log_before_prompt_reversal(
        self,
        mock_generate_prompt,
        mock_run_generation,
    ):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "repo"
            target_dir = Path(tmpdir) / "source-folder"
            project_root.mkdir()
            target_dir.mkdir()
            (target_dir / "metadata.json").write_text("{}", encoding="utf-8")
            (target_dir / "img.png").write_bytes(b"png")
            mock_generate_prompt.side_effect = RuntimeError("prompt provider failed")

            with self.assertRaisesRegex(RuntimeError, "prompt provider failed"):
                xhs.run_xhs_workflow(
                    target_dir=target_dir,
                    project_root=project_root,
                    prompt_provider="vertex",
                    prompt_model="gemini-2.5-flash",
                    image_model="gemini-3-pro-image-preview",
                    metadata_provider="vertex",
                    metadata_model="gemini-2.5-flash",
                    count=5,
                    aspect_ratio="9:16",
                    resolution="4K",
                    temperature=1.0,
                    top_p=0.95,
                    overwrite=False,
                )

            log_text = (
                project_root / "output" / "source-folder" / "xhs_workflow_log.txt"
            ).read_text(encoding="utf-8")
            self.assertIn("Loaded XHS inputs", log_text)
            self.assertIn("Starting prompt reversal", log_text)
            self.assertIn("XHS workflow failed", log_text)
            mock_run_generation.assert_not_called()


if __name__ == "__main__":
    unittest.main()
