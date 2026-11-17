# Adobe Stock Batch Manager

Windows 11 desktop front end for the existing stock pipeline. Use Python 3.11+
with Tkinter
(included in the current Windows Python installation) and a separate worker
process. The single-queue CLI and manual Adobe uploader remain available.

## First run

From the repository directory, install the Python and Node dependencies:

~~~powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm ci
~~~

If the virtual environment is missing, first run `python -m venv .venv`.
Configure `.env` and Google credentials as for the single-queue pipeline.
Choose **Nano Banana Pro** (default) or **Nano Banana 2.1** in the
**Image model** dropdown. The choice overrides `IMAGE_MODEL` for that run
and applies only to new generation; completed images are reused. For new
metadata, select `gemini-3.8-flash` (default) to use Vertex AI, or
`gemini-4-hydrogen` to use the Gemini Developer API. Other model IDs entered
in the editable field use Vertex AI. For Hydrogen, add `GEMINI_API_KEY` from
your AI Studio project to the repository's
`.env`. Your AI Studio project's billing tier and quota apply to these metadata
requests. Image generation still uses Vertex AI.
If Hydrogen returns HTTP 503, the rest of that run's metadata uses
`gemini-3.8-flash` through Vertex AI. Vertex AI billing applies to those requests.
Offline test mode does not invoke either model.

**Parallel assets** runs up to 1, 2, or 3 assets from the active queue at
once. Default is 1. Each asset runs image generation, QC, and metadata in
order. The image limit applies to the whole run. When stopped, active assets
finish and queued assets wait for the next run. Adobe upload starts after
all selected queues pass the existing quality gate.

For Nano Banana Pro, **Temperature** defaults to 1.0 and **Top-p** to 0.95.
Nano Banana 2.1 disables these controls. **Max new images** works with both:
leave it blank for unlimited, or enter a positive whole number. The limit is
per confirmed run, across the selected queues. One slot is used per asset that starts generation,
even if it fails; the existing retries for that asset still apply. Images
already on disk do not use a slot. Select that queue again to resume deferred assets.
The combined upload waits if a selected queue still has unfinished work. The queue's
resolution and one-image-per-asset behavior are unchanged.

The checkout must be on a **clean local main** with
`KY-2000/google-img-generation` as `origin`. The manager fast-forwards from
`origin/main` when possible. If the branches diverge, it creates a local merge
only when the remote changes exclusively add new JSON files under
`queues/incoming/` and those paths do not collide with local files. Other
divergence, an invalid branch or remote, and network errors stop before queue
discovery. Review and integrate code changes locally before starting a live run.
**Push to main** syncs with `origin/main` first, then publishes the committed
tip of local `main` to `origin/main`. It uses the same GitHub credentials as
Git and reports success or rejection in the activity log. A successful
push makes the commit hash open its GitHub diff.

The default CDP endpoint is `http://127.0.0.1:9222`. When upload is ready, the
manager reuses a connected Edge session or starts Edge with the user profile
`.edge-xhs-adobe-automation` in your home directory. Sign in to Adobe if the
profile's session has expired. Clear the CDP field to let Playwright launch
the repository's persistent `.pw-edge-adobe-profile` instead. Set
`ADOBE_USER_DATA_DIR` to override the launch profile in either mode, or
`ADOBE_CDP` to override the default endpoint. Resume with the same Adobe account.

Double-click `start_stock_manager.bat` or run:

~~~powershell
.\start_stock_manager.bat
~~~

To pin a Windows launcher, run `powershell -NoProfile -ExecutionPolicy Bypass -File .\build_stock_manager_launcher.ps1`.
Right-click `output/stock_manager/Adobe Stock Batch Manager.exe` and choose
**Show more options → Pin to taskbar**. Keep the EXE there; the launcher
uses this checkout's existing Python virtual environment.

The window opens with **Offline test** selected. In this mode, **Run Offline Test** only
validates `commercial_background_queue_2026W44_r02.json` and writes a local
report. It does not sync Git, open Adobe, create images, or update SQLite.
Uncheck **Offline test** to run live with the Git safety checks.

The local `work_io/topic_finder/manager_baseline.json` snapshot skips the
existing queue contents except r02 in live runs. It does not mark anything
uploaded. New or changed content is considered normally. To create the initial
snapshot on another installation, use `stock_batch_manager.py --snapshot-baseline`
with the project's Python. The command refuses to overwrite an existing snapshot.

In live mode, **Check Queues** syncs Git and opens a read-only preview.
It reports new and updated JSONs pulled in that sync, pending queues, themes,
the total images in each queue, and how many are estimated to need generation.
Choose how many of the first queues to run in the displayed order.
**Start Selected** authorizes paid model calls and upload to Adobe drafts for
those queues. Closing the preview cancels the run. If the checkout, queues, or
their previewed progress change while it is open, check queues again.
Final Submit is always manual. **Retry Failed** opens the same preview.
**Stop After Active Assets** and closing the window wait for a safe boundary;
a request already running may finish.

## Files, state, and ordering

- Scan `queues/incoming/` recursively. Production objects and Finder arrays
  must pass the repository JSON Schemas and Python contracts. A wholly
  unnumbered production queue is allowed; mixed IDs are an error. Files ending
  in `.example.json` are listed as templates and never generated or uploaded.
- Sources sort by `generated_at`, then path. Finder arrays use the synced
  commit time. Identical copies are skipped. Incompatible contents for a
  known `run_id`, duplicate AST IDs, and partial overlaps with another
  Finder batch are blocked. A complete, unchanged legacy Finder batch
  adopts its original run and AST IDs. Use a new run ID for a new batch.
- Production copies live under `work_io/topic_finder/runtime_queues/`.
  Missing IDs are allocated there, considering local queues, asset outputs,
  SQLite assets, candidate reservations and the allocator's high watermark.
  Finder conversion uses a content-derived run ID, so distinct batches on
  the same date do not collide. Original JSON files are left untouched.
  Git-ignored local JSON can run; tracked incoming JSON still syncs.
- Queue and asset states are stored in `data/stock_pipeline.sqlite`. Completed
  assets are reused. Invalid files are visible in the preview and cannot be
  selected. Discovery checks conflicts across all incoming queues before
  selection. Any selected queue that fails or stays incomplete blocks the
  combined upload for that selection.
- Images and metadata retain their existing paths under `output/assets/`.
  Combined manifests and CSVs are in `output/adobe_batches/BATCH_COMBINED_*/`.
  Every manifest row identifies the source file, commit, content hash, run,
  asset, and prepared image hash.
- Activity logs, JSON and Markdown reports are under
  `output/stock_manager/runs/`. **Open Logs** opens the current run folder.
- **Open ChatGPT + Copy Error Log** refreshes the redacted excerpt from the
  current run, or the most recent run with an error, and opens the local chat configured in
  `output/stock_manager/support_chat.txt`. Paste it into the composer with
  **Ctrl+V**. To change the destination, save the chat's desktop deep link in
  that file.

## Draft recovery and limits

Managed upload reuses Playwright and reconciles exact filenames. It records
an attempt for each PNG before sending an image batch, imports CSV metadata, sets Illustrations
and Generative AI, saves, then reloads to verify the filename, title,
keywords, category and flags. It writes a saved receipt under
`output/stock_manager/adobe_receipts/` **before** updating SQLite. If a
database update fails, Start replays the receipt without sending the file
again. Existing legacy `UPLOADED_DRAFT` states are respected.
During upload, a stop request takes effect between image batches or drafts.
With Playwright 1.58 or newer, Batch Manager uses local file paths over CDP
and groups PNGs into batches of up to 120 MB. Older Playwright versions use
the 45 MB batches. The Adobe upload wait for larger batches scales with size.

- **Login expired:** sign in again with the same account/profile and retry.
  Local generation is reused.
- **Partial upload:** retry. An attempted file still visible by exact name
  resumes metadata and save without re-upload. An untracked draft is only
  adopted if its saved fields and flags already match.
- **Attempted file not visible:** stop and check drafts and submission history.
  It might have been accepted, submitted, deleted, or hidden by the page.
  Only after confirming Adobe never accepted it, move the corresponding
  `*.attempt.json` out of the receipt folder if you want to retry.
  Never remove a `*.saved.json` to retry a confirmed draft.
- **Grid filters or DOM changes:** the worker scans every page of the English
  **Uploaded Files → New → File types: All** grid before uploading. If it
  cannot account for the reported draft count or verify a saved field, check
  the page filter and the inventory counts in the run log.
- **Sudden shutdown:** the next Start reuses local artifacts and saved
  receipts. A model request interrupted before its result was written
  locally may need to be retried. Do not run the manual pipeline or other
  upload jobs against the same assets while the manager is running.

## Offline checks

~~~powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
node --test tests/adobe_managed_upload.test.mjs tests/adobe_upload_playwright.test.mjs
~~~

The integration tests exercise real temporary SQLite and the existing pipeline
with mock image/metadata generation and mock upload. JavaScript tests simulate
Adobe and check receipt-based recovery. Neither test uses paid generation or
a live Adobe account.
