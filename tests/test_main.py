import tempfile
import unittest
from unittest import mock
from pathlib import Path
import json
import shutil

import main


class EnvLoadingTests(unittest.TestCase):
    def test_load_env_file_reads_google_cloud_project(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text(
                "GOOGLE_CLOUD_PROJECT=test-project\nOTHER_VALUE=ignored\n",
                encoding="utf-8",
            )

            env_vars = main.load_env_file(env_path)

            self.assertEqual(env_vars["GOOGLE_CLOUD_PROJECT"], "test-project")

    def test_get_vertex_ai_config_reads_project_and_location_from_env_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            env_path = project_root / ".env"
            env_path.write_text(
                "GOOGLE_CLOUD_PROJECT=test-project\nGOOGLE_CLOUD_LOCATION=us-central1\n",
                encoding="utf-8",
            )

            config = main.get_vertex_ai_config(project_root)

            self.assertEqual(
                config,
                {
                    "project": "test-project",
                    "location": "us-central1",
                },
            )

    def test_get_google_auth_mode_reads_env_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            env_path = project_root / ".env"
            env_path.write_text(
                "GOOGLE_AUTH_MODE=api_key\n",
                encoding="utf-8",
            )

            auth_mode = main.get_google_auth_mode(project_root)

            self.assertEqual(auth_mode, "api_key")

    def test_get_google_api_key_reads_env_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            env_path = project_root / ".env"
            env_path.write_text(
                "GOOGLE_API_KEY=test-google-key\n",
                encoding="utf-8",
            )

            api_key = main.get_google_api_key(project_root)

            self.assertEqual(api_key, "test-google-key")

    def test_get_default_image_model_reads_image_model_from_env_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            env_path = project_root / ".env"
            env_path.write_text(
                "IMAGE_MODEL=nanobanana2\n",
                encoding="utf-8",
            )

            model = main.get_default_image_model(project_root)

            self.assertEqual(model, "gemini-3.1-flash-image-preview")

    def test_get_default_metadata_provider_reads_env_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            env_path = project_root / ".env"
            env_path.write_text(
                "METADATA_PROVIDER=openrouter\n",
                encoding="utf-8",
            )

            provider = main.get_default_metadata_provider(project_root)

            self.assertEqual(provider, "openrouter")

    def test_get_default_metadata_model_reads_env_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            env_path = project_root / ".env"
            env_path.write_text(
                "METADATA_MODEL=qwenfree\n",
                encoding="utf-8",
            )

            model = main.get_default_metadata_model(project_root)

            self.assertEqual(model, "qwen/qwen3.6-plus:free")

    def test_get_nim_api_key_reads_env_file(self):
        project_root = Path.cwd()
        temp_root = project_root / "output" / "test-env-nim-key"
        shutil.rmtree(temp_root, ignore_errors=True)
        temp_root.mkdir(parents=True)
        env_path = temp_root / ".env"
        env_path.write_text(
            "NIM_API_KEY=test-nim-key\n",
            encoding="utf-8",
        )

        try:
            api_key = main.get_nim_api_key(temp_root)
            self.assertEqual(api_key, "test-nim-key")
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)


class ModelAliasTests(unittest.TestCase):
    def test_resolve_image_model_alias_maps_nanobanana2(self):
        self.assertEqual(
            main.resolve_image_model_alias("nanobanana2"),
            "gemini-3.1-flash-image-preview",
        )

    def test_resolve_image_model_alias_maps_nanobananapro(self):
        self.assertEqual(
            main.resolve_image_model_alias("nanobananapro"),
            "gemini-3-pro-image-preview",
        )

    def test_resolve_image_model_alias_keeps_full_model_name(self):
        self.assertEqual(
            main.resolve_image_model_alias("gemini-3-pro-image-preview"),
            "gemini-3-pro-image-preview",
        )

    def test_resolve_metadata_model_alias_maps_qwenfree(self):
        self.assertEqual(
            main.resolve_metadata_model_alias("qwenfree"),
            "qwen/qwen3.6-plus:free",
        )

    def test_resolve_metadata_model_alias_maps_minimaxfree(self):
        self.assertEqual(
            main.resolve_metadata_model_alias("minimaxfree"),
            "minimax/minimax-m2.5:free",
        )

    def test_resolve_metadata_model_alias_maps_nemotronsuperfree(self):
        self.assertEqual(
            main.resolve_metadata_model_alias("nemotronsuperfree"),
            "nvidia/nemotron-3-super-120b-a12b:free",
        )

    def test_resolve_metadata_model_alias_maps_kimik25(self):
        self.assertEqual(
            main.resolve_metadata_model_alias("kimik25"),
            "moonshotai/kimi-k2.5",
        )


class OutputLayoutTests(unittest.TestCase):
    def test_create_run_output_dir_creates_timestamped_subdir_in_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)

            run_dir = main.create_run_output_dir(project_root, timestamp="20260403-120000")

            self.assertEqual(run_dir, project_root / "output" / "20260403-120000")
            self.assertTrue(run_dir.is_dir())

    def test_save_prompt_writes_prompt_txt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)

            prompt_path = main.save_prompt(run_dir, "hello world")

            self.assertEqual(prompt_path.name, "prompt.txt")
            self.assertEqual(prompt_path.read_text(encoding="utf-8"), "hello world")

    def test_save_run_config_writes_generation_parameters_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)

            config_path = main.save_run_config(
                run_dir=run_dir,
                config={
                    "model": "gemini-3-pro-image-preview",
                    "metadata_model": "gemini-2.5-flash",
                    "temperature": 0.9,
                    "top_p": 0.8,
                    "count": 3,
                    "aspect_ratio": "16:9",
                    "resolution": "2K",
                },
            )

            self.assertEqual(
                config_path.read_text(encoding="utf-8"),
                '{\n  "aspect_ratio": "16:9",\n  "count": 3,\n  "metadata_model": "gemini-2.5-flash",\n  "model": "gemini-3-pro-image-preview",\n  "resolution": "2K",\n  "temperature": 0.9,\n  "top_p": 0.8\n}',
            )

    def test_create_temp_png_path_returns_closed_writable_path(self):
        temp_path = main.create_temp_png_path()

        try:
            self.assertEqual(temp_path.suffix, ".png")
            self.assertFalse(temp_path.exists())
            temp_path.write_bytes(b"test")
            self.assertEqual(temp_path.read_bytes(), b"test")
        finally:
            temp_path.unlink(missing_ok=True)

    def test_find_missing_background_removals_returns_only_unprocessed_images(self):
        output_root = Path.cwd() / "output" / "test-rembg-scan"
        shutil.rmtree(output_root, ignore_errors=True)
        run_dir = output_root / "20260407-153000"
        run_dir.mkdir(parents=True)

        try:
            (run_dir / "img-1.png").write_bytes(b"one")
            (run_dir / "img-2.png").write_bytes(b"two")
            (run_dir / "img-rembg-20260407-153000-1.png").write_bytes(b"done")
            (run_dir / "notes.txt").write_text("ignore", encoding="utf-8")

            pending = main.find_missing_background_removals(output_root)

            self.assertEqual(
                pending,
                [
                    (
                        run_dir / "img-2.png",
                        run_dir / "img-rembg-20260407-153000-2.png",
                    )
                ],
            )
        finally:
            shutil.rmtree(output_root, ignore_errors=True)

    def test_list_original_images_in_run_dir_ignores_rembg_files(self):
        run_dir = Path.cwd() / "output" / "test-compare-run" / "20260413-120000"
        shutil.rmtree(run_dir.parent, ignore_errors=True)
        run_dir.mkdir(parents=True)

        try:
            (run_dir / "img-1.png").write_bytes(b"one")
            (run_dir / "img-2.png").write_bytes(b"two")
            (run_dir / "img-rembg-20260413-120000-1.png").write_bytes(b"done")
            (run_dir / "prompt.txt").write_text("prompt", encoding="utf-8")

            originals = main.list_original_images_in_run_dir(run_dir)

            self.assertEqual(
                originals,
                [
                    run_dir / "img-1.png",
                    run_dir / "img-2.png",
                ],
            )
        finally:
            shutil.rmtree(run_dir.parent, ignore_errors=True)


class ComparisonHelperTests(unittest.TestCase):
    def test_parse_model_spec_splits_provider_and_model(self):
        self.assertEqual(
            main.parse_model_spec("google:gemini-2.5-flash"),
            ("google", "gemini-2.5-flash"),
        )


class MetadataPromptTests(unittest.TestCase):
    def test_build_metadata_user_prompt_embeds_generation_prompt_and_categories(self):
        user_prompt = main.build_metadata_user_prompt(
            generation_prompt="sunlit oranges on a white table",
            category_list="1. Animals\n7. Food",
        )

        self.assertIn("Analyze this image for Adobe Stock metadata.", user_prompt)
        self.assertIn("sunlit oranges on a white table", user_prompt)
        self.assertIn("1. Animals\n7. Food", user_prompt)

    def test_build_openrouter_metadata_payload_includes_text_and_image(self):
        payload = main.build_openrouter_metadata_payload(
            model="qwen/qwen3.6-plus:free",
            system_prompt="system",
            user_prompt="user",
            image_data_url="data:image/png;base64,abc",
            temperature=0.7,
            top_p=0.85,
        )

        self.assertEqual(payload["model"], "qwen/qwen3.6-plus:free")
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(payload["messages"][1]["content"][0]["type"], "text")
        self.assertEqual(payload["messages"][1]["content"][1]["type"], "image_url")
        self.assertEqual(
            payload["messages"][1]["content"][1]["image_url"]["url"],
            "data:image/png;base64,abc",
        )

    def test_build_nim_metadata_payload_includes_text_and_image(self):
        payload = main.build_nim_metadata_payload(
            model="moonshotai/kimi-k2.5",
            system_prompt="system",
            user_prompt="user",
            image_data_url="data:image/png;base64,abc",
            temperature=0.7,
            top_p=0.85,
        )

        self.assertEqual(payload["model"], "moonshotai/kimi-k2.5")
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(payload["messages"][1]["content"][0]["type"], "text")
        self.assertEqual(payload["messages"][1]["content"][1]["type"], "image_url")


class MetadataParsingTests(unittest.TestCase):
    def test_parse_metadata_response_extracts_upload_fields(self):
        response_text = (
            "Title: Tropical fruit arrangement on white background\n"
            "Keywords: tropical fruit, fruit, fresh\n"
            "Category Code: 7\n"
            "Category Name: Food\n"
        )

        metadata = main.parse_metadata_response(response_text)

        self.assertEqual(metadata["Title"], "Tropical fruit arrangement on white background")
        self.assertEqual(metadata["Keywords"], "tropical fruit, fruit, fresh")
        self.assertEqual(metadata["Category"], "7")

    def test_parse_metadata_response_rejects_missing_required_field(self):
        with self.assertRaisesRegex(RuntimeError, "Category Name"):
            main.parse_metadata_response(
                "Title: Example only\n"
                "Keywords: keyword one, keyword two\n"
                "Category Code: 3\n"
            )


class MetadataOutputTests(unittest.TestCase):
    def test_save_metadata_outputs_writes_text_and_csv_files(self):
        response_text = (
            "Title: Young woman recording vlog in home studio\n"
            "Keywords: camera, content creator, recording\n"
            "Category Code: 13\n"
            "Category Name: People\n"
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)

            metadata_txt_path, metadata_csv_path = main.save_metadata_outputs(
                run_dir=run_dir,
                rows=[
                    {
                        "image_filename": "img-rembg-20260403-120000.png",
                        "response_text": response_text,
                    }
                ],
            )

            self.assertEqual(
                metadata_txt_path.read_text(encoding="utf-8"),
                response_text.strip(),
            )
            csv_lines = metadata_csv_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(csv_lines[0], "Filename,Title,Keywords,Category,Releases")
            self.assertIn("img-rembg-20260403-120000.png", csv_lines[1])
            self.assertIn("Young woman recording vlog in home studio", csv_lines[1])
            self.assertIn(",13,", csv_lines[1])

    def test_save_metadata_outputs_writes_multiple_rows(self):
        response_text = (
            "Title: Young woman recording vlog in home studio\n"
            "Keywords: camera, content creator, recording\n"
            "Category Code: 13\n"
            "Category Name: People\n"
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)

            main.save_metadata_outputs(
                run_dir=run_dir,
                rows=[
                    {
                        "image_filename": "img-rembg-20260403-120000-1.png",
                        "response_text": response_text,
                    },
                    {
                        "image_filename": "img-rembg-20260403-120000-2.png",
                        "response_text": response_text,
                    },
                ],
            )

            csv_lines = (run_dir / "adobe_stock_metadata.csv").read_text(
                encoding="utf-8"
            ).splitlines()
            self.assertEqual(len(csv_lines), 3)
            self.assertIn("img-rembg-20260403-120000-1.png", csv_lines[1])
            self.assertIn("img-rembg-20260403-120000-2.png", csv_lines[2])


class ArgParsingTests(unittest.TestCase):
    def test_default_image_generation_model_is_gemini_3_pro_image_preview(self):
        args = main.parse_args(["example prompt"], project_root=Path("/tmp/project"))

        self.assertEqual(args.model, "gemini-3-pro-image-preview")

    def test_parse_args_accepts_generation_parameters(self):
        args = main.parse_args(
            [
                "example prompt",
                "--metadata-provider",
                "nim",
                "--metadata-model",
                "kimik25",
                "--temperature",
                "0.7",
                "--top-p",
                "0.85",
                "--count",
                "4",
                "--aspect-ratio",
                "16:9",
                "--resolution",
                "2K",
            ],
            project_root=Path("/tmp/project"),
        )

        self.assertEqual(args.metadata_provider, "nim")
        self.assertEqual(args.metadata_model, "moonshotai/kimi-k2.5")
        self.assertEqual(args.temperature, 0.7)
        self.assertEqual(args.top_p, 0.85)
        self.assertEqual(args.count, 4)
        self.assertEqual(args.aspect_ratio, "16:9")
        self.assertEqual(args.resolution, "2K")

    def test_parse_args_resolves_model_alias(self):
        args = main.parse_args(
            ["example prompt", "--model", "nanobananapro"],
            project_root=Path("/tmp/project"),
        )

        self.assertEqual(args.model, "gemini-3-pro-image-preview")

    def test_parse_args_resolves_metadata_model_alias(self):
        args = main.parse_args(
            [
                "example prompt",
                "--metadata-model",
                "nemotronsuperfree",
            ],
            project_root=Path("/tmp/project"),
        )

        self.assertEqual(args.metadata_model, "nvidia/nemotron-3-super-120b-a12b:free")


class RunFlowTests(unittest.TestCase):
    @mock.patch("main.save_metadata_outputs")
    @mock.patch("main.generate_metadata_text")
    @mock.patch("main.remove_background_ffmpeg")
    @mock.patch("main.save_png_from_bytes")
    @mock.patch("main.generate_image_bytes")
    @mock.patch("main.save_run_config")
    @mock.patch("main.save_prompt")
    @mock.patch("main.create_run_output_dir")
    @mock.patch("main.load_text_asset")
    @mock.patch("main.build_nim_metadata_client")
    @mock.patch("main.build_google_client")
    @mock.patch("main.get_nim_api_key")
    @mock.patch("main.get_google_auth_mode")
    @mock.patch("main.get_vertex_ai_config")
    def test_run_uses_nim_client_for_metadata_when_selected(
        self,
        mock_get_vertex_config,
        mock_get_google_auth_mode,
        mock_get_nim_key,
        mock_build_google_client,
        mock_build_nim_client,
        mock_load_text_asset,
        mock_create_run_output_dir,
        mock_save_prompt,
        mock_save_run_config,
        mock_generate_image_bytes,
        mock_save_png_from_bytes,
        mock_remove_background_ffmpeg,
        mock_generate_metadata_text,
        mock_save_metadata_outputs,
    ):
        project_root = Path.cwd()
        run_dir = project_root / "output" / "20260403-120000-nim"
        shutil.rmtree(run_dir, ignore_errors=True)
        run_dir.mkdir(parents=True)
        mock_create_run_output_dir.return_value = run_dir
        mock_get_google_auth_mode.return_value = "adc"
        mock_get_vertex_config.return_value = {
            "project": "test-project",
            "location": "global",
        }
        mock_get_nim_key.return_value = "test-nim-key"
        mock_load_text_asset.side_effect = ["system prompt", "1. Animals\n7. Food"]
        mock_generate_image_bytes.return_value = b"fake-image"
        mock_generate_metadata_text.return_value = (
            "Title: Example\n"
            "Keywords: one, two\n"
            "Category Code: 7\n"
            "Category Name: Food"
        )
        nim_client = object()
        mock_build_nim_client.return_value = nim_client

        try:
            main.run(
                prompt="example prompt",
                model="gemini-3-pro-image-preview",
                metadata_provider="nim",
                metadata_model="moonshotai/kimi-k2.5",
                project_root=project_root,
                temperature=0.7,
                top_p=0.85,
                count=1,
                aspect_ratio="16:9",
                resolution="2K",
            )

            mock_get_nim_key.assert_called_once_with(project_root)
            mock_build_nim_client.assert_called_once()
            self.assertEqual(
                mock_generate_metadata_text.call_args.kwargs["provider"],
                "nim",
            )
            self.assertIs(
                mock_generate_metadata_text.call_args.kwargs["client"],
                nim_client,
            )
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)

    @mock.patch("main.time.sleep")
    @mock.patch("main.save_metadata_outputs")
    @mock.patch("main.generate_metadata_text")
    @mock.patch("main.remove_background_ffmpeg")
    @mock.patch("main.save_png_from_bytes")
    @mock.patch("main.generate_image_bytes")
    @mock.patch("main.save_run_config")
    @mock.patch("main.save_prompt")
    @mock.patch("main.create_run_output_dir")
    @mock.patch("main.load_text_asset")
    @mock.patch("main.build_google_client")
    @mock.patch("main.get_google_auth_mode")
    @mock.patch("main.get_vertex_ai_config")
    def test_run_writes_text_and_json_session_logs(
        self,
        mock_get_vertex_config,
        mock_get_google_auth_mode,
        mock_build_google_client,
        mock_load_text_asset,
        mock_create_run_output_dir,
        mock_save_prompt,
        mock_save_run_config,
        mock_generate_image_bytes,
        mock_save_png_from_bytes,
        mock_remove_background_ffmpeg,
        mock_generate_metadata_text,
        mock_save_metadata_outputs,
        mock_sleep,
    ):
        project_root = Path.cwd()
        run_dir = project_root / "output" / "test-session-log"
        shutil.rmtree(run_dir, ignore_errors=True)
        run_dir.mkdir(parents=True)
        mock_create_run_output_dir.return_value = run_dir
        mock_get_google_auth_mode.return_value = "adc"
        mock_get_vertex_config.return_value = {
            "project": "test-project",
            "location": "global",
        }
        mock_load_text_asset.side_effect = ["system prompt", "1. Animals\n7. Food"]
        mock_generate_image_bytes.return_value = b"fake-image"
        mock_generate_metadata_text.return_value = (
            "Title: Example\n"
            "Keywords: one, two\n"
            "Category Code: 7\n"
            "Category Name: Food"
        )

        try:
            main.run(
                prompt="example prompt",
                model="gemini-3-pro-image-preview",
                metadata_model="gemini-2.5-flash",
                project_root=project_root,
                temperature=0.7,
                top_p=0.85,
                count=1,
                aspect_ratio="16:9",
                resolution="2K",
            )

            text_log = (run_dir / "session_log.txt").read_text(encoding="utf-8")
            json_log = json.loads((run_dir / "session_log.json").read_text(encoding="utf-8"))

            self.assertIn("Run started", text_log)
            self.assertIn("Session 1 completed", text_log)
            self.assertEqual(json_log["status"], "success")
            self.assertEqual(len(json_log["sessions"]), 1)
            self.assertEqual(json_log["sessions"][0]["status"], "success")
            self.assertIn("image_generation", json_log["sessions"][0]["steps"])
            self.assertIn("background_removal", json_log["sessions"][0]["steps"])
            self.assertIn("metadata_generation", json_log["sessions"][0]["steps"])
            mock_sleep.assert_not_called()
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)

    @mock.patch("main.time.sleep")
    @mock.patch("main.save_metadata_outputs")
    @mock.patch("main.generate_metadata_text")
    @mock.patch("main.remove_background_ffmpeg")
    @mock.patch("main.save_png_from_bytes")
    @mock.patch("main.generate_image_bytes")
    @mock.patch("main.save_run_config")
    @mock.patch("main.save_prompt")
    @mock.patch("main.create_run_output_dir")
    @mock.patch("main.load_text_asset")
    @mock.patch("main.build_google_client")
    @mock.patch("main.get_google_auth_mode")
    @mock.patch("main.get_vertex_ai_config")
    def test_run_retries_retryable_image_generation_errors_and_logs_wait_time(
        self,
        mock_get_vertex_config,
        mock_get_google_auth_mode,
        mock_build_google_client,
        mock_load_text_asset,
        mock_create_run_output_dir,
        mock_save_prompt,
        mock_save_run_config,
        mock_generate_image_bytes,
        mock_save_png_from_bytes,
        mock_remove_background_ffmpeg,
        mock_generate_metadata_text,
        mock_save_metadata_outputs,
        mock_sleep,
    ):
        class RetryableError(Exception):
            pass

        project_root = Path.cwd()
        run_dir = project_root / "output" / "test-session-log-retry"
        shutil.rmtree(run_dir, ignore_errors=True)
        run_dir.mkdir(parents=True)
        mock_create_run_output_dir.return_value = run_dir
        mock_get_google_auth_mode.return_value = "adc"
        mock_get_vertex_config.return_value = {
            "project": "test-project",
            "location": "global",
        }
        mock_load_text_asset.side_effect = ["system prompt", "1. Animals\n7. Food"]
        mock_generate_image_bytes.side_effect = [
            RetryableError("429 RESOURCE_EXHAUSTED"),
            b"fake-image",
        ]
        mock_generate_metadata_text.return_value = (
            "Title: Example\n"
            "Keywords: one, two\n"
            "Category Code: 7\n"
            "Category Name: Food"
        )

        try:
            main.run(
                prompt="example prompt",
                model="gemini-3-pro-image-preview",
                metadata_model="gemini-2.5-flash",
                project_root=project_root,
                temperature=0.7,
                top_p=0.85,
                count=1,
                aspect_ratio="16:9",
                resolution="2K",
            )

            json_log = json.loads((run_dir / "session_log.json").read_text(encoding="utf-8"))
            image_attempts = json_log["sessions"][0]["steps"]["image_generation"]["attempts"]

            self.assertEqual(mock_generate_image_bytes.call_count, 2)
            mock_sleep.assert_called_once_with(1.0)
            self.assertEqual(image_attempts[0]["status"], "error")
            self.assertEqual(image_attempts[0]["retry_wait_seconds"], 1.0)
            self.assertEqual(image_attempts[1]["status"], "success")
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)

    @mock.patch("main.save_metadata_outputs")
    @mock.patch("main.generate_metadata_text")
    @mock.patch("main.remove_background_ffmpeg")
    @mock.patch("main.save_png_from_bytes")
    @mock.patch("main.generate_image_bytes")
    @mock.patch("main.save_run_config")
    @mock.patch("main.save_prompt")
    @mock.patch("main.create_run_output_dir")
    @mock.patch("main.load_text_asset")
    @mock.patch("main.build_google_client")
    @mock.patch("main.get_google_auth_mode")
    @mock.patch("main.get_vertex_ai_config")
    def test_run_generates_multiple_images_sequentially(
        self,
        mock_get_vertex_config,
        mock_get_google_auth_mode,
        mock_build_google_client,
        mock_load_text_asset,
        mock_create_run_output_dir,
        mock_save_prompt,
        mock_save_run_config,
        mock_generate_image_bytes,
        mock_save_png_from_bytes,
        mock_remove_background_ffmpeg,
        mock_generate_metadata_text,
        mock_save_metadata_outputs,
    ):
        project_root = Path.cwd()
        run_dir = project_root / "output" / "20260403-120000"
        shutil.rmtree(run_dir, ignore_errors=True)
        run_dir.mkdir(parents=True)
        mock_create_run_output_dir.return_value = run_dir
        mock_get_google_auth_mode.return_value = "adc"
        mock_get_vertex_config.return_value = {
            "project": "test-project",
            "location": "global",
        }
        mock_load_text_asset.side_effect = ["system prompt", "1. Animals\n7. Food"]
        mock_generate_image_bytes.return_value = b"fake-image"
        mock_generate_metadata_text.return_value = (
            "Title: Example\n"
            "Keywords: one, two\n"
            "Category Code: 7\n"
            "Category Name: Food"
        )

        try:
            main.run(
                prompt="example prompt",
                model="gemini-3-pro-image-preview",
                metadata_model="gemini-2.5-flash",
                project_root=project_root,
                temperature=0.7,
                top_p=0.85,
                count=2,
                aspect_ratio="16:9",
                resolution="2K",
            )

            self.assertEqual(mock_generate_image_bytes.call_count, 2)
            self.assertEqual(mock_generate_metadata_text.call_count, 2)
            self.assertEqual(mock_generate_image_bytes.call_args_list[0].args[3], 0.7)
            self.assertEqual(mock_generate_image_bytes.call_args_list[0].args[4], 0.85)
            self.assertEqual(mock_generate_image_bytes.call_args_list[0].args[5], "16:9")
            self.assertEqual(mock_generate_image_bytes.call_args_list[0].args[6], "2K")
            self.assertEqual(mock_save_png_from_bytes.call_args_list[0].args[1], run_dir / "img-1.png")
            self.assertEqual(
                mock_remove_background_ffmpeg.call_args_list[1].args[1],
                run_dir / "img-rembg-20260403-120000-2.png",
            )
            mock_save_metadata_outputs.assert_called_once()
            rows = mock_save_metadata_outputs.call_args.kwargs["rows"]
            self.assertEqual(rows[0]["image_filename"], "img-rembg-20260403-120000-1.png")
            self.assertEqual(rows[1]["image_filename"], "img-rembg-20260403-120000-2.png")
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)

    @mock.patch("main.save_metadata_outputs")
    @mock.patch("main.generate_metadata_text")
    @mock.patch("main.remove_background_ffmpeg")
    @mock.patch("main.save_png_from_bytes")
    @mock.patch("main.generate_image_bytes")
    @mock.patch("main.save_run_config")
    @mock.patch("main.save_prompt")
    @mock.patch("main.create_run_output_dir")
    @mock.patch("main.load_text_asset")
    @mock.patch("main.build_openrouter_metadata_client")
    @mock.patch("main.build_google_client")
    @mock.patch("main.get_openrouter_api_key")
    @mock.patch("main.get_google_auth_mode")
    @mock.patch("main.get_vertex_ai_config")
    def test_run_uses_openrouter_client_for_metadata_when_selected(
        self,
        mock_get_vertex_config,
        mock_get_google_auth_mode,
        mock_get_openrouter_key,
        mock_build_google_client,
        mock_build_openrouter_client,
        mock_load_text_asset,
        mock_create_run_output_dir,
        mock_save_prompt,
        mock_save_run_config,
        mock_generate_image_bytes,
        mock_save_png_from_bytes,
        mock_remove_background_ffmpeg,
        mock_generate_metadata_text,
        mock_save_metadata_outputs,
    ):
        project_root = Path.cwd()
        run_dir = project_root / "output" / "20260403-120000-openrouter"
        shutil.rmtree(run_dir, ignore_errors=True)
        run_dir.mkdir(parents=True)
        mock_create_run_output_dir.return_value = run_dir
        mock_get_google_auth_mode.return_value = "adc"
        mock_get_vertex_config.return_value = {
            "project": "test-project",
            "location": "global",
        }
        mock_load_text_asset.side_effect = ["system prompt", "1. Animals\n7. Food"]
        mock_generate_image_bytes.return_value = b"fake-image"
        mock_generate_metadata_text.return_value = (
            "Title: Example\n"
            "Keywords: one, two\n"
            "Category Code: 7\n"
            "Category Name: Food"
        )
        openrouter_client = object()
        mock_build_openrouter_client.return_value = openrouter_client

        try:
            main.run(
                prompt="example prompt",
                model="gemini-3-pro-image-preview",
                metadata_provider="openrouter",
                metadata_model="qwen/qwen3.6-plus:free",
                project_root=project_root,
                temperature=0.7,
                top_p=0.85,
                count=1,
                aspect_ratio="16:9",
                resolution="2K",
            )

            mock_get_openrouter_key.assert_called_once_with(project_root)
            mock_build_openrouter_client.assert_called_once()
            self.assertEqual(
                mock_generate_metadata_text.call_args.kwargs["provider"],
                "openrouter",
            )
            self.assertIs(
                mock_generate_metadata_text.call_args.kwargs["client"],
                openrouter_client,
            )
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)

    @mock.patch("main.save_metadata_outputs")
    @mock.patch("main.generate_metadata_text")
    @mock.patch("main.remove_background_ffmpeg")
    @mock.patch("main.save_png_from_bytes")
    @mock.patch("main.generate_image_bytes")
    @mock.patch("main.save_run_config")
    @mock.patch("main.save_prompt")
    @mock.patch("main.create_run_output_dir")
    @mock.patch("main.load_text_asset")
    @mock.patch("main.build_google_client")
    @mock.patch("main.get_google_api_key")
    @mock.patch("main.get_google_auth_mode")
    @mock.patch("main.get_vertex_ai_config")
    def test_run_uses_google_api_key_mode_when_selected(
        self,
        mock_get_vertex_config,
        mock_get_google_auth_mode,
        mock_get_google_api_key,
        mock_build_google_client,
        mock_load_text_asset,
        mock_create_run_output_dir,
        mock_save_prompt,
        mock_save_run_config,
        mock_generate_image_bytes,
        mock_save_png_from_bytes,
        mock_remove_background_ffmpeg,
        mock_generate_metadata_text,
        mock_save_metadata_outputs,
    ):
        project_root = Path.cwd()
        run_dir = project_root / "output" / "20260403-120000-api-key"
        shutil.rmtree(run_dir, ignore_errors=True)
        run_dir.mkdir(parents=True)
        mock_create_run_output_dir.return_value = run_dir
        mock_get_google_auth_mode.return_value = "api_key"
        mock_get_google_api_key.return_value = "test-google-key"
        mock_load_text_asset.side_effect = ["system prompt", "1. Animals\n7. Food"]
        mock_generate_image_bytes.return_value = b"fake-image"
        mock_generate_metadata_text.return_value = (
            "Title: Example\n"
            "Keywords: one, two\n"
            "Category Code: 7\n"
            "Category Name: Food"
        )

        try:
            main.run(
                prompt="example prompt",
                model="gemini-3-pro-image-preview",
                metadata_model="gemini-2.5-flash",
                project_root=project_root,
                temperature=0.7,
                top_p=0.85,
                count=1,
                aspect_ratio="16:9",
                resolution="2K",
                metadata_provider="google",
            )

            mock_get_google_api_key.assert_called_once_with(project_root)
            mock_build_google_client.assert_called_once_with(api_key="test-google-key")
            mock_get_vertex_config.assert_not_called()
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
