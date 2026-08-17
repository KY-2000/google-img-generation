# XHS Wallpaper Agent Automation Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an agent-friendly workflow that reads one XHS download folder, reverse-engineers clean Chinese wallpaper generation prompts from images plus metadata, generates a configurable number of images at configurable resolution/aspect ratio, and writes Adobe Stock-ready metadata outputs into a named output folder.

**Architecture:** Keep the existing image generation and Adobe Stock metadata logic in `main.py`, but add a dedicated XHS ingestion and prompt-reversal workflow around it. The workflow must not use Google AI Studio / Gemini Developer API. Google-backed calls must go through Vertex AI only, while OpenRouter and NVIDIA NIM remain valid non-Google provider options where the workflow exposes provider selection. The workflow should be deterministic at the filesystem level: one target folder in, one `output/<target_folder>/` folder out, with prompt, generated images, raw metadata, CSV, config, and logs saved together.

**Tech Stack:** Python, `google-genai` Vertex AI client with `vertexai=True`, optional OpenRouter/NVIDIA NIM metadata or prompt providers, Pillow, existing CSV metadata writer, existing retry/logging helpers.

---

## Required User Flow

Input:
- User provides `C:\Users\tanke\OneDrive\Desktop\xhs-files-downloads\{target_folder}`.
- Folder contains wallpaper-like images, often screenshots or phone-wallpaper photos with noise such as time, notifications, app icons, phone frame/body, bezels, reflections, and UI overlays.
- Folder contains `metadata.json`.
- `metadata.json` fields to read:
  - `title`
  - `description`
  - `hashtags`

Output:
- Create `output/{target_folder}/` inside this repo.
- Save the final Chinese generation prompt.
- Generate images with configurable count, resolution, and aspect ratio.
- Defaults should remain wallpaper-oriented: `count=4`, `resolution=4K`, `aspect_ratio=9:16`.
- Generate Adobe Stock metadata for each generated image using Vertex AI, OpenRouter, or NVIDIA NIM and the existing metadata format and CSV logic.
- Save all generated images, Chinese prompt, title/keywords/category outputs, CSV, config, and logs in that output folder.

## Required API Configuration

This workflow must not use Google AI Studio / Gemini Developer API. If a Google/Gemini model is used, it must be accessed through Vertex AI with `vertexai=True`.

Required from the user before real API execution:
- If using Vertex AI:
  - Google Cloud project ID with billing enabled.
  - Vertex AI API enabled in that Google Cloud project.
  - Vertex AI location/region, default `global` unless the selected models require a specific region.
  - Authentication mode:
  - Preferred for local automation: Application Default Credentials (`GOOGLE_AUTH_MODE=adc`) created with `gcloud auth application-default login`.
  - Alternative if supported by the project: Vertex API key auth (`GOOGLE_AUTH_MODE=api_key` plus the key variable used by the code).
- If using OpenRouter for prompt reversal or metadata:
  - `OPENROUTER_API_KEY`
  - OpenRouter model names that support image input.
- If using NVIDIA NIM for prompt reversal or metadata:
  - `NIM_API_KEY`
  - NIM model names that support image input.
- Image generation model name. Current default for Vertex image generation: `gemini-3-pro-image-preview`.
- Prompt reversal provider/model. The selected model must support image input.
- Metadata provider/model. The selected model must support image input.
- Optional generation parameters:
  - `IMAGE_COUNT`, default `4`
  - `IMAGE_RESOLUTION`, default `4K`
  - `IMAGE_ASPECT_RATIO`, default `9:16`

Expected `.env` shape for ADC:

```env
GOOGLE_AUTH_MODE=adc
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
GOOGLE_CLOUD_LOCATION=global
IMAGE_MODEL=gemini-3-pro-image-preview
PROMPT_REVERSE_MODEL=gemini-2.5-flash
METADATA_MODEL=gemini-2.5-flash
IMAGE_COUNT=4
IMAGE_RESOLUTION=4K
IMAGE_ASPECT_RATIO=9:16
```

Expected `.env` shape for Vertex API key auth, if supported:

```env
GOOGLE_AUTH_MODE=api_key
VERTEX_API_KEY=your-vertex-api-key
IMAGE_MODEL=gemini-3-pro-image-preview
PROMPT_REVERSE_MODEL=gemini-2.5-flash
METADATA_MODEL=gemini-2.5-flash
IMAGE_COUNT=4
IMAGE_RESOLUTION=4K
IMAGE_ASPECT_RATIO=9:16
```

Expected optional provider keys:

```env
OPENROUTER_API_KEY=your-openrouter-api-key
NIM_API_KEY=your-nim-api-key
PROMPT_PROVIDER=vertex
METADATA_PROVIDER=vertex
IMAGE_COUNT=4
IMAGE_RESOLUTION=4K
IMAGE_ASPECT_RATIO=9:16
```

Implementation requirement:
- Standardize the Vertex API key variable before execution. Current code reads `VERTEX_API_KEY`; README currently mentions `GOOGLE_API_KEY`. Use `VERTEX_API_KEY` for Vertex auth unless the implementation deliberately changes both code and docs together.
- Do not instantiate `genai.Client(api_key=...)` without `vertexai=True`; that would be the Google AI Studio / Developer API path and is not allowed for this workflow.

## Existing Behavior To Reuse

- Vertex AI image generation currently uses `google-genai` with `vertexai=True` in `main.py`.
- Default image model is `gemini-3-pro-image-preview`.
- Default metadata provider is currently named `google` in code, but the XHS automation should rename or document this as the Vertex AI provider to avoid confusion with Google AI Studio.
- Default metadata model is `gemini-2.5-flash`.
- Existing metadata providers also include OpenRouter and NVIDIA NIM; these remain valid for XHS prompt reversal or metadata if selected.
- Existing Adobe Stock metadata output format is driven by `prompts/adobe_stock_metadata_system_prompt.txt`.
- Existing metadata output writer already creates Adobe-style CSV rows.

## Important Semantics

- Prompt reversal should focus on the wallpaper content itself, not the screenshot or phone presentation.
- Ignore or explicitly remove:
  - phone device
  - phone frame
  - screen bezel
  - time display
  - notification bar
  - status bar
  - app icons
  - navigation gestures
  - social media UI
  - watermarks when not part of the wallpaper
  - hand holding phone unless the final goal is lifestyle stock, which this workflow is not
- Use `metadata.json` as supporting context, not as the source of truth.
- The image content should remain primary.
- Final reversed prompt must be Chinese.
- Reversed prompt should describe how to generate a similar style and element composition, not identify the source image.

## Proposed File Structure

- Create: `xhs_wallpaper_workflow.py`
  - CLI entrypoint for the complete automation.
  - Accepts the XHS target folder path.
  - Creates `output/<target_folder>/`.
  - Orchestrates prompt reversal, image generation, metadata generation, and output writing.

- Modify: `main.py`
  - Extract or expose reusable helpers without changing current CLI behavior.
  - Allow `run()` or a new helper to accept an explicit output folder name/path.
  - Allow generated prompt to be passed from the XHS workflow.
  - Keep existing default CLI working.

- Create: `prompts/xhs_wallpaper_reverse_prompt_system_prompt.txt`
  - System prompt for reverse-engineering a clean Chinese wallpaper generation prompt.
  - Must instruct the model to ignore phone/screenshot/UI noise.

- Modify: `tests/test_main.py`
  - Add tests only for shared helper changes.

- Create: `tests/test_xhs_wallpaper_workflow.py`
  - Unit tests for target folder parsing, metadata loading, prompt input construction, output folder naming, and orchestration boundaries.

## Chunk 1: Filesystem Input And Metadata Parsing

### Task 1: Load XHS Folder Metadata

**Files:**
- Create: `xhs_wallpaper_workflow.py`
- Test: `tests/test_xhs_wallpaper_workflow.py`

- [ ] **Step 1: Write failing tests**

Cover:
- Finds `metadata.json`.
- Reads `title`, `description`, and `hashtags`.
- Handles missing optional fields as empty strings/lists.
- Lists input image files from the target folder.
- Excludes `metadata.json` and non-image files.

- [ ] **Step 2: Run tests**

Run: `python -m pytest tests/test_xhs_wallpaper_workflow.py -v`

- [ ] **Step 3: Implement metadata and image discovery**

Add functions:
- `load_xhs_metadata(target_dir: Path) -> dict[str, object]`
- `list_xhs_input_images(target_dir: Path) -> list[Path]`
- `get_target_output_name(target_dir: Path) -> str`

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_xhs_wallpaper_workflow.py -v`

## Chunk 2: Chinese Wallpaper Prompt Reversal

### Task 2: Add Prompt-Reversal System Prompt

**Files:**
- Create: `prompts/xhs_wallpaper_reverse_prompt_system_prompt.txt`
- Modify: `xhs_wallpaper_workflow.py`
- Test: `tests/test_xhs_wallpaper_workflow.py`

- [ ] **Step 1: Write failing tests**

Cover:
- User prompt includes metadata title, description, hashtags.
- User prompt includes all selected input images.
- User prompt explicitly says to ignore phone/screenshot/UI noise.
- Final expected output language is Chinese.

- [ ] **Step 2: Implement prompt builder**

Add:
- `build_wallpaper_reverse_user_prompt(metadata: dict[str, object]) -> str`
- `generate_chinese_wallpaper_prompt(...) -> str`

Use Vertex AI by default for prompt reversal. OpenRouter and NVIDIA NIM may be added as selectable prompt-reversal providers if the selected model supports image input. Do not use Google AI Studio / Gemini Developer API.

- [ ] **Step 3: Persist prompt**

Save:
- `output/<target_folder>/chinese_prompt.txt`
- optionally `output/<target_folder>/reverse_prompt_raw.txt` if the model returns extra reasoning or structured output.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_xhs_wallpaper_workflow.py -v`

## Chunk 3: Generate Configurable Images

### Task 3: Reuse Existing Image Generation

**Files:**
- Modify: `main.py`
- Modify: `xhs_wallpaper_workflow.py`
- Test: `tests/test_main.py`
- Test: `tests/test_xhs_wallpaper_workflow.py`

- [ ] **Step 1: Write failing tests**

Cover:
- XHS workflow defaults image generation to `count=4`.
- XHS workflow defaults to `aspect_ratio="9:16"`.
- XHS workflow defaults to `resolution="4K"`.
- XHS workflow accepts overrides for count, aspect ratio, and resolution.
- CLI overrides take precedence over `.env`; `.env` values take precedence over hardcoded defaults.
- Uses the generated Chinese prompt.
- Writes into `output/<target_folder>/`.

- [ ] **Step 2: Refactor output folder handling**

Add a narrowly scoped option to reuse current `run()` behavior with a provided `run_dir` or output name.

- [ ] **Step 3: Run tests**

Run:
- `python -m pytest tests/test_main.py -v`
- `python -m pytest tests/test_xhs_wallpaper_workflow.py -v`

## Chunk 4: Adobe Stock Metadata For Generated Images

### Task 4: Reuse Existing Metadata Pipeline

**Files:**
- Modify: `xhs_wallpaper_workflow.py`
- Test: `tests/test_xhs_wallpaper_workflow.py`

- [ ] **Step 1: Write failing tests**

Cover:
- Metadata generation receives each generated image.
- Metadata prompt input uses the Chinese prompt as the generation prompt.
- Provider can be Vertex AI, OpenRouter, or NVIDIA NIM.
- No XHS workflow CLI or orchestration path can use Google AI Studio / Gemini Developer API.
- CSV and raw metadata outputs land in `output/<target_folder>/`.

- [ ] **Step 2: Implement orchestration**

Reuse:
- `generate_metadata_text`
- `parse_metadata_response`
- `save_metadata_outputs`
- existing Vertex AI/OpenRouter/NIM client builders

- [ ] **Step 3: Run tests**

Run: `python -m pytest tests/test_xhs_wallpaper_workflow.py -v`

## Chunk 5: Agent-Friendly CLI

### Task 5: Add Complete Automation Command

**Files:**
- Modify: `xhs_wallpaper_workflow.py`
- Modify: `README.md`
- Test: `tests/test_xhs_wallpaper_workflow.py`

- [ ] **Step 1: Write CLI tests**

Cover:
- Required positional `target_folder`.
- Optional `--prompt-provider`.
- Optional `--metadata-provider`.
- Optional `--metadata-model`.
- Optional `--prompt-model`.
- Optional `--count`.
- Optional `--aspect-ratio`.
- Optional `--resolution`.
- Provider choices must exclude Google AI Studio / Gemini Developer API. Google-backed provider should be named `vertex` in the XHS CLI even if existing internal helpers still use `google`.
- Defaults remain agent-friendly.

- [ ] **Step 2: Implement CLI**

Example:

```bash
python xhs_wallpaper_workflow.py "C:\Users\tanke\OneDrive\Desktop\xhs-files-downloads\some-folder"
```

With generation overrides:

```bash
python xhs_wallpaper_workflow.py "C:\Users\tanke\OneDrive\Desktop\xhs-files-downloads\some-folder" \
  --count 6 \
  --aspect-ratio 9:16 \
  --resolution 4K
```

Expected output folder:

```text
output/some-folder/
```

- [ ] **Step 3: Update README**

Document the new workflow separately from the current one-image-generation CLI.

- [ ] **Step 4: Final verification**

Run:
- `python -m pytest -v`
- One dry-run-style test with mocked API calls.

## Open Implementation Notes

- Do not overwrite an existing `output/<target_folder>/` silently. Prefer creating a timestamped suffix or failing with a clear message unless the user passes `--overwrite`.
- Keep API calls isolated behind small functions so Codex can mock them during tests.
- Keep logs in the output folder for agent auditability.
- Current code reads `VERTEX_API_KEY`, while README mentions `GOOGLE_API_KEY`; fix this inconsistency before relying on API key auth.
- The prompt reversal model must be vision-capable. If it is a Google/Gemini model, it must be available through Vertex AI.
