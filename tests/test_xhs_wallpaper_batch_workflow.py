import csv
import time
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import xhs_wallpaper_batch_workflow as batch


class XhsBatchDiscoveryTests(unittest.TestCase):
    def test_list_valid_xhs_folders_returns_only_direct_children_with_metadata_and_images(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            valid = root / "valid note"
            no_metadata = root / "no metadata"
            no_images = root / "no images"
            nested = root / "valid note" / "nested"
            for folder in (valid, no_metadata, no_images, nested):
                folder.mkdir(parents=True)
            (valid / "metadata.json").write_text("{}", encoding="utf-8")
            (valid / "image_1.jpeg").write_bytes(b"jpeg")
            (nested / "metadata.json").write_text("{}", encoding="utf-8")
            (nested / "nested.png").write_bytes(b"png")
            (no_metadata / "image.png").write_bytes(b"png")
            (no_images / "metadata.json").write_text("{}", encoding="utf-8")

            folders = batch.list_valid_xhs_folders(root)

            self.assertEqual(folders, [valid])


class XhsBatchCsvTests(unittest.TestCase):
    def test_prepare_combined_adobe_upload_rewrites_duplicate_filenames(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            first = project_root / "output" / "first note"
            second = project_root / "output" / "second note"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            for run_dir, title in ((first, "First Title"), (second, "Second Title")):
                (run_dir / "img-1.png").write_bytes(title.encode("utf-8"))
                with (run_dir / "adobe_stock_metadata.csv").open("w", encoding="utf-8", newline="") as file:
                    writer = csv.writer(file)
                    writer.writerow(["Filename", "Title", "Keywords", "Category", "Releases"])
                    writer.writerow(["img-1.png", title, "wallpaper, sky", "11", ""])

            upload_dir = batch.prepare_combined_adobe_upload(
                run_dirs=[first, second],
                batch_dir=project_root / "output" / "_batch_test",
            )

            combined_csv = upload_dir / "adobe_stock_metadata.csv"
            with combined_csv.open(encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(
                [row["Filename"] for row in rows],
                ["first-note-img-1.png", "second-note-img-1.png"],
            )
            self.assertTrue((upload_dir / "first-note-img-1.png").exists())
            self.assertTrue((upload_dir / "second-note-img-1.png").exists())
            self.assertEqual(rows[0]["Title"], "First Title")
            self.assertEqual(rows[1]["Title"], "Second Title")


class XhsBatchWorkflowTests(unittest.TestCase):
    @mock.patch("xhs_wallpaper_batch_workflow.run_adobe_upload")
    @mock.patch("xhs_wallpaper_batch_workflow.run_xhs_workflow")
    def test_run_batch_processes_each_folder_then_uploads_once(
        self,
        mock_run_xhs_workflow,
        mock_run_adobe_upload,
    ):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "repo"
            source_root = Path(tmpdir) / "xhs"
            project_root.mkdir()
            source_root.mkdir()
            note_a = source_root / "note a"
            note_b = source_root / "note b"
            note_a.mkdir()
            note_b.mkdir()
            for note in (note_a, note_b):
                (note / "metadata.json").write_text("{}", encoding="utf-8")
                (note / "image.jpeg").write_bytes(b"jpeg")

            output_a = project_root / "output" / "note a"
            output_b = project_root / "output" / "note b"
            for output_dir, title in ((output_a, "A"), (output_b, "B")):
                output_dir.mkdir(parents=True)
                (output_dir / "img-1.png").write_bytes(title.encode("utf-8"))
                with (output_dir / "adobe_stock_metadata.csv").open("w", encoding="utf-8", newline="") as file:
                    writer = csv.writer(file)
                    writer.writerow(["Filename", "Title", "Keywords", "Category", "Releases"])
                    writer.writerow(["img-1.png", title, "wallpaper", "11", ""])
            mock_run_xhs_workflow.side_effect = [output_a, output_b]

            args = batch.BatchConfig(
                source_root=source_root,
                project_root=project_root,
                prompt_provider="vertex",
                prompt_model="gemini-2.5-flash",
                image_model="gemini-3-pro-image-preview",
                metadata_provider="vertex",
                metadata_model="gemini-2.5-flash",
                count=1,
                aspect_ratio="9:16",
                resolution="4K",
                temperature=1.0,
                top_p=0.95,
                overwrite=True,
                skip_adobe_upload=False,
                adobe_cdp="http://127.0.0.1:9222",
                adobe_user_data_dir=None,
                adobe_file_type="illustrations",
                adobe_mark_ai=True,
                adobe_mark_fictional=False,
                adobe_save_work=True,
                adobe_dry_run=True,
                max_workers=2,
                move_done=True,
            )

            result = batch.run_batch(args)

            self.assertEqual(mock_run_xhs_workflow.call_count, 2)
            self.assertEqual(mock_run_xhs_workflow.call_args_list[0].kwargs["target_dir"], note_a)
            self.assertEqual(mock_run_xhs_workflow.call_args_list[1].kwargs["target_dir"], note_b)
            mock_run_adobe_upload.assert_called_once()
            upload_call = mock_run_adobe_upload.call_args.kwargs
            self.assertEqual(upload_call["csv_path"], result.upload_dir / "adobe_stock_metadata.csv")
            self.assertEqual(upload_call["images_dir"], result.upload_dir)
            self.assertEqual(len(result.run_dirs), 2)
            self.assertFalse(note_a.exists())
            self.assertFalse(note_b.exists())
            self.assertTrue(result.done_dir.name.startswith("DONE_"))
            self.assertTrue((result.done_dir / "note a").exists())
            self.assertTrue((result.done_dir / "note b").exists())

    @mock.patch("xhs_wallpaper_batch_workflow.run_adobe_upload")
    @mock.patch("xhs_wallpaper_batch_workflow.run_xhs_workflow")
    def test_run_batch_uses_parallel_workers_when_requested(
        self,
        mock_run_xhs_workflow,
        mock_run_adobe_upload,
    ):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "repo"
            source_root = Path(tmpdir) / "xhs"
            project_root.mkdir()
            source_root.mkdir()
            notes = []
            outputs = []
            for index in range(3):
                note = source_root / f"note {index}"
                note.mkdir()
                (note / "metadata.json").write_text("{}", encoding="utf-8")
                (note / "image.jpeg").write_bytes(b"jpeg")
                notes.append(note)
                output = project_root / "output" / f"note {index}"
                output.mkdir(parents=True)
                (output / "img-1.png").write_bytes(b"png")
                with (output / "adobe_stock_metadata.csv").open("w", encoding="utf-8", newline="") as file:
                    writer = csv.writer(file)
                    writer.writerow(["Filename", "Title", "Keywords", "Category", "Releases"])
                    writer.writerow(["img-1.png", f"Title {index}", "wallpaper", "11", ""])
                outputs.append(output)

            def slow_workflow(**kwargs):
                time.sleep(0.2)
                return outputs[notes.index(kwargs["target_dir"])]

            mock_run_xhs_workflow.side_effect = slow_workflow
            args = batch.BatchConfig(
                source_root=source_root,
                project_root=project_root,
                prompt_provider="vertex",
                prompt_model="gemini-2.5-flash",
                image_model="gemini-3-pro-image-preview",
                metadata_provider="vertex",
                metadata_model="gemini-2.5-flash",
                count=1,
                aspect_ratio="9:16",
                resolution="4K",
                temperature=1.0,
                top_p=0.95,
                overwrite=True,
                skip_adobe_upload=False,
                adobe_cdp=None,
                adobe_user_data_dir=None,
                adobe_file_type="illustrations",
                adobe_mark_ai=True,
                adobe_mark_fictional=False,
                adobe_save_work=True,
                adobe_dry_run=True,
                max_workers=3,
                move_done=True,
            )

            start = time.perf_counter()
            result = batch.run_batch(args)
            duration = time.perf_counter() - start

            self.assertLess(duration, 0.5)
            self.assertEqual(len(result.run_dirs), 3)
            mock_run_adobe_upload.assert_called_once()


if __name__ == "__main__":
    unittest.main()
