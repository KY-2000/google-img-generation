import argparse
import json
from pathlib import Path

from main import (
    CATEGORY_LIST_PATH,
    METADATA_PROMPT_PATH,
    build_google_client,
    build_nim_metadata_client,
    build_openrouter_metadata_client,
    create_run_output_dir,
    generate_metadata_text,
    get_google_api_key,
    get_google_auth_mode,
    get_nim_api_key,
    get_openrouter_api_key,
    get_vertex_ai_config,
    list_original_images_in_run_dir,
    load_text_asset,
    parse_model_spec,
)


def build_metadata_client(provider: str, project_root: Path):
    if provider == "google":
        google_auth_mode = get_google_auth_mode(project_root)
        if google_auth_mode == "adc":
            vertex_ai_config = get_vertex_ai_config(project_root)
            return build_google_client(
                project=vertex_ai_config["project"],
                location=vertex_ai_config["location"],
            )
        if google_auth_mode == "api_key":
            return build_google_client(api_key=get_google_api_key(project_root))
        raise RuntimeError(f"Unsupported GOOGLE_AUTH_MODE: {google_auth_mode}")
    if provider == "openrouter":
        return build_openrouter_metadata_client(get_openrouter_api_key(project_root))
    if provider == "nim":
        return build_nim_metadata_client(get_nim_api_key(project_root))
    raise RuntimeError(f"Unsupported metadata provider: {provider}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare two metadata models against all original images in one run folder."
    )
    parser.add_argument("run_dir", help="Existing output run folder, e.g. output/20260413-120000")
    parser.add_argument("--model-a", required=True, help="Model spec like google:gemini-2.5-flash")
    parser.add_argument("--model-b", required=True, help="Model spec like openrouter:qwen/qwen3.6-plus:free")
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--top-p", type=float, default=0.95)
    return parser.parse_args()


def main() -> int:
    project_root = Path(__file__).resolve().parent
    args = parse_args()
    run_dir = Path(args.run_dir).resolve()

    if not run_dir.exists():
        raise RuntimeError(f"Run folder does not exist: {run_dir}")

    image_paths = list_original_images_in_run_dir(run_dir)
    if not image_paths:
        raise RuntimeError(f"No original images found in: {run_dir}")

    prompt = (run_dir / "prompt.txt").read_text(encoding="utf-8").strip()
    metadata_system_prompt = load_text_asset(project_root, METADATA_PROMPT_PATH)
    category_list = load_text_asset(project_root, CATEGORY_LIST_PATH)

    provider_a, model_a = parse_model_spec(args.model_a)
    provider_b, model_b = parse_model_spec(args.model_b)
    client_a = build_metadata_client(provider_a, project_root)
    client_b = build_metadata_client(provider_b, project_root)

    comparison_root = run_dir / "comparisons"
    comparison_dir = create_run_output_dir(comparison_root)
    results: list[dict[str, object]] = []

    for image_path in image_paths:
        image_label = image_path.stem
        print(f"[COMPARE] {image_path.name} :: {provider_a}:{model_a} vs {provider_b}:{model_b}")
        text_a = generate_metadata_text(
            client=client_a,
            provider=provider_a,
            image_path=image_path,
            generation_prompt=prompt,
            system_prompt=metadata_system_prompt,
            category_list=category_list,
            model=model_a,
            temperature=args.temperature,
            top_p=args.top_p,
        )
        text_b = generate_metadata_text(
            client=client_b,
            provider=provider_b,
            image_path=image_path,
            generation_prompt=prompt,
            system_prompt=metadata_system_prompt,
            category_list=category_list,
            model=model_b,
            temperature=args.temperature,
            top_p=args.top_p,
        )

        (comparison_dir / f"{image_label}-model-a.txt").write_text(text_a, encoding="utf-8")
        (comparison_dir / f"{image_label}-model-b.txt").write_text(text_b, encoding="utf-8")
        results.append(
            {
                "image": image_path.name,
                "model_a": {"provider": provider_a, "model": model_a, "output_file": f"{image_label}-model-a.txt"},
                "model_b": {"provider": provider_b, "model": model_b, "output_file": f"{image_label}-model-b.txt"},
            }
        )

    (comparison_dir / "comparison.json").write_text(
        json.dumps(
            {
                "run_dir": str(run_dir),
                "prompt_file": "prompt.txt",
                "model_a": {"provider": provider_a, "model": model_a},
                "model_b": {"provider": provider_b, "model": model_b},
                "results": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[INFO] Saved comparison outputs to: {comparison_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
