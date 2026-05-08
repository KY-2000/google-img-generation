from pathlib import Path

from main import find_missing_background_removals, remove_background_ffmpeg


def main() -> int:
    project_root = Path(__file__).resolve().parent
    output_root = project_root / "output"
    pending = find_missing_background_removals(output_root)

    processed = 0
    failed = 0

    if not output_root.exists():
        print(f"[INFO] Output folder does not exist: {output_root}")
        return 0

    if not pending:
        print("[INFO] No missing background removals found.")
        return 0

    for original_image_path, rembg_image_path in pending:
        print(f"[PROCESS] {original_image_path} -> {rembg_image_path}")
        try:
            remove_background_ffmpeg(original_image_path, rembg_image_path)
        except Exception as exc:
            failed += 1
            print(f"[ERROR] Failed: {original_image_path} error={exc}")
            continue

        processed += 1
        print(f"[DONE] {rembg_image_path}")

    total_found = len(pending)
    print(
        f"[SUMMARY] missing={total_found} processed={processed} failed={failed} skipped=0"
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
