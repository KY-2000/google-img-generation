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
- `GOOGLE_API_KEY`
  Required when `GOOGLE_AUTH_MODE=api_key`

If you use OpenRouter for metadata generation, the script also reads `OPENROUTER_API_KEY`.

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

### Option 2: API key

Use this if your project allows Vertex AI API keys and you do not want to use `gcloud auth application-default login`.

Example:

```env
GOOGLE_AUTH_MODE=api_key
GOOGLE_API_KEY=your-google-api-key
```

If you use `api_key` mode, `GOOGLE_CLOUD_PROJECT` is not required by this repo’s current client setup.

The default image model is read from `IMAGE_MODEL` if present. If `IMAGE_MODEL` is not set, it falls back to `gemini-3-pro-image-preview`.

Supported image model aliases:
- `nanobanana2` -> `gemini-3.1-flash-image-preview`
- `nanobananapro` -> `gemini-3-pro-image-preview`

Supported metadata providers:
- `google`
- `openrouter`

Supported metadata model aliases:
- `qwenfree` -> `qwen/qwen3.6-plus:free`
- `minimaxfree` -> `minimax/minimax-m2.5:free`
- `nemotronsuperfree` -> `nvidia/nemotron-3-super-120b-a12b:free`

## Basic Usage

Run from the repo root:

```bash
python3 main.py "a studio portrait of a young woman recording a vlog at home"
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
- `--metadata-model`: metadata model
  You can also use aliases: `qwenfree`, `minimaxfree`, `nemotronsuperfree`
- `--temperature`: sampling temperature for both model calls
- `--top-p`: top-p sampling value for both model calls
- `--count`: number of images to generate sequentially
- `--aspect-ratio`: one of `1:1`, `1:4`, `1:8`, `2:3`, `3:2`, `3:4`, `4:1`, `4:3`, `4:5`, `5:4`, `8:1`, `9:16`, `16:9`, `21:9`
- `--resolution`: one of `512`, `1K`, `2K`, `4K`

## Example

```bash
python3 main.py "cute bakery themed sticker sheet on pure blue background" \
  --metadata-provider openrouter \
  --metadata-model qwenfree \
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
- `GOOGLE_API_KEY`

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
