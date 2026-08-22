# Stock Icon Pipeline V1

Stock Icon Pipeline V1 turns approved Topic Finder queues into human-reviewable Adobe Stock illustration drafts. It coexists with the legacy `main.py` and XHS workflows and reuses their Vertex AI, metadata-provider, retry, logging, staging, and Playwright foundations.

## Architecture and ownership

ChatGPT Work produces weekly topic queues and may consume future monthly or quarterly exports. The local pipeline owns queue validation, deterministic `IMG_V1` prompt rendering, image generation, deterministic QC, `META_V2` generation and validation, SQLite state, stable filenames, Adobe CSV/staging, draft upload, retries, resume, and logs. No per-image ChatGPT Work call is required.

The checked-in contracts are `schemas/topic_queue.schema.json`, `schemas/metadata.schema.json`, `config/stock_icon_pipeline.json`, and the two versioned files under `config/prompts/`. Runtime validators mirror the schema and reject unknown or malformed fields.

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

## Performance preparation

`python tools/import_adobe_performance.py performance.csv` imports snapshots with `asset_id,status,accepted_at,downloads,revenue,snapshot_date`. Adobe disposition is mapped to performance timestamps and never overwrites the production lifecycle status. V1 intentionally includes no Adobe scraper.
