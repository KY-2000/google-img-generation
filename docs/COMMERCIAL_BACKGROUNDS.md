# Commercial backgrounds in the existing stock pipeline

The existing `run_stock_pipeline.py` CLI supports both legacy icon queues and
commercial background queues. No separate runner is needed.

```powershell
.\.venv\Scripts\python.exe .\run_stock_pipeline.py `
  --queue .\queues\incoming\commercial_background_queue_2026W44.json `
  --resume `
  --upload-draft
```

This generates eight illustrations using the existing model/authentication
configuration, creates metadata and an Adobe batch, and saves an Adobe draft.
The uploader requires the existing authenticated browser setup. It marks AI
content and saves work; it does not submit to Adobe for review. When using an
existing remote-debugging browser, append `--cdp http://127.0.0.1:9222`.

For generation, metadata and staging without browser upload, omit `--upload-draft`.
For a no-network prompt check, use `--dry-run` without `--upload-draft`.
Dry runs still register assets and write prompts locally.

## Queue contract

The queue keeps `run_id`, `topic_finder_version`, `generated_at`, and `assets`.
Run IDs keep the `TF_` prefix and asset IDs keep the globally unique `AST######`
format required by the registry, filename mapping and Adobe staging code.

Each commercial background retains the common production fields (`domain`,
`theme`, `source_type`, `topic_score`, `topic_bucket`, `palette`, `status`, and
`topic_finder_version`) and adds:

```json
{
  "asset_type": "commercial_background",
  "prompt": "One full-frame commercial illustration, with copy space...",
  "negative_prompt": "text, logos, watermark, icon grid",
  "aspect_ratio": "16:9",
  "resolution": "4K",
  "remove_background": false
}
```

Allowed background ratios are `16:9` and `9:16`. The complete prompt is used
verbatim, followed by a `Negative prompt:` block. The icon template is bypassed.
One original image is generated per asset. This pipeline calls the existing
`main.generate_image_bytes()` function directly, not `main.run()`, and has no
background-removal step. Background QC checks the requested ratio within 2%
and the configured minimum resolution on the long edge (currently 4096).

Omitting `asset_type` continues to mean `icon_set`; explicitly setting
`icon_set` is equivalent. All four icon fields, the original prompt template,
configured image settings, square QC, metadata and staging behavior remain.

## Converted W44 queue

- `approved_queue` became `assets` and `run_id` gained `TF_`.
- IDs were assigned `AST990024` through `AST990031`, above IDs found in the
  existing queues and registry at conversion time.
- `opportunity_score` maps to `topic_score`; status and topic bucket are
  `APPROVED`; the palette is a production-compatible string.
- Portrait compositions changed from 4:5 to 9:16 and landscape compositions
  from 3:2 to 16:9 where applicable. Ratio mentions inside prompts match.
- Original IDs, statuses, prompts, palette arrays, aspect ratios, buyer/event
  research and source URLs are preserved in queue/asset `research` objects.

## Persistence and resume

The registry automatically adds one `asset_config_json` column to existing
databases. Existing rows default to `{}`. Background prompt/settings/research
persist in this column and in background manifests. Reusing an asset ID with
changed production settings is rejected instead of silently resuming stale art.

Assets remain under `output/assets/AST######/`. The W44 batch is
`output/adobe_batches/BATCH_commercial_background_2026W44_initial/`.
Existing status-based resume skips completed assets and reuses a saved image
when retrying failed metadata. Existing Adobe upload recovery controls remain:
`--upload-resume-after` is an explicitly verified upload prefix; generation
resume does not infer progress inside a failed browser upload. A batch already
marked `UPLOADED_DRAFT` cannot be uploaded again through `--upload-draft`.

## Offline validation

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --test tests/adobe_upload_playwright.test.mjs tests/adobe_rejected_dreamstime_playwright.test.mjs
```

Background tests use local PNGs and fake metadata generators. Coverage includes
both orientations, direct prompts, rejected settings, schema migration,
interrupted resume, staging and draft status transitions. No paid generation
API or live Adobe browser upload is exercised by these tests.

## QC failure recovery

Gemini responses can contain intermediate images marked `thought=True`.
The shared image reader excludes these and saves only final image parts.
Responses containing only thought images fail instead of saving a draft.

If a saved image fails QC, its status is `IMAGE_QC_FAILED`. `--resume` rechecks
the saved file without generating another image or bypassing QC. Failed runs
skip Adobe upload and print their summary instead of reading a missing batch
manifest. The same recovery also handles older `FAILED` rows with a saved
image. To deliberately regenerate failed QC images after fixing an API issue,
use `--force-image`; that makes new paid requests and replaces the original.
