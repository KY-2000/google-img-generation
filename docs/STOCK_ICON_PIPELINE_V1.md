# Stock Icon Pipeline V1

Stock Icon Pipeline V1 turns approved Topic Finder queues into human-reviewable Adobe Stock illustration drafts. It coexists with the legacy `main.py` and XHS workflows and reuses their Vertex AI, metadata-provider, retry, logging, staging, and Playwright foundations.

## Architecture and ownership

ChatGPT Work produces weekly topic queues and may consume future monthly or quarterly exports. The local pipeline owns queue validation, deterministic `IMG_V1` prompt rendering, image generation, deterministic QC, `META_V2` generation and validation, SQLite state, stable filenames, Adobe CSV/staging, draft upload, retries, resume, and logs. No per-image ChatGPT Work call is required.

The checked-in contracts are `schemas/topic_queue.schema.json`, `schemas/metadata.schema.json`, `config/stock_icon_pipeline.json`, and the two versioned files under `config/prompts/`. Runtime validators mirror the schema and reject unknown or malformed fields.

## ChatGPT Work Finder Candidate Workflow

Research output and production state use separate contracts. ChatGPT Work writes a top-level array governed by `schemas/finder_candidates.schema.json`; this preserves strategy, buyer use case, differentiation, similarity risk, and optional regional/industry/role/problem context. `run_stock_pipeline.py --queue` continues to accept only the narrow object governed by `schemas/topic_queue.schema.json`.

Convert a Finder result before running production:

```powershell
python tools/convert_candidates_to_queue.py `
  --input queues/finder/incoming/adobe_stock_test_regional_commercial_20260828.json `
  --output queues/production/topic_queue_regional_commercial_20260828.json

python run_stock_pipeline.py `
  --queue queues/production/topic_queue_regional_commercial_20260828.json `
  --resume
```

For the same validation, registration, conversion, and production run in one command, use the explicit candidate entry point:

```powershell
python run_stock_pipeline.py `
  --candidates queues/finder/incoming/adobe_stock_test_regional_commercial_20260828.json `
  --resume
```

`--queue` and `--candidates` are mutually exclusive. The former remains production-only; the latter writes the validated generated queue under `queues/production/` before continuing through the same pipeline.

The converter validates the complete input, imports all candidates into SQLite, applies only explicit filters, reserves permanent local `AST000001`-style IDs for selected candidates, maps them to the unchanged production contract, validates that result, and atomically replaces the output file. Defaults convert every valid risk and score using `TF_V1_1`. Optional controls include `--min-score 85`, `--include-risk LOW MEDIUM`, `--strategy REGIONAL_COMMERCIAL`, `--run-id`, and `--database`.

SQLite records lineage by the composite research identity `(strategy, candidate_id)`. Re-importing or reconverting the same identity updates its research/source context but retains its existing `production_asset_id`; IDs are allocated from SQLite and are never derived from Finder prefixes. This makes later Adobe acceptance, rejection, download, and revenue records groupable by strategy without placing research-only fields in production queues.

When `--run-id` is omitted, the converter derives `TF_<STRATEGY>_<YYYYMMDD>` from strategy and a date embedded in the input filename (falling back to the source file date). `generated_at` is the actual conversion time with an `Asia/Kuala_Lumpur` timezone offset.

## Asset lifecycle

`asset_id` is permanent and follows `AST000001`. SQLite stores the topic definition, prompt/model versions, original image path, mapped Adobe filename, structured metadata, lifecycle timestamps, errors, retry count, and optional performance data. Queue re-import is idempotent only when the complete identity-defining topic record matches.

The explicit lifecycle is:

```text
QUEUED -> PROMPT_READY -> IMAGE_GENERATING -> IMAGE_READY
       -> METADATA_PENDING -> METADATA_READY -> READY_TO_STAGE
       -> STAGED -> READY_TO_UPLOAD -> UPLOADED_DRAFT
```

Image and metadata validation failures use `IMAGE_QC_FAILED` and `METADATA_QC_FAILED`; operational failures use `FAILED`. Per-asset exception boundaries keep the remaining queue running.

## Resume and review

Use `--resume` for normal reruns. Completed images and metadata are not regenerated. Interrupted image, metadata, and staging states recover from persisted paths/statuses. `--force-image` deliberately invalidates downstream work; `--force-metadata` retains the original image. Deterministic image QC checks existence, decoding, square dimensions, minimum resolution, and format. Semantic four-icon/commercial review remains a human pilot gate and is represented as `NOT_RUN`/`null`, never faked by deterministic CV.

Each asset folder contains its manifest, production prompt, untouched original PNG bytes, QC result, structured and raw metadata, plus text and JSON event logs.

## Staging and Adobe upload

Only current-queue `READY_TO_STAGE` assets enter a batch. Staging creates stable `AST000001.png` names, builds CSV from validated JSON, and verifies exact CSV/image correspondence. Re-staging is additive and reproducible. Once any member is `UPLOADED_DRAFT`, that batch ID is frozen; use `--batch-id BATCH_<name>_2` for late assets.

`--upload-draft` validates every manifest member before launching the inherited Playwright uploader. It uses `illustrations`, marks generative AI, saves work, records upload timestamps/timing, and never clicks Adobe's final Submit button.

## Rejected asset reuse on Dreamstime

`tools/process_adobe_rejections_to_dreamstime.py` is an independent downstream workflow. It reads Adobe's Not accepted page over the same CDP browser, applies an inclusive 14-day cutoff by default, extracts Adobe title/original filename/reason, resolves local originals and canonical keywords, and creates an audit manifest plus a populated copy of `Image_spreadsheet_template.xls`. It does not modify Stock Pipeline lifecycle state.

The Dreamstime preparation option uploads in CDP-safe chunks and configures each commercial form while preserving auto categories and the AI Contributor flag. Final `Submit commercial` remains disabled unless `--submit-commercial` is explicitly supplied. See the README for exact commands and resume options.

## Performance preparation

`python tools/import_adobe_performance.py performance.csv` imports snapshots with `asset_id,status,accepted_at,downloads,revenue,snapshot_date`. Adobe disposition is mapped to performance timestamps and never overwrites the production lifecycle status. V1 intentionally includes no Adobe scraper.
