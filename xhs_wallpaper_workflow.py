import argparse
import base64
import json
import shutil
import urllib.request
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any

from main import (
    DEFAULT_ASPECT_RATIO,
    DEFAULT_COUNT,
    DEFAULT_METADATA_MODEL,
    DEFAULT_MODEL,
    DEFAULT_RESOLUTION,
    DEFAULT_TEMPERATURE,
    DEFAULT_TOP_P,
    METADATA_MODEL_ALIASES,
    VALID_ASPECT_RATIOS,
    VALID_RESOLUTIONS,
    build_google_client,
    build_nim_metadata_client,
    build_openrouter_metadata_client,
    get_google_api_key,
    get_google_auth_mode,
    get_nim_api_key,
    get_openrouter_api_key,
    get_vertex_ai_config,
    load_env_file,
    resolve_image_model_alias,
    resolve_metadata_model_alias,
    run,
    save_text_output,
)


DEFAULT_XHS_COUNT = 4
DEFAULT_XHS_ASPECT_RATIO = "9:16"
DEFAULT_XHS_RESOLUTION = "4K"
DEFAULT_PROMPT_PROVIDER = "vertex"
DEFAULT_METADATA_PROVIDER = "vertex"
DEFAULT_PROMPT_REVERSE_MODEL = "gemini-2.5-flash"
PROMPT_SYSTEM_PROMPT_PATH = Path("prompts/xhs_wallpaper_reverse_prompt_system_prompt.txt")
SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
PROVIDER_CHOICES = ("vertex", "openrouter", "nim")


class XhsWorkflowLogger:
    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.text_log_path = run_dir / "xhs_workflow_log.txt"
        self.json_log_path = run_dir / "xhs_workflow_log.json"
        self.events: list[dict[str, object]] = []
        self._persist()

    def _persist(self) -> None:
        self.text_log_path.write_text(
            "\n".join(str(event["line"]) for event in self.events) + "\n"
            if self.events
            else "",
            encoding="utf-8",
        )
        self.json_log_path.write_text(
            json.dumps(self.events, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def log(self, message: str, **fields: object) -> None:
        timestamp = datetime.now().isoformat(timespec="seconds")
        field_text = " ".join(f"{key}={value}" for key, value in fields.items())
        line = f"[XHS] {timestamp} {message}"
        if field_text:
            line = f"{line} {field_text}"
        print(line)
        self.events.append(
            {
                "timestamp": timestamp,
                "message": message,
                "fields": fields,
                "line": line,
            }
        )
        self._persist()


def load_xhs_metadata(target_dir: Path) -> dict[str, object]:
    metadata_path = target_dir / "metadata.json"
    if not metadata_path.exists():
        raise RuntimeError(f"metadata.json is missing in: {target_dir}")

    raw_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    hashtags = raw_metadata.get("hashtags") or []
    if isinstance(hashtags, str):
        hashtags = [hashtags]

    return {
        "title": str(raw_metadata.get("title") or ""),
        "description": str(raw_metadata.get("description") or ""),
        "hashtags": [str(tag) for tag in hashtags],
    }


def list_xhs_input_images(target_dir: Path) -> list[Path]:
    if not target_dir.exists():
        raise RuntimeError(f"Target folder does not exist: {target_dir}")
    if not target_dir.is_dir():
        raise RuntimeError(f"Target path is not a folder: {target_dir}")

    return sorted(
        path
        for path in target_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES
    )


def get_target_output_name(target_dir: Path) -> str:
    raw_path = str(target_dir)
    if "\\" in raw_path:
        return PureWindowsPath(raw_path).name
    return target_dir.name


def get_env_value(project_root: Path, key: str, default: str) -> str:
    env_vars = load_env_file(project_root / ".env")
    return env_vars.get(key) or default


def get_generation_defaults(project_root: Path) -> dict[str, object]:
    return {
        "count": int(get_env_value(project_root, "IMAGE_COUNT", str(DEFAULT_XHS_COUNT))),
        "resolution": get_env_value(project_root, "IMAGE_RESOLUTION", DEFAULT_XHS_RESOLUTION),
        "aspect_ratio": get_env_value(project_root, "IMAGE_ASPECT_RATIO", DEFAULT_XHS_ASPECT_RATIO),
    }


def normalize_provider_name(provider: str) -> str:
    normalized = provider.lower()
    if normalized == "google":
        return "vertex"
    return normalized


def get_default_prompt_provider(project_root: Path) -> str:
    return normalize_provider_name(
        get_env_value(project_root, "PROMPT_PROVIDER", DEFAULT_PROMPT_PROVIDER)
    )


def get_default_prompt_model(project_root: Path) -> str:
    return resolve_metadata_model_alias(
        get_env_value(project_root, "PROMPT_REVERSE_MODEL", DEFAULT_PROMPT_REVERSE_MODEL)
    )


def get_default_metadata_provider(project_root: Path) -> str:
    return normalize_provider_name(
        get_env_value(project_root, "METADATA_PROVIDER", DEFAULT_METADATA_PROVIDER)
    )


def map_provider_for_main(provider: str) -> str:
    if provider == "vertex":
        return "google"
    if provider in ("openrouter", "nim"):
        return provider
    raise RuntimeError(f"Unsupported provider: {provider}")


def build_wallpaper_reverse_user_prompt(
    metadata: dict[str, object],
    image_paths: list[Path],
) -> str:
    hashtags = metadata.get("hashtags") or []
    if isinstance(hashtags, list):
        hashtag_text = ", ".join(str(tag) for tag in hashtags)
    else:
        hashtag_text = str(hashtags)
    image_list = "\n".join(f"- {path.name}" for path in image_paths)
    return (
        "请把我上传的图片作为唯一风格参考，先自动分析这些参考图的整体视觉风格，"
        "包括但不限于：\n"
        "- 画风类型\n"
        "- 配色方式\n"
        "- 光影氛围\n"
        "- 笔触/线条特征\n"
        "- 质感与渲染方式\n"
        "- 构图特点\n"
        "- 情绪与氛围表达\n\n"
        "然后在保留上述风格特征的前提下，生成一张“内容不同”的新壁纸。"
        "新图片内容替换为下方 metadata 所描述的主题、氛围、关键词和用途：\n\n"
        "metadata:\n"
        f"title: {metadata.get('title', '')}\n"
        f"description: {metadata.get('description', '')}\n"
        f"hashtags: {hashtag_text}\n\n"
        "输入图片文件:\n"
        f"{image_list}\n\n"
        "要求：\n"
        "1. 不要直接复制原图中的主体、场景、背景或具体元素。\n"
        "2. 只复刻原图的风格、质感、色彩、光影和整体视觉语言。\n"
        "3. 新图内容必须明显不同，但风格要高度一致。\n"
        "4. 让最终效果看起来像是与参考图属于同一系列、同一作者、同一风格体系下的新作品。\n"
        "5. 画面完整、美观、自然，细节丰富，质量高。\n"
        "6. 请忽略所有不属于壁纸本体的杂讯，包括手机、手机边框、屏幕边框、时间、通知、状态栏、"
        "app 图标、社交媒体界面、水印、手持手机、反光和拍摄环境。\n"
        "7. 只输出最终可直接用于 AI 生图的中文提示词，不要输出分析过程、标题、编号或解释。"
    )


def image_part_for_google(image_path: Path):
    from google.genai import types

    mime_type = "image/jpeg" if image_path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    return types.Part.from_bytes(data=image_path.read_bytes(), mime_type=mime_type)


def image_data_url(image_path: Path) -> str:
    suffix = image_path.suffix.lower()
    mime_type = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }.get(suffix, "image/png")
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def build_vertex_client(project_root: Path):
    auth_mode = get_google_auth_mode(project_root)
    if auth_mode == "adc":
        vertex_ai_config = get_vertex_ai_config(project_root)
        return build_google_client(
            project=vertex_ai_config["project"],
            location=vertex_ai_config["location"],
        )
    if auth_mode == "api_key":
        return build_google_client(api_key=get_google_api_key(project_root))
    raise RuntimeError(f"Unsupported GOOGLE_AUTH_MODE: {auth_mode}")


def generate_vertex_prompt_text(
    client,
    image_paths: list[Path],
    user_prompt: str,
    system_prompt: str,
    model: str,
    temperature: float,
    top_p: float,
) -> str:
    from google.genai import types

    contents = [image_part_for_google(path) for path in image_paths] + [user_prompt]
    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            systemInstruction=system_prompt,
            temperature=temperature,
            topP=top_p,
        ),
    )
    response_text = getattr(response, "text", None)
    if not response_text:
        raise RuntimeError("The prompt reversal API response did not contain text output.")
    return response_text.strip()


def build_chat_prompt_payload(
    model: str,
    system_prompt: str,
    user_prompt: str,
    image_paths: list[Path],
    temperature: float,
    top_p: float,
) -> dict[str, object]:
    return {
        "model": model,
        "temperature": temperature,
        "top_p": top_p,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [{"type": "text", "text": user_prompt}]
                + [
                    {
                        "type": "image_url",
                        "image_url": {"url": image_data_url(path)},
                    }
                    for path in image_paths
                ],
            },
        ],
    }


def generate_chat_prompt_text(
    client: dict[str, str],
    provider: str,
    image_paths: list[Path],
    user_prompt: str,
    system_prompt: str,
    model: str,
    temperature: float,
    top_p: float,
) -> str:
    payload = build_chat_prompt_payload(
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        image_paths=image_paths,
        temperature=temperature,
        top_p=top_p,
    )
    if provider == "openrouter":
        url = "https://openrouter.ai/api/v1/chat/completions"
    elif provider == "nim":
        url = f"{client['base_url']}/v1/chat/completions"
    else:
        raise RuntimeError(f"Unsupported prompt provider: {provider}")

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {client['api_key']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request) as response:
        response_json = json.loads(response.read().decode("utf-8"))
    response_text = response_json["choices"][0]["message"]["content"]
    if not response_text:
        raise RuntimeError("The prompt reversal API response did not contain text output.")
    return response_text.strip()


def generate_chinese_wallpaper_prompt(
    *,
    project_root: Path,
    provider: str,
    model: str,
    metadata: dict[str, object],
    image_paths: list[Path],
    temperature: float,
    top_p: float,
) -> str:
    if not image_paths:
        raise RuntimeError("No input images found for prompt reversal.")

    system_prompt = (project_root / PROMPT_SYSTEM_PROMPT_PATH).read_text(encoding="utf-8").strip()
    user_prompt = build_wallpaper_reverse_user_prompt(metadata, image_paths)

    if provider == "vertex":
        return generate_vertex_prompt_text(
            client=build_vertex_client(project_root),
            image_paths=image_paths,
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            model=model,
            temperature=temperature,
            top_p=top_p,
        )
    if provider == "openrouter":
        return generate_chat_prompt_text(
            client=build_openrouter_metadata_client(get_openrouter_api_key(project_root)),
            provider=provider,
            image_paths=image_paths,
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            model=model,
            temperature=temperature,
            top_p=top_p,
        )
    if provider == "nim":
        return generate_chat_prompt_text(
            client=build_nim_metadata_client(get_nim_api_key(project_root)),
            provider=provider,
            image_paths=image_paths,
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            model=model,
            temperature=temperature,
            top_p=top_p,
        )
    raise RuntimeError(f"Unsupported prompt provider: {provider}")


def prepare_output_dir(project_root: Path, output_name: str, overwrite: bool) -> Path:
    run_dir = project_root / "output" / output_name
    if run_dir.exists():
        if not overwrite:
            raise RuntimeError(
                f"Output folder already exists: {run_dir}. Pass --overwrite to replace it."
            )
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def run_generation_pipeline(**kwargs: Any) -> Path:
    return run(**kwargs)


def run_xhs_workflow(
    *,
    target_dir: Path,
    project_root: Path,
    prompt_provider: str,
    prompt_model: str,
    image_model: str,
    metadata_provider: str,
    metadata_model: str,
    count: int,
    aspect_ratio: str,
    resolution: str,
    temperature: float,
    top_p: float,
    overwrite: bool,
) -> Path:
    target_dir = target_dir.resolve()
    metadata = load_xhs_metadata(target_dir)
    image_paths = list_xhs_input_images(target_dir)
    output_name = get_target_output_name(target_dir)
    run_dir = prepare_output_dir(project_root, output_name, overwrite=overwrite)
    logger = XhsWorkflowLogger(run_dir)
    logger.log(
        "Loaded XHS inputs",
        target_dir=str(target_dir),
        image_count=len(image_paths),
        output_dir=str(run_dir),
    )
    save_text_output(
        run_dir,
        "xhs_source_metadata.json",
        json.dumps(metadata, indent=2, ensure_ascii=False, sort_keys=True),
    )
    save_text_output(
        run_dir,
        "xhs_source_images.json",
        json.dumps([str(path) for path in image_paths], indent=2, ensure_ascii=False),
    )

    try:
        logger.log(
            "Starting prompt reversal",
            provider=prompt_provider,
            model=prompt_model,
        )
        chinese_prompt = generate_chinese_wallpaper_prompt(
            project_root=project_root,
            provider=prompt_provider,
            model=prompt_model,
            metadata=metadata,
            image_paths=image_paths,
            temperature=temperature,
            top_p=top_p,
        )
        save_text_output(run_dir, "chinese_prompt.txt", chinese_prompt)
        logger.log("Finished prompt reversal", prompt_file=str(run_dir / "chinese_prompt.txt"))

        logger.log(
            "Starting image generation and Adobe Stock metadata",
            image_model=image_model,
            metadata_provider=metadata_provider,
            metadata_model=metadata_model,
            count=count,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
        )
        result_dir = run_generation_pipeline(
            prompt=chinese_prompt,
            model=image_model,
            metadata_model=metadata_model,
            project_root=project_root,
            temperature=temperature,
            top_p=top_p,
            count=count,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            metadata_provider=map_provider_for_main(metadata_provider),
            run_dir=run_dir,
            remove_background=False,
        )
        logger.log("Finished XHS workflow", output_dir=str(result_dir))
        return result_dir
    except Exception as exc:
        logger.log("XHS workflow failed", error=str(exc))
        raise


def parse_args(
    argv: list[str] | None = None,
    project_root: Path | None = None,
) -> argparse.Namespace:
    resolved_project_root = project_root or Path(__file__).resolve().parent
    generation_defaults = get_generation_defaults(resolved_project_root)
    parser = argparse.ArgumentParser(
        description="Reverse an XHS wallpaper folder into a prompt and generate stock-ready outputs."
    )
    parser.add_argument("target_folder", help="XHS download folder containing images and metadata.json.")
    parser.add_argument(
        "--prompt-provider",
        choices=PROVIDER_CHOICES,
        default=get_default_prompt_provider(resolved_project_root),
    )
    parser.add_argument(
        "--prompt-model",
        default=get_default_prompt_model(resolved_project_root),
    )
    parser.add_argument(
        "--metadata-provider",
        choices=PROVIDER_CHOICES,
        default=get_default_metadata_provider(resolved_project_root),
    )
    parser.add_argument(
        "--metadata-model",
        default=resolve_metadata_model_alias(
            get_env_value(resolved_project_root, "METADATA_MODEL", DEFAULT_METADATA_MODEL)
        ),
    )
    parser.add_argument(
        "--model",
        default=resolve_image_model_alias(get_env_value(resolved_project_root, "IMAGE_MODEL", DEFAULT_MODEL)),
        help="Vertex image generation model.",
    )
    parser.add_argument("--count", type=int, default=generation_defaults["count"])
    parser.add_argument(
        "--aspect-ratio",
        choices=VALID_ASPECT_RATIOS,
        default=generation_defaults["aspect_ratio"],
    )
    parser.add_argument(
        "--resolution",
        choices=VALID_RESOLUTIONS,
        default=generation_defaults["resolution"],
    )
    parser.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE)
    parser.add_argument("--top-p", type=float, default=DEFAULT_TOP_P)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    args.prompt_provider = normalize_provider_name(args.prompt_provider)
    args.metadata_provider = normalize_provider_name(args.metadata_provider)
    args.prompt_model = METADATA_MODEL_ALIASES.get(args.prompt_model.lower(), args.prompt_model)
    args.metadata_model = resolve_metadata_model_alias(args.metadata_model)
    args.model = resolve_image_model_alias(args.model)
    return args


def main() -> int:
    project_root = Path(__file__).resolve().parent
    args = parse_args(project_root=project_root)
    run_dir = run_xhs_workflow(
        target_dir=Path(args.target_folder),
        project_root=project_root,
        prompt_provider=args.prompt_provider,
        prompt_model=args.prompt_model,
        image_model=args.model,
        metadata_provider=args.metadata_provider,
        metadata_model=args.metadata_model,
        count=args.count,
        aspect_ratio=args.aspect_ratio,
        resolution=args.resolution,
        temperature=args.temperature,
        top_p=args.top_p,
        overwrite=args.overwrite,
    )
    print(f"[INFO] Saved XHS workflow outputs to: {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
