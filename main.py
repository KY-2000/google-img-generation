import argparse
import base64
import csv
import io
import json
import os
import subprocess
import tempfile
import time
import urllib.request
from collections import Counter
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np
from PIL import Image


ALPHA_LOW_CUTOFF = 128
ALPHA_HIGH_CUTOFF = 220
EDGE_ANALYSIS_SIMILARITY = 0.14
EDGE_ANALYSIS_BLEND = 0.02
DEFAULT_MODEL = "gemini-3-pro-image-preview"
DEFAULT_GOOGLE_AUTH_MODE = "adc"
DEFAULT_METADATA_PROVIDER = "google"
DEFAULT_METADATA_MODEL = "gemini-2.5-flash"
DEFAULT_NIM_BASE_URL = "https://integrate.api.nvidia.com"
DEFAULT_TEMPERATURE = 1.0
DEFAULT_TOP_P = 0.95
DEFAULT_COUNT = 1
DEFAULT_ASPECT_RATIO = "1:1"
DEFAULT_RESOLUTION = "1K"
VALID_ASPECT_RATIOS = (
    "1:1",
    "1:4",
    "1:8",
    "2:3",
    "3:2",
    "3:4",
    "4:1",
    "4:3",
    "4:5",
    "5:4",
    "8:1",
    "9:16",
    "16:9",
    "21:9",
)
VALID_RESOLUTIONS = ("512", "1K", "2K", "4K")
METADATA_PROMPT_PATH = Path("prompts/adobe_stock_metadata_system_prompt.txt")
CATEGORY_LIST_PATH = Path("category-code.txt")
IMAGE_MODEL_ALIASES = {
    "nanobanana2": "gemini-3.1-flash-image-preview",
    "nanobananapro": "gemini-3-pro-image-preview",
}
METADATA_MODEL_ALIASES = {
    "qwenfree": "qwen/qwen3.6-plus:free",
    "minimaxfree": "minimax/minimax-m2.5:free",
    "nemotronsuperfree": "nvidia/nemotron-3-super-120b-a12b:free",
    "kimik25": "moonshotai/kimi-k2.5",
}


def load_env_file(env_path: Path) -> dict[str, str]:
    env_vars: dict[str, str] = {}
    if not env_path.exists():
        return env_vars

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        env_vars[key.strip()] = value.strip().strip("'\"")

    return env_vars


def get_vertex_ai_config(project_root: Path) -> dict[str, str]:
    env_vars = load_env_file(project_root / ".env")
    project = env_vars.get("GOOGLE_CLOUD_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT")
    location = (
        env_vars.get("GOOGLE_CLOUD_LOCATION")
        or os.getenv("GOOGLE_CLOUD_LOCATION")
        or "global"
    )
    if not project:
        raise RuntimeError(
            "GOOGLE_CLOUD_PROJECT is missing. Add it to the project root .env file."
        )
    return {
        "project": project,
        "location": location,
    }


def get_google_auth_mode(project_root: Path) -> str:
    env_vars = load_env_file(project_root / ".env")
    return (
        env_vars.get("GOOGLE_AUTH_MODE")
        or os.getenv("GOOGLE_AUTH_MODE")
        or DEFAULT_GOOGLE_AUTH_MODE
    ).lower()


def get_google_api_key(project_root: Path) -> str:
    env_vars = load_env_file(project_root / ".env")
    api_key = env_vars.get("VERTEX_API_KEY") or os.getenv("VERTEX_API_KEY")
    if not api_key:
        raise RuntimeError(
            "VERTEX_API_KEY is missing. Add it to the project root .env file."
        )
    return api_key


def get_openrouter_api_key(project_root: Path) -> str:
    env_vars = load_env_file(project_root / ".env")
    api_key = env_vars.get("OPENROUTER_API_KEY") or os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is missing. Add it to the project root .env file."
        )
    return api_key


def get_nim_api_key(project_root: Path) -> str:
    env_vars = load_env_file(project_root / ".env")
    api_key = env_vars.get("NIM_API_KEY") or os.getenv("NIM_API_KEY")
    if not api_key:
        raise RuntimeError(
            "NIM_API_KEY is missing. Add it to the project root .env file."
        )
    return api_key


def resolve_image_model_alias(model_name: str) -> str:
    return IMAGE_MODEL_ALIASES.get(model_name.lower(), model_name)


def resolve_metadata_model_alias(model_name: str) -> str:
    return METADATA_MODEL_ALIASES.get(model_name.lower(), model_name)


def get_default_image_model(project_root: Path) -> str:
    env_vars = load_env_file(project_root / ".env")
    model_name = env_vars.get("IMAGE_MODEL") or os.getenv("IMAGE_MODEL") or DEFAULT_MODEL
    return resolve_image_model_alias(model_name)


def get_default_metadata_provider(project_root: Path) -> str:
    env_vars = load_env_file(project_root / ".env")
    return (
        env_vars.get("METADATA_PROVIDER")
        or os.getenv("METADATA_PROVIDER")
        or DEFAULT_METADATA_PROVIDER
    ).lower()


def get_default_metadata_model(project_root: Path) -> str:
    env_vars = load_env_file(project_root / ".env")
    model_name = (
        env_vars.get("METADATA_MODEL")
        or os.getenv("METADATA_MODEL")
        or DEFAULT_METADATA_MODEL
    )
    return resolve_metadata_model_alias(model_name)


def timestamp_string() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def create_run_output_dir(project_root: Path, timestamp: str | None = None) -> Path:
    run_timestamp = timestamp or timestamp_string()
    run_dir = project_root / "output" / run_timestamp
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def save_prompt(run_dir: Path, prompt: str) -> Path:
    prompt_path = run_dir / "prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    return prompt_path


def save_text_output(run_dir: Path, filename: str, content: str) -> Path:
    output_path = run_dir / filename
    output_path.write_text(content, encoding="utf-8")
    return output_path


def save_run_config(run_dir: Path, config: dict[str, object]) -> Path:
    config_path = run_dir / "run_config.json"
    config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return config_path


def create_temp_png_path() -> Path:
    file_descriptor, temp_path = tempfile.mkstemp(suffix=".png")
    os.close(file_descriptor)
    Path(temp_path).unlink(missing_ok=True)
    return Path(temp_path)


def build_rembg_output_path(run_dir: Path, original_image_path: Path) -> Path:
    image_index = original_image_path.stem.removeprefix("img-")
    return run_dir / f"img-rembg-{run_dir.name}-{image_index}.png"


def find_missing_background_removals(output_root: Path) -> list[tuple[Path, Path]]:
    pending: list[tuple[Path, Path]] = []
    if not output_root.exists():
        return pending

    for run_dir in sorted(path for path in output_root.iterdir() if path.is_dir()):
        for original_image_path in sorted(run_dir.glob("img-*.png")):
            if original_image_path.name.startswith("img-rembg-"):
                continue
            rembg_image_path = build_rembg_output_path(run_dir, original_image_path)
            if not rembg_image_path.exists():
                pending.append((original_image_path, rembg_image_path))

    return pending


def list_original_images_in_run_dir(run_dir: Path) -> list[Path]:
    originals: list[Path] = []
    for image_path in sorted(run_dir.glob("img-*.png")):
        if image_path.name.startswith("img-rembg-"):
            continue
        originals.append(image_path)
    return originals


def parse_model_spec(model_spec: str) -> tuple[str, str]:
    provider, separator, model_name = model_spec.partition(":")
    if separator != ":" or not provider or not model_name:
        raise ValueError(
            "Model spec must be in the format '<provider>:<model>', "
            "for example 'google:gemini-2.5-flash'."
        )
    return provider.lower(), resolve_metadata_model_alias(model_name)


def iso_timestamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def round_duration(seconds: float) -> float:
    return round(seconds, 3)


class RunLogger:
    def __init__(self, run_dir: Path, config: dict[str, object]):
        run_dir.mkdir(parents=True, exist_ok=True)
        self.run_dir = run_dir
        self.text_log_path = run_dir / "session_log.txt"
        self.json_log_path = run_dir / "session_log.json"
        self.data: dict[str, Any] = {
            "started_at": iso_timestamp(),
            "ended_at": None,
            "duration_seconds": None,
            "status": "running",
            "error": None,
            "config": config,
            "sessions": [],
            "events": [],
        }
        self._persist()

    def _persist(self) -> None:
        self.json_log_path.write_text(
            json.dumps(self.data, indent=2, sort_keys=False),
            encoding="utf-8",
        )
        self.text_log_path.write_text(
            "\n".join(event["line"] for event in self.data["events"]) + "\n"
            if self.data["events"]
            else "",
            encoding="utf-8",
        )

    def log_event(
        self,
        message: str,
        *,
        level: str = "INFO",
        session_index: int | None = None,
        step: str | None = None,
        attempt: int | None = None,
        duration_seconds: float | None = None,
        retry_wait_seconds: float | None = None,
        error: str | None = None,
    ) -> None:
        parts = [f"[{level}]", iso_timestamp()]
        if session_index is not None:
            parts.append(f"[session {session_index}]")
        if step:
            parts.append(f"[{step}]")
        if attempt is not None:
            parts.append(f"[attempt {attempt}]")
        parts.append(message)
        if duration_seconds is not None:
            parts.append(f"duration={duration_seconds:.3f}s")
        if retry_wait_seconds is not None:
            parts.append(f"retry_wait={retry_wait_seconds:.3f}s")
        if error:
            parts.append(f"error={error}")

        line = " ".join(parts)
        print(line)
        self.data["events"].append(
            {
                "timestamp": iso_timestamp(),
                "level": level,
                "session_index": session_index,
                "step": step,
                "attempt": attempt,
                "message": message,
                "duration_seconds": duration_seconds,
                "retry_wait_seconds": retry_wait_seconds,
                "error": error,
                "line": line,
            }
        )
        self._persist()

    def start_session(self, session_index: int) -> dict[str, Any]:
        session_data: dict[str, Any] = {
            "index": session_index,
            "started_at": iso_timestamp(),
            "ended_at": None,
            "duration_seconds": None,
            "status": "running",
            "error": None,
            "steps": {},
        }
        self.data["sessions"].append(session_data)
        self._persist()
        return session_data

    def finish_step(
        self,
        session_data: dict[str, Any],
        step_name: str,
        attempts: list[dict[str, Any]],
        status: str,
        duration_seconds: float,
        error: str | None = None,
    ) -> None:
        session_data["steps"][step_name] = {
            "status": status,
            "duration_seconds": duration_seconds,
            "error": error,
            "attempts": attempts,
        }
        self._persist()

    def finish_session(
        self,
        session_data: dict[str, Any],
        status: str,
        duration_seconds: float,
        error: str | None = None,
    ) -> None:
        session_data["status"] = status
        session_data["ended_at"] = iso_timestamp()
        session_data["duration_seconds"] = duration_seconds
        session_data["error"] = error
        self._persist()

    def finish_run(
        self,
        status: str,
        duration_seconds: float,
        error: str | None = None,
    ) -> None:
        self.data["status"] = status
        self.data["ended_at"] = iso_timestamp()
        self.data["duration_seconds"] = duration_seconds
        self.data["error"] = error
        self._persist()


def is_retryable_error(error: Exception) -> bool:
    message = str(error).upper()
    return "429" in message or "RESOURCE_EXHAUSTED" in message


def run_step_with_retries(
    operation: Callable[[], Any],
    *,
    logger: RunLogger,
    session_data: dict[str, Any],
    session_index: int,
    step_name: str,
    max_attempts: int = 4,
    initial_wait_seconds: float = 1.0,
) -> Any:
    attempts: list[dict[str, Any]] = []
    wait_seconds = initial_wait_seconds
    step_started_at = time.perf_counter()

    for attempt_index in range(1, max_attempts + 1):
        attempt_started_at = time.perf_counter()
        try:
            result = operation()
        except Exception as exc:
            attempt_duration = round_duration(time.perf_counter() - attempt_started_at)
            should_retry = is_retryable_error(exc) and attempt_index < max_attempts
            attempt_record = {
                "attempt": attempt_index,
                "status": "error",
                "duration_seconds": attempt_duration,
                "error": str(exc),
                "retry_wait_seconds": wait_seconds if should_retry else None,
            }
            attempts.append(attempt_record)
            logger.log_event(
                f"{step_name} failed",
                level="WARNING" if should_retry else "ERROR",
                session_index=session_index,
                step=step_name,
                attempt=attempt_index,
                duration_seconds=attempt_duration,
                retry_wait_seconds=wait_seconds if should_retry else None,
                error=str(exc),
            )
            if not should_retry:
                step_duration = round_duration(time.perf_counter() - step_started_at)
                logger.finish_step(
                    session_data,
                    step_name,
                    attempts=attempts,
                    status="error",
                    duration_seconds=step_duration,
                    error=str(exc),
                )
                raise
            time.sleep(wait_seconds)
            wait_seconds *= 2
            continue

        attempt_duration = round_duration(time.perf_counter() - attempt_started_at)
        attempts.append(
            {
                "attempt": attempt_index,
                "status": "success",
                "duration_seconds": attempt_duration,
                "error": None,
                "retry_wait_seconds": None,
            }
        )
        step_duration = round_duration(time.perf_counter() - step_started_at)
        logger.finish_step(
            session_data,
            step_name,
            attempts=attempts,
            status="success",
            duration_seconds=step_duration,
        )
        logger.log_event(
            f"{step_name} completed",
            session_index=session_index,
            step=step_name,
            attempt=attempt_index,
            duration_seconds=attempt_duration,
        )
        return result

    raise RuntimeError(f"{step_name} failed after {max_attempts} attempts")


def run_step_with_timing(
    operation: Callable[[], Any],
    *,
    logger: RunLogger,
    session_data: dict[str, Any],
    session_index: int,
    step_name: str,
) -> Any:
    started_at = time.perf_counter()
    try:
        result = operation()
    except Exception as exc:
        duration_seconds = round_duration(time.perf_counter() - started_at)
        logger.finish_step(
            session_data,
            step_name,
            attempts=[],
            status="error",
            duration_seconds=duration_seconds,
            error=str(exc),
        )
        logger.log_event(
            f"{step_name} failed",
            level="ERROR",
            session_index=session_index,
            step=step_name,
            duration_seconds=duration_seconds,
            error=str(exc),
        )
        raise

    duration_seconds = round_duration(time.perf_counter() - started_at)
    logger.finish_step(
        session_data,
        step_name,
        attempts=[],
        status="success",
        duration_seconds=duration_seconds,
    )
    logger.log_event(
        f"{step_name} completed",
        session_index=session_index,
        step=step_name,
        duration_seconds=duration_seconds,
    )
    return result


def extract_edge_pixels(image_path, edge_ratio=0.1, sample_step=5):
    img = Image.open(image_path).convert("RGB")
    arr = np.array(img)
    height, width = arr.shape[:2]

    edge_x = max(1, int(width * edge_ratio))
    edge_y = max(1, int(height * edge_ratio))

    top = arr[:edge_y, :, :]
    bottom = arr[height - edge_y :, :, :]
    left = arr[edge_y : height - edge_y, :edge_x, :]
    right = arr[edge_y : height - edge_y, width - edge_x :, :]

    edge_pixels = np.concatenate(
        [
            top.reshape(-1, 3),
            bottom.reshape(-1, 3),
            left.reshape(-1, 3),
            right.reshape(-1, 3),
        ]
    )

    return edge_pixels[::sample_step]


def detect_background_rgb_from_edges(image_path, edge_ratio=0.1, sample_step=5):
    edge_pixels = extract_edge_pixels(
        image_path,
        edge_ratio=edge_ratio,
        sample_step=sample_step,
    )
    counts = Counter(map(tuple, edge_pixels))
    dominant = counts.most_common(1)[0][0]
    return tuple(int(channel) for channel in dominant[:3])


def rgb_to_ffmpeg_hex(rgb):
    r, g, b = rgb[:3]
    return f"0x{r:02X}{g:02X}{b:02X}"


def build_ffmpeg_command(input_path, output_path, hex_color, similarity, blend):
    return [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-filter_complex",
        (
            f"[0:v]colorkey={hex_color}:{similarity}:{blend},format=rgba[ck];"
            f"[ck]alphaextract[a];"
            f"[a]erosion=1,boxblur=1,"
            f"lut=y='if(lt(val,{ALPHA_LOW_CUTOFF}),0,"
            f"if(gt(val,{ALPHA_HIGH_CUTOFF}),255,val))'[a2];"
            f"[ck][a2]alphamerge"
        ),
        "-frames:v",
        "1",
        "-update",
        "1",
        str(output_path),
    ]


def decontaminate_edge_spill(image, background_rgb):
    img = image.convert("RGBA")
    arr = np.array(img).astype(np.float32)

    alpha = arr[..., 3:4] / 255.0
    bg = np.array(background_rgb, dtype=np.float32).reshape((1, 1, 3))

    partial_mask = (alpha > 0.0) & (alpha < 1.0)
    safe_alpha = np.maximum(alpha, 1e-6)
    corrected_rgb = (arr[..., :3] - bg * (1.0 - alpha)) / safe_alpha
    corrected_rgb = np.clip(corrected_rgb, 0, 255)

    arr[..., :3] = np.where(partial_mask, corrected_rgb, arr[..., :3])

    return Image.fromarray(arr.astype(np.uint8))


def save_rgba_image(image, output_path):
    image.save(output_path)


def remove_background_ffmpeg(
    input_path,
    output_path,
    similarity=EDGE_ANALYSIS_SIMILARITY,
    blend=EDGE_ANALYSIS_BLEND,
):
    background_rgb = detect_background_rgb_from_edges(input_path)
    hex_color = rgb_to_ffmpeg_hex(background_rgb)

    print(f"[INFO] Background RGB: {background_rgb}")
    print(f"[INFO] Using: {hex_color}, similarity={similarity}, blend={blend}")

    tmp_png = create_temp_png_path()
    try:
        subprocess.run(
            build_ffmpeg_command(input_path, tmp_png, hex_color, similarity, blend),
            check=True,
        )

        with Image.open(tmp_png) as img:
            cleaned_img = decontaminate_edge_spill(img, background_rgb)
            save_rgba_image(cleaned_img, output_path)
    finally:
        with suppress(FileNotFoundError):
            tmp_png.unlink()


def build_client(
    project: str | None = None,
    location: str | None = None,
    api_key: str | None = None,
):
    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError(
            "google-genai is not installed. Install it with: pip install google-genai"
        ) from exc

    if api_key:
        return genai.Client(vertexai=True, api_key=api_key)
    return genai.Client(vertexai=True, project=project, location=location)


def build_google_client(
    project: str | None = None,
    location: str | None = None,
    api_key: str | None = None,
):
    return build_client(project=project, location=location, api_key=api_key)


def build_openrouter_metadata_client(api_key: str) -> dict[str, str]:
    return {"api_key": api_key}


def build_nim_metadata_client(api_key: str) -> dict[str, str]:
    return {
        "api_key": api_key,
        "base_url": DEFAULT_NIM_BASE_URL,
    }


def iter_inline_images(response) -> Iterable[bytes]:
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            inline_data = getattr(part, "inline_data", None)
            data = getattr(inline_data, "data", None)
            if data:
                yield data


def generate_image_bytes(
    client,
    prompt: str,
    model: str,
    temperature: float,
    top_p: float,
    aspect_ratio: str,
    resolution: str,
) -> bytes:
    try:
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError(
            "google-genai is not installed. Install it with: pip install google-genai"
        ) from exc

    response = client.models.generate_content(
        model=model,
        contents=[prompt],
        config=types.GenerateContentConfig(
            temperature=temperature,
            topP=top_p,
            imageConfig=types.ImageConfig(
                aspectRatio=aspect_ratio,
                imageSize=resolution,
            ),
        ),
    )

    first_image = next(iter_inline_images(response), None)
    if first_image is None:
        raise RuntimeError("The API response did not contain an inline image.")

    return first_image


def load_text_asset(project_root: Path, relative_path: Path) -> str:
    asset_path = project_root / relative_path
    return asset_path.read_text(encoding="utf-8").strip()


def build_metadata_user_prompt(generation_prompt: str, category_list: str) -> str:
    return (
        "Analyze this image for Adobe Stock metadata.\n\n"
        "Generation prompt:\n"
        f"{generation_prompt}\n\n"
        "Category list:\n"
        f"{category_list}\n\n"
        "Return final output only in the required format."
    )


def build_openrouter_metadata_payload(
    model: str,
    system_prompt: str,
    user_prompt: str,
    image_data_url: str,
    temperature: float,
    top_p: float,
) -> dict[str, object]:
    return {
        "model": model,
        "temperature": temperature,
        "top_p": top_p,
        "messages": [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": image_data_url},
                    },
                ],
            },
        ],
    }


def build_nim_metadata_payload(
    model: str,
    system_prompt: str,
    user_prompt: str,
    image_data_url: str,
    temperature: float,
    top_p: float,
) -> dict[str, object]:
    return {
        "model": model,
        "temperature": temperature,
        "top_p": top_p,
        "messages": [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": image_data_url},
                    },
                ],
            },
        ],
    }


def generate_google_metadata_text(
    client,
    image_path: Path,
    user_prompt: str,
    system_prompt: str,
    model: str,
    temperature: float,
    top_p: float,
) -> str:
    try:
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError(
            "google-genai is not installed. Install it with: pip install google-genai"
        ) from exc

    image_bytes = image_path.read_bytes()
    image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/png")

    response = client.models.generate_content(
        model=model,
        contents=[image_part, user_prompt],
        config=types.GenerateContentConfig(
            systemInstruction=system_prompt,
            temperature=temperature,
            topP=top_p,
        ),
    )

    response_text = getattr(response, "text", None)
    if not response_text:
        raise RuntimeError("The metadata API response did not contain text output.")

    return response_text.strip()


def generate_openrouter_metadata_text(
    client: dict[str, str],
    image_path: Path,
    user_prompt: str,
    system_prompt: str,
    model: str,
    temperature: float,
    top_p: float,
) -> str:
    image_data_url = (
        "data:image/png;base64,"
        + base64.b64encode(image_path.read_bytes()).decode("ascii")
    )
    payload = build_openrouter_metadata_payload(
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        image_data_url=image_data_url,
        temperature=temperature,
        top_p=top_p,
    )

    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
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
        raise RuntimeError("The metadata API response did not contain text output.")

    return response_text.strip()


def generate_nim_metadata_text(
    client: dict[str, str],
    image_path: Path,
    user_prompt: str,
    system_prompt: str,
    model: str,
    temperature: float,
    top_p: float,
) -> str:
    image_data_url = (
        "data:image/png;base64,"
        + base64.b64encode(image_path.read_bytes()).decode("ascii")
    )
    payload = build_nim_metadata_payload(
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        image_data_url=image_data_url,
        temperature=temperature,
        top_p=top_p,
    )

    request = urllib.request.Request(
        f"{client['base_url']}/v1/chat/completions",
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
        raise RuntimeError("The metadata API response did not contain text output.")

    return response_text.strip()


def generate_metadata_text(
    client,
    provider: str,
    image_path: Path,
    generation_prompt: str,
    system_prompt: str,
    category_list: str,
    model: str,
    temperature: float,
    top_p: float,
) -> str:
    user_prompt = build_metadata_user_prompt(generation_prompt, category_list)
    if provider == "google":
        return generate_google_metadata_text(
            client=client,
            image_path=image_path,
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            model=model,
            temperature=temperature,
            top_p=top_p,
        )
    if provider == "openrouter":
        return generate_openrouter_metadata_text(
            client=client,
            image_path=image_path,
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            model=model,
            temperature=temperature,
            top_p=top_p,
        )
    if provider == "nim":
        return generate_nim_metadata_text(
            client=client,
            image_path=image_path,
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            model=model,
            temperature=temperature,
            top_p=top_p,
        )
    raise RuntimeError(f"Unsupported metadata provider: {provider}")


def parse_metadata_response(response_text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    label_map = {
        "Title": "Title",
        "Keywords": "Keywords",
        "Category Code": "Category",
        "Category Name": "Category Name",
    }

    for raw_line in response_text.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue

        key, value = line.split(":", 1)
        normalized_key = label_map.get(key.strip())
        if normalized_key:
            fields[normalized_key] = value.strip()

    required_fields = ("Title", "Keywords", "Category", "Category Name")
    for field_name in required_fields:
        if not fields.get(field_name):
            raise RuntimeError(f"Metadata response is missing required field: {field_name}")

    return fields


def save_metadata_outputs(run_dir: Path, rows: list[dict[str, str]]) -> tuple[Path, Path]:
    metadata_blocks = [row["response_text"].strip() for row in rows]
    metadata_txt_path = save_text_output(
        run_dir,
        "metadata.txt",
        "\n\n".join(metadata_blocks),
    )

    metadata_csv_path = run_dir / "adobe_stock_metadata.csv"
    with metadata_csv_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["Filename", "Title", "Keywords", "Category", "Releases"])
        for row in rows:
            metadata = parse_metadata_response(row["response_text"])
            writer.writerow(
                [
                    row["image_filename"],
                    metadata["Title"],
                    metadata["Keywords"],
                    metadata["Category"],
                    "",
                ]
            )

    return metadata_txt_path, metadata_csv_path


def save_png_from_bytes(image_bytes: bytes, output_path: Path) -> Path:
    with Image.open(io.BytesIO(image_bytes)) as img:
        img.save(output_path, format="PNG")
    return output_path


def run(
    prompt: str,
    model: str,
    metadata_model: str,
    project_root: Path,
    temperature: float,
    top_p: float,
    count: int,
    aspect_ratio: str,
    resolution: str,
    metadata_provider: str = DEFAULT_METADATA_PROVIDER,
    run_dir: Path | None = None,
    remove_background: bool = True,
) -> Path:
    run_started_at = time.perf_counter()
    google_auth_mode = get_google_auth_mode(project_root)
    if google_auth_mode == "adc":
        vertex_ai_config = get_vertex_ai_config(project_root)
        image_client = build_google_client(
            project=vertex_ai_config["project"],
            location=vertex_ai_config["location"],
        )
    elif google_auth_mode == "api_key":
        google_api_key = get_google_api_key(project_root)
        image_client = build_google_client(api_key=google_api_key)
    else:
        raise RuntimeError(f"Unsupported GOOGLE_AUTH_MODE: {google_auth_mode}")
    if metadata_provider == "google":
        metadata_client = image_client
    elif metadata_provider == "openrouter":
        metadata_api_key = get_openrouter_api_key(project_root)
        metadata_client = build_openrouter_metadata_client(metadata_api_key)
    elif metadata_provider == "nim":
        metadata_api_key = get_nim_api_key(project_root)
        metadata_client = build_nim_metadata_client(metadata_api_key)
    else:
        raise RuntimeError(f"Unsupported metadata provider: {metadata_provider}")
    metadata_system_prompt = load_text_asset(project_root, METADATA_PROMPT_PATH)
    category_list = load_text_asset(project_root, CATEGORY_LIST_PATH)

    run_dir = run_dir or create_run_output_dir(project_root)
    run_dir.mkdir(parents=True, exist_ok=True)
    save_prompt(run_dir, prompt)
    run_timestamp = run_dir.name
    save_run_config(
        run_dir=run_dir,
        config={
            "model": model,
            "metadata_provider": metadata_provider,
            "metadata_model": metadata_model,
            "temperature": temperature,
            "top_p": top_p,
            "count": count,
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
            "remove_background": remove_background,
            "remove_background": remove_background,
        },
    )
    logger = RunLogger(
        run_dir=run_dir,
        config={
            "model": model,
            "metadata_provider": metadata_provider,
            "metadata_model": metadata_model,
            "temperature": temperature,
            "top_p": top_p,
            "count": count,
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
        },
    )
    logger.log_event("Run started")
    metadata_rows: list[dict[str, str]] = []

    try:
        for index in range(1, count + 1):
            session_started_at = time.perf_counter()
            original_image_path = run_dir / f"img-{index}.png"
            rembg_image_path = run_dir / f"img-rembg-{run_timestamp}-{index}.png"
            session_data = logger.start_session(index)
            logger.log_event("Session started", session_index=index)

            try:
                image_bytes = run_step_with_retries(
                    lambda: generate_image_bytes(
                        image_client,
                        prompt,
                        model,
                        temperature,
                        top_p,
                        aspect_ratio,
                        resolution,
                    ),
                    logger=logger,
                    session_data=session_data,
                    session_index=index,
                    step_name="image_generation",
                )
                save_png_from_bytes(image_bytes, original_image_path)
                metadata_image_filename = original_image_path.name
                if remove_background:
                    run_step_with_timing(
                        lambda: remove_background_ffmpeg(original_image_path, rembg_image_path),
                        logger=logger,
                        session_data=session_data,
                        session_index=index,
                        step_name="background_removal",
                    )
                    metadata_image_filename = rembg_image_path.name
                else:
                    logger.log_event(
                        "Background removal skipped",
                        session_index=index,
                        step="background_removal",
                    )
                metadata_text = run_step_with_retries(
                    lambda: generate_metadata_text(
                        client=metadata_client,
                        provider=metadata_provider,
                        image_path=original_image_path,
                        generation_prompt=prompt,
                        system_prompt=metadata_system_prompt,
                        category_list=category_list,
                        model=metadata_model,
                        temperature=temperature,
                        top_p=top_p,
                    ),
                    logger=logger,
                    session_data=session_data,
                    session_index=index,
                    step_name="metadata_generation",
                )
                metadata_rows.append(
                    {
                        "image_filename": metadata_image_filename,
                        "response_text": metadata_text,
                    }
                )
                session_duration = round_duration(time.perf_counter() - session_started_at)
                logger.finish_session(
                    session_data,
                    status="success",
                    duration_seconds=session_duration,
                )
                logger.log_event(
                    f"Session {index} completed",
                    session_index=index,
                    duration_seconds=session_duration,
                )
            except Exception as exc:
                session_duration = round_duration(time.perf_counter() - session_started_at)
                logger.finish_session(
                    session_data,
                    status="error",
                    duration_seconds=session_duration,
                    error=str(exc),
                )
                logger.log_event(
                    f"Session {index} failed",
                    level="ERROR",
                    session_index=index,
                    duration_seconds=session_duration,
                    error=str(exc),
                )
                raise

        save_metadata_outputs(run_dir=run_dir, rows=metadata_rows)
    except Exception as exc:
        logger.finish_run(
            status="error",
            duration_seconds=round_duration(time.perf_counter() - run_started_at),
            error=str(exc),
        )
        logger.log_event(
            "Run failed",
            level="ERROR",
            duration_seconds=round_duration(time.perf_counter() - run_started_at),
            error=str(exc),
        )
        raise

    logger.finish_run(
        status="success",
        duration_seconds=round_duration(time.perf_counter() - run_started_at),
    )
    logger.log_event(
        "Run completed",
        duration_seconds=round_duration(time.perf_counter() - run_started_at),
    )

    return run_dir


def parse_args(
    argv: list[str] | None = None,
    project_root: Path | None = None,
) -> argparse.Namespace:
    resolved_project_root = project_root or Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Generate a Google image and save outputs.")
    parser.add_argument("prompt", help="Prompt used for image generation.")
    parser.add_argument(
        "--model",
        default=get_default_image_model(resolved_project_root),
        help=(
            "Model to use. Default: IMAGE_MODEL from .env or "
            f"{DEFAULT_MODEL} if not set"
        ),
    )
    parser.add_argument(
        "--metadata-provider",
        choices=("google", "openrouter", "nim"),
        default=get_default_metadata_provider(resolved_project_root),
        help=(
            "Metadata provider to use. Default: METADATA_PROVIDER from .env or "
            f"{DEFAULT_METADATA_PROVIDER} if not set"
        ),
    )
    parser.add_argument(
        "--metadata-model",
        default=get_default_metadata_model(resolved_project_root),
        help=(
            "Metadata model to use. Default: METADATA_MODEL from .env or "
            f"{DEFAULT_METADATA_MODEL} if not set"
        ),
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=DEFAULT_TEMPERATURE,
        help=f"Sampling temperature for both model calls. Default: {DEFAULT_TEMPERATURE}",
    )
    parser.add_argument(
        "--top-p",
        type=float,
        default=DEFAULT_TOP_P,
        help=f"Top-p sampling value for both model calls. Default: {DEFAULT_TOP_P}",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=DEFAULT_COUNT,
        help=f"Number of images to generate sequentially. Default: {DEFAULT_COUNT}",
    )
    parser.add_argument(
        "--aspect-ratio",
        choices=VALID_ASPECT_RATIOS,
        default=DEFAULT_ASPECT_RATIO,
        help=f"Image aspect ratio. Default: {DEFAULT_ASPECT_RATIO}",
    )
    parser.add_argument(
        "--resolution",
        choices=VALID_RESOLUTIONS,
        default=DEFAULT_RESOLUTION,
        help=f"Image resolution preset. Default: {DEFAULT_RESOLUTION}",
    )
    args = parser.parse_args(argv)
    args.model = resolve_image_model_alias(args.model)
    args.metadata_provider = args.metadata_provider.lower()
    args.metadata_model = resolve_metadata_model_alias(args.metadata_model)
    return args


def main():
    project_root = Path(__file__).resolve().parent
    args = parse_args(project_root=project_root)
    run_dir = run(
        prompt=args.prompt,
        model=args.model,
        metadata_model=args.metadata_model,
        project_root=project_root,
        temperature=args.temperature,
        top_p=args.top_p,
        count=args.count,
        aspect_ratio=args.aspect_ratio,
        resolution=args.resolution,
        metadata_provider=args.metadata_provider,
    )
    print(f"[INFO] Saved outputs to: {run_dir}")


if __name__ == "__main__":
    main()
