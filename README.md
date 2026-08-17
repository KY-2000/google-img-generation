# Google Image Generation to Adobe Stock CSV

This repo generates images with Gemini, runs a second Gemini pass to create Adobe Stock metadata, and writes the results into an Adobe-style CSV.

## Workflow

For each run, the script does this:

1. Generate image(s) from your prompt with `gemini-3-pro-image-preview`
2. Remove the background from each generated image with `ffmpeg`
3. Send the generated image, the original prompt, and the category list to `gemini-2.5-flash`
4. Parse `Title`, `Keywords`, and `Category Code`
5. Write an Adobe Stock CSV file with:
   - `Filename`
   - `Title`
   - `Keywords`
   - `Category`
   - `Releases`

## Files

- `main.py`: main CLI script
- `category-code.txt`: Adobe Stock category list passed to the metadata model
- `prompts/adobe_stock_metadata_system_prompt.txt`: system prompt for metadata generation
- `Sample_Adobe_Stock_CSV_upload.csv`: Adobe sample CSV reference
- `csv-format.txt`: Adobe CSV format notes

## Requirements

- Python 3.10+
- `ffmpeg`
- `google-genai`
- `numpy`
- `Pillow`
- Google Cloud CLI (`gcloud`)

Install Python dependencies:

```bash
pip install google-genai numpy pillow
```

Install `ffmpeg` on macOS with Homebrew:

```bash
brew install ffmpeg
```

## API Key Setup

Create a `.env` file in the repo root:

```env
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
GOOGLE_CLOUD_LOCATION=global
GOOGLE_AUTH_MODE=adc
OPENROUTER_API_KEY=your_openrouter_key_here
NIM_API_KEY=your_nim_key_here
IMAGE_MODEL=nanobanana2
METADATA_PROVIDER=google
METADATA_MODEL=gemini-2.5-flash
```

For Google models, this repo now uses Vertex AI in one of two auth modes:
- `adc`
- `api_key`

The script reads these Google settings from:
- repo root `.env`
- or your shell environment

Required:
- `GOOGLE_AUTH_MODE`

Optional:
- `GOOGLE_CLOUD_PROJECT`
  Required when `GOOGLE_AUTH_MODE=adc`
- `GOOGLE_CLOUD_LOCATION`
  Defaults to `global`
- `VERTEX_API_KEY`
  Required when `GOOGLE_AUTH_MODE=api_key`

If you use OpenRouter for metadata generation, the script also reads `OPENROUTER_API_KEY`.
If you use NVIDIA NIM for metadata generation, the script also reads `NIM_API_KEY`.

## Vertex AI Authentication Setup

### Option 1: ADC

Use this if you want to authenticate with `gcloud`.

Example:

```env
GOOGLE_AUTH_MODE=adc
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
GOOGLE_CLOUD_LOCATION=global
```

Before running the script:

1. Create or select a Google Cloud project
2. Enable billing for that project
3. Enable the Vertex AI API
4. Install and initialize `gcloud`
5. Create local ADC credentials:

```bash
gcloud init
gcloud auth application-default login
```

On Windows PowerShell, if `gcloud` is not recognized, open **Google Cloud SDK Shell** from the Windows Start menu and run the same command there:

```powershell
gcloud auth application-default login
```

Or call `gcloud.cmd` directly from PowerShell:

```powershell
& "C:\Program Files (x86)\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd" auth application-default login
```

If that path does not exist, try:

```powershell
& "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd" auth application-default login
```

After ADC login finishes, return to the project terminal and rerun the Python command.

### Option 2: API key

Use this if your project allows Vertex AI API keys and you do not want to use `gcloud auth application-default login`.

Example:

```env
GOOGLE_AUTH_MODE=api_key
VERTEX_API_KEY=your-vertex-api-key
```

If you use `api_key` mode, `GOOGLE_CLOUD_PROJECT` is not required by this repo’s current client setup.

If you already have `VERTEX_API_KEY` in `.env` and do not want to use ADC, set:

```env
GOOGLE_AUTH_MODE=api_key
```

The default image model is read from `IMAGE_MODEL` if present. If `IMAGE_MODEL` is not set, it falls back to `gemini-3-pro-image-preview`.

Supported image model aliases:
- `nanobanana2` -> `gemini-3.1-flash-image-preview`
- `nanobananapro` -> `gemini-3-pro-image-preview`

Supported metadata providers:
- `google`
- `openrouter`
- `nim`

For `main.py`, `google` means Vertex AI through `google-genai` with `vertexai=True`. This repo should not use Google AI Studio / Gemini Developer API for Google-backed calls.

Supported metadata model aliases:
- `qwenfree` -> `qwen/qwen3.6-plus:free`
- `minimaxfree` -> `minimax/minimax-m2.5:free`
- `nemotronsuperfree` -> `nvidia/nemotron-3-super-120b-a12b:free`
- `kimik25` -> `moonshotai/kimi-k2.5`

## Basic Usage

Run from the repo root:

```bash
python3 main.py "a studio portrait of a young woman recording a vlog at home"
```

## XHS Wallpaper Workflow

Use `xhs_wallpaper_workflow.py` when you have a folder from:

```text
C:\Users\tanke\OneDrive\Desktop\xhs-files-downloads\<target_folder>
```

The folder must contain:
- one or more image files: `.png`, `.jpg`, `.jpeg`, `.webp`, `.bmp`, `.tif`, or `.tiff`
- `metadata.json` with optional `title`, `description`, and `hashtags`

The workflow does this:

1. Reads the input images and `metadata.json`.
2. Reverse-engineers one clean Chinese wallpaper generation prompt.
3. Ignores phone/screenshot noise such as time, notifications, app icons, status bars, phone frames, and social UI.
4. Generates images with configurable count, aspect ratio, and resolution.
5. Generates Adobe Stock metadata for the generated images.
6. Skips background removal for this workflow.
7. Writes all outputs to:

```text
output/<target_folder>/
```

Manual example:

```bash
python xhs_wallpaper_workflow.py "C:\Users\tanke\OneDrive\Desktop\xhs-files-downloads\some-folder" \
  --prompt-provider vertex \
  --metadata-provider vertex \
  --count 4 \
  --aspect-ratio 9:16 \
  --resolution 4K
```

Provider options:
- `vertex`: Vertex AI only, never Google AI Studio / Gemini Developer API
- `openrouter`
- `nim`

Default `.env` values for the XHS workflow:

```env
PROMPT_PROVIDER=vertex
METADATA_PROVIDER=vertex
IMAGE_MODEL=gemini-3-pro-image-preview
PROMPT_REVERSE_MODEL=gemini-2.5-flash
METADATA_MODEL=gemini-2.5-flash
IMAGE_COUNT=4
IMAGE_ASPECT_RATIO=9:16
IMAGE_RESOLUTION=4K
```

CLI arguments override `.env`, and `.env` overrides built-in defaults.

For AI Agent / Codex automation, call the same deterministic CLI instead of asking the agent to improvise the workflow:

```bash
python xhs_wallpaper_workflow.py "<absolute-xhs-target-folder>" \
  --prompt-provider vertex \
  --metadata-provider vertex \
  --count 4 \
  --aspect-ratio 9:16 \
  --resolution 4K \
  --overwrite
```

Use `--overwrite` only when the agent is allowed to replace `output/<target_folder>/`. Without `--overwrite`, the command fails if that output folder already exists.

XHS output folder contents include:

```text
output/<target_folder>/
  chinese_prompt.txt
  xhs_source_metadata.json
  xhs_source_images.json
  prompt.txt
  run_config.json
  session_log.txt
  session_log.json
  metadata.txt
  adobe_stock_metadata.csv
  img-<n>.png
```

XHS workflow does not run background removal. `adobe_stock_metadata.csv` references the generated `img-<n>.png` files directly.

## Batch XHS Wallpaper Workflow and Adobe Upload

## XHS Download Automation with Playwright

Use `tools/xhs_download_playwright.mjs` when Edge already has:

- XHS login state
- Tampermonkey installed
- `XHS-Downloader.js` installed and enabled
- site download permissions already configured

This script is intended to reduce AI/Computer Use clicking. It connects to the existing Edge session through CDP, opens the XHS board, triggers the Tampermonkey downloader, selects notes, waits for completion, and prints the downloaded folder paths.

### Reuse the Edge automation profile

Use one dedicated Edge profile for the full XHS + Adobe automation flow. The profile stores:

- XHS login state
- Adobe Contributor login state
- Tampermonkey and the `XHS-Downloader.js` userscript
- XHS file-system permissions for the download folder

Recommended Windows profile:

```text
%USERPROFILE%\.edge-xhs-adobe-automation
```

Start Edge with remote debugging and this profile before running the Playwright scripts:

```powershell
cmd /c start "" msedge --remote-debugging-port=9222 --user-data-dir="%USERPROFILE%\.edge-xhs-adobe-automation" --no-first-run --new-window "https://www.xiaohongshu.com/board/69da0e110000000017020f9b?source=web_user_page" "https://contributor.stock.adobe.com/en/uploads"
```

Then all browser automation commands should connect to that already-running Edge instance with:

```text
--cdp http://127.0.0.1:9222
```

Do not change `--user-data-dir` unless you intentionally want a fresh profile. A different profile will not have your XHS login, Adobe login, Tampermonkey script, or XHS folder permissions.

On the first XHS download in a new profile, Edge may show two browser/OS permission prompts:

1. `Select where this site can save changes`: choose the XHS download root, for example `C:\Users\tanke\OneDrive\Desktop\xhs-files-downloads`.
2. `Allow this site to edit files?`: click `Allow`.

These prompts are browser security permissions and are not normal page DOM. Playwright cannot reliably operate them directly. For a fully automated first-time bootstrap on Windows, pass `--auto-folder-permission`; this starts `tools/windows_edge_file_permission_helper.ps1`, which uses Windows UI Automation to fill the folder picker and click `Allow`.

After the permission is granted once, it is normally reused by the same Edge profile, same site, and same folder. You can keep `--auto-folder-permission` in the command; if no prompt appears, the helper exits after its timeout and the XHS script continues. The helper log is written under `final_runs/xhs_download/run_*/windows_folder_permission_helper.log`.

When the XHS download command finishes, it prints `XHS_DOWNLOAD_STAGE_COMPLETE`. That is the end of the downloader stage; run `xhs_wallpaper_batch_workflow.py` next if you want generation and Adobe upload.

The current `XHS-Downloader.js` reuses its previously granted directory handle. This allows normal runs to work in `--background` mode without opening `showDirectoryPicker()` again. After updating `XHS-Downloader/static/XHS-Downloader.js`, update the installed Tampermonkey userscript with the same file contents.

First-time setup or changing the download folder:

```powershell
cmd /c npm run xhs-download -- --board-url "<board-url>" --download-root "D:\new-xhs-download-root" --note-count 1 --cdp http://127.0.0.1:9222 --auto-folder-permission --reset-folder-permission
```

Normal background run after the folder has been granted:

```powershell
cmd /c npm run xhs-download -- --board-url "<board-url>" --download-root "D:\new-xhs-download-root" --note-count 1 --cdp http://127.0.0.1:9222 --background
```

`--background` prevents Playwright from explicitly calling `bringToFront()`, but Edge may still switch tabs when the XHS userscript calls `window.open()` for its helper tab. Browser-controlled popup/tab activation cannot be reliably disabled from Playwright. For uninterrupted daily work, run the dedicated automation profile in its own Edge window or virtual desktop. Start it with these optional background-execution flags:

```powershell
cmd /c start "" msedge --remote-debugging-port=9222 --user-data-dir="%USERPROFILE%\.edge-xhs-adobe-automation" --no-first-run --new-window --disable-background-timer-throttling --disable-backgrounding-occluded-windows --disable-renderer-backgrounding "https://www.xiaohongshu.com/board/69da0e110000000017020f9b?source=web_user_page" "https://contributor.stock.adobe.com/en/uploads"
```

Adobe upload is background-compatible when its Contributor tab is already open. The Adobe script reuses the existing `contributor.stock.adobe.com` tab, uses Playwright file chooser interception instead of native Windows file dialogs, does not call `bringToFront()`, and does not close the CDP-connected Edge instance.

If `--cdp http://127.0.0.1:9222` fails with `ECONNREFUSED`, Edge is not currently running with `--remote-debugging-port=9222`. Start the automation profile command above again.

Then run:

```powershell
cmd /c npm run xhs-download -- --board-url "https://www.xiaohongshu.com/board/69da0e110000000017020f9b?source=web_user_page" --download-root "C:\Users\tanke\OneDrive\Desktop\xhs-files-downloads" --note-count 1 --cdp http://127.0.0.1:9222 --auto-folder-permission --background
```

The downloader uses a conservative safety mode. It reuses an already-open matching board page instead of reloading it, applies fixed cooldowns between UI actions, limits each run to at most three notes, and stops immediately if XHS shows a frequency, risk, or verification warning. This reduces unnecessary platform load; it is not a method for bypassing XHS detection or controls. If a warning appears, stop automated runs and review the account/page manually before retrying.

Useful flags:

- `--note-count <n>`: number of notes to select in the downloader modal. Default is `1`; maximum is `3` per run.
- `--action-delay <ms>`: fixed cooldown after UI actions. Default and minimum are `5000`.
- `--navigation-cooldown <ms>`: cooldown after a required board navigation. Default and minimum are `10000`.
- `--cdp <url>`: connect to an existing Edge / Chrome session.
- `--user-data-dir <path>`: launch a persistent Edge profile when CDP is not used.
- `--background`: do not bring the XHS tab to the foreground during normal Playwright page operations. First-time Windows folder permission prompts may still temporarily take focus.
- `--auto-folder-permission`: Windows only. Start a PowerShell UI Automation helper for Edge's first-time folder picker and file edit permission prompts.
- `--reset-folder-permission`: clear the XHS-Downloader saved directory handle and choose `--download-root` again. Requires `--auto-folder-permission` and temporarily brings XHS to the foreground.
- `--folder-permission-timeout <seconds>`: helper timeout. Default follows `--timeout`.
- `--dry-run`: validate Playwright and the download folder without opening XHS.

After XHS download succeeds, process all downloaded child folders with:

```powershell
python xhs_wallpaper_batch_workflow.py "C:\Users\tanke\OneDrive\Desktop\xhs-files-downloads" --count 2 --aspect-ratio 9:16 --resolution 4K --overwrite --max-workers 2 --adobe-cdp http://127.0.0.1:9222 --adobe-file-type illustrations
```

Use `xhs_wallpaper_batch_workflow.py` when you have one parent folder that contains multiple XHS note folders and you want to process all of them in sequence.

Input shape:

```text
<xhs-download-root>/
  note-folder-a/
    metadata.json
    image_1.jpeg
  note-folder-b/
    metadata.json
    image_1.jpeg
```

The batch script only processes direct child folders that contain both `metadata.json` and at least one supported image file. For each valid child folder, it runs the same single-folder workflow as:

```bash
python xhs_wallpaper_workflow.py "<child-folder>" ...
```

After every child folder finishes successfully, the batch script creates one combined Adobe upload staging folder:

```text
output/_batch_<source-root-name>_<timestamp>/adobe_upload/
  adobe_stock_metadata.csv
  <folder-prefix>-img-1.png
  <folder-prefix>-img-2.png
  ...
```

This staging step rewrites duplicate filenames such as `img-1.png` into unique filenames, and rewrites the combined CSV so every `Filename` matches the copied upload image.

By default, child folders are moved after successful processing into:

```text
<xhs-download-root>/DONE_<timestamp>/
```

This makes it easy to see which downloaded XHS folders have already been handled. Pass `--no-move-done` to keep the source folders in place.

The batch script can process multiple child folders in parallel with `--max-workers`. Vertex AI can accept concurrent requests, but effective throughput is still limited by your project, region, model, and Vertex AI quota/throughput availability. Start conservatively with `--max-workers 2`; if you see `429 RESOURCE_EXHAUSTED`, lower the value or request more quota / provisioned throughput.

Example with an already-running Edge CDP session:

```bash
python xhs_wallpaper_batch_workflow.py "C:\Users\tanke\OneDrive\Desktop\xhs-files-downloads" \
  --prompt-provider vertex \
  --metadata-provider vertex \
  --count 2 \
  --aspect-ratio 9:16 \
  --resolution 4K \
  --overwrite \
  --max-workers 2 \
  --adobe-cdp http://127.0.0.1:9222 \
  --adobe-file-type illustrations
```

Useful flags:

- `--skip-adobe-upload`: run every XHS workflow and prepare the combined Adobe upload folder, but do not open Adobe.
- `--max-workers <n>`: process up to `<n>` child folders at the same time. Default is `1`.
- `--no-move-done`: do not move successfully processed child folders into `DONE_<timestamp>`.
- `--adobe-dry-run`: call `tools/adobe_upload_playwright.mjs` in dry-run mode after preparing the combined upload folder.
- `--adobe-cdp`: connect to an existing Edge or Chrome session started with `--remote-debugging-port=9222`.
- `--adobe-user-data-dir`: let Playwright launch a persistent browser profile instead of CDP.
- `--adobe-file-type photos|illustrations`: default is `illustrations`.
- `--no-adobe-mark-ai`: do not check Adobe's generative AI checkbox.
- `--adobe-mark-fictional`: check Adobe's fictional people/property checkbox when visible.
- `--no-adobe-save-work`: do not click Adobe's draft save button.

The Adobe upload step uses:

```bash
npm run adobe-upload -- --csv <combined-csv> --images <combined-upload-folder> ...
```

It uploads images, uploads the combined CSV, verifies metadata appears, optionally sets file type / AI flags, optionally saves draft work, and intentionally does not click Adobe's final `Submit <n> file(s)` button.

To backfill missing background-removed files for old runs without generating new images:

```bash
python3 remove_background_only.py
```

This scans every `output/<timestamp>/` folder, looks for original files named `img-<n>.png`, and only generates the matching `img-rembg-<timestamp>-<n>.png` when it is missing.

To compare exactly two metadata models against all original images in one existing run folder:

```bash
python3 compare_metadata_models.py output/20260407-153000 \
  --model-a google:gemini-2.5-flash \
  --model-b openrouter:qwen/qwen3.6-plus:free
```

This does not generate new images. It reads `prompt.txt`, runs metadata on each `img-<n>.png`, and writes side-by-side outputs under:

```text
output/<run>/comparisons/<timestamp>/
```

## CLI Options

```bash
python3 main.py "your prompt here" \
  --model gemini-3-pro-image-preview \
  --metadata-provider google \
  --metadata-model gemini-2.5-flash \
  --temperature 1.0 \
  --top-p 0.95 \
  --count 3 \
  --aspect-ratio 16:9 \
  --resolution 2K
```

### Options

- `prompt`: image generation prompt
- `--model`: image generation model
  If omitted, the script uses `IMAGE_MODEL` from `.env`, or `gemini-3-pro-image-preview` by default.
  You can also use aliases: `nanobanana2`, `nanobananapro`
- `--metadata-provider`: metadata provider, either `google` or `openrouter`
  or `nim`
- `--metadata-model`: metadata model
  You can also use aliases: `qwenfree`, `minimaxfree`, `nemotronsuperfree`, `kimik25`
- `--temperature`: sampling temperature for both model calls
- `--top-p`: top-p sampling value for both model calls
- `--count`: number of images to generate sequentially
- `--aspect-ratio`: one of `1:1`, `1:4`, `1:8`, `2:3`, `3:2`, `3:4`, `4:1`, `4:3`, `4:5`, `5:4`, `8:1`, `9:16`, `16:9`, `21:9`
- `--resolution`: one of `512`, `1K`, `2K`, `4K`

## Example

```bash
python3 main.py "cute bakery themed sticker sheet on pure blue background" \
  --metadata-provider nim \
  --metadata-model kimik25 \
  --count 2 \
  --aspect-ratio 1:1 \
  --resolution 1K \
  --temperature 1.1 \
  --top-p 0.95
```

## Output Structure

Each run creates a folder:

```text
output/<timestamp>/
```

Example contents for `--count 2`:

```text
output/20260407-153000/
  prompt.txt
  run_config.json
  metadata.txt
  adobe_stock_metadata.csv
  img-1.png
  img-2.png
  img-rembg-20260407-153000-1.png
  img-rembg-20260407-153000-2.png
```

### Output Files

- `prompt.txt`: the original image prompt
- `run_config.json`: saved run settings
- `metadata.txt`: raw metadata model output for all generated images, separated by blank lines
- `adobe_stock_metadata.csv`: Adobe-style CSV with one row per generated image
- `img-<n>.png`: original generated images
- `img-rembg-<timestamp>-<n>.png`: background-removed images

## CSV Behavior

The CSV header is:

```text
Filename,Title,Keywords,Category,Releases
```

Notes:
- `Filename` is the background-removed image filename
- `Category` uses the numeric `Category Code`
- `Releases` is currently written as blank

## What You Can Customize

Edit these files if needed:

- `category-code.txt`: change the category list sent to the metadata model
- `prompts/adobe_stock_metadata_system_prompt.txt`: change metadata instructions

You do not need to edit files to change the image prompt. Just pass a different prompt on the command line.

## Run Tests

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

## Troubleshooting

### `GOOGLE_CLOUD_API_KEY is missing`

This repo no longer uses `GOOGLE_CLOUD_API_KEY` for Google models.

Use one of these:

ADC mode:
- `GOOGLE_AUTH_MODE=adc`
- `GOOGLE_CLOUD_PROJECT`
- optionally `GOOGLE_CLOUD_LOCATION`

and authenticate with:

```bash
gcloud auth application-default login
```

API key mode:
- `GOOGLE_AUTH_MODE=api_key`
- `VERTEX_API_KEY`

### `ffmpeg` not found

Install `ffmpeg` and verify:

```bash
ffmpeg -version
```

### No image returned from API

Check:
- your ADC login
- your Google Cloud project
- the selected model name
- whether the image model supports the selected settings

### OpenRouter metadata call fails

Check:
- `OPENROUTER_API_KEY` is set
- `--metadata-provider openrouter` is selected
- the selected OpenRouter model supports image input

### Metadata output missing fields

If Gemini does not return all of:
- `Title`
- `Keywords`
- `Category Code`
- `Category Name`

the script will raise an error instead of writing incomplete CSV data.
