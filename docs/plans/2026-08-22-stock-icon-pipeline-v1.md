# Stock Icon Pipeline V1 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Refactor the existing image-generation repository into an idempotent, resumable Adobe Stock four-icon production pipeline while retaining the legacy XHS workflow.

**Architecture:** Add an isolated `stock_pipeline` Python package around the proven clients and retry primitives in `main.py`. A SQLite registry is the source of truth; versioned JSON schemas and prompt files define external contracts; deterministic validators and staging code gate every irreversible or costly step. The root CLI composes these modules and injects generation functions so unit tests never call external services.

**Tech Stack:** Python 3.10+, stdlib SQLite/JSON/CSV, Pillow, existing `google-genai` providers, existing Node/Playwright Adobe uploader, `unittest`/pytest-compatible tests.

---

### Task 1: Contracts, configuration, and prompt rendering

**Files:**
- Create: `schemas/topic_queue.schema.json`
- Create: `schemas/metadata.schema.json`
- Create: `config/stock_icon_pipeline.json`
- Create: `config/prompts/image_prompt_v1.txt`
- Create: `config/prompts/metadata_v2.txt`
- Create: `stock_pipeline/contracts.py`
- Create: `stock_pipeline/prompt_builder.py`
- Test: `tests/test_stock_pipeline.py`

1. Write failing tests for valid queues, duplicate IDs, invalid queue fields, and deterministic prompt rendering.
2. Run `python -m pytest tests/test_stock_pipeline.py -q` and confirm missing-module failures.
3. Add formal schemas, validation errors, config loading, and strict placeholder rendering.
4. Re-run focused tests and confirm they pass.

### Task 2: SQLite registry and state transitions

**Files:**
- Create: `stock_pipeline/registry.py`
- Test: `tests/test_stock_pipeline.py`

1. Write failing tests for deterministic ID allocation, idempotent queue registration, duplicate/conflicting identity rejection, valid transitions, invalid transitions, performance fields, and force/resume decisions.
2. Confirm the tests fail for missing behavior.
3. Implement the minimal SQLite schema, explicit transition map, atomic allocation, upsert semantics, and update helpers.
4. Re-run focused tests.

### Task 3: Image generation and deterministic QC

**Files:**
- Create: `stock_pipeline/image_generator.py`
- Create: `stock_pipeline/image_qc.py`
- Test: `tests/test_stock_pipeline.py`

1. Write failing tests for original PNG preservation, 1:1/2K defaults, square/resolution/format checks, and corrupt-image results.
2. Confirm failures.
3. Wrap `main.generate_image_bytes` without background removal and implement Pillow-based QC with nullable semantic fields.
4. Re-run focused tests.

### Task 4: Structured META_V2 generation and validation

**Files:**
- Create: `stock_pipeline/metadata_generator.py`
- Create: `stock_pipeline/metadata_validator.py`
- Test: `tests/test_stock_pipeline.py`

1. Write failing tests for theme inclusion, JSON/fenced-JSON parsing, title length, keyword count, normalized duplicates, asset mismatch, category mismatch, and valid records.
2. Confirm failures.
3. Reuse the provider-specific calls from `main.py`, persist raw text, parse canonical JSON, and validate without silent truncation.
4. Re-run focused tests.

### Task 5: Filename mapping, Adobe CSV, and reproducible staging

**Files:**
- Create: `stock_pipeline/filenames.py`
- Create: `stock_pipeline/adobe_csv.py`
- Create: `stock_pipeline/staging.py`
- Test: `tests/test_stock_pipeline.py`

1. Write failing tests for filename length/uniqueness, deterministic CSV fields, missing/extra image detection, READY_TO_STAGE filtering, and reproducible staging.
2. Confirm failures.
3. Implement stable `AST000001.png` mapping, CSV construction from validated JSON only, copied-image verification, and batch manifests.
4. Re-run focused tests.

### Task 6: Orchestration, failure isolation, resume, and uploader integration

**Files:**
- Create: `stock_pipeline/pipeline.py`
- Create: `stock_pipeline/logging.py`
- Create: `run_stock_pipeline.py`
- Test: `tests/test_stock_pipeline.py`

1. Write failing tests proving completed stages are skipped, force flags rerun only requested stages, and one failed asset does not abort the batch.
2. Confirm failures.
3. Implement per-asset exception boundaries, timings, manifests/logs, conservative CLI defaults, staging, and optional invocation of the existing uploader with `illustrations`, `--mark-ai`, and `--save-work` but no final submission.
4. Re-run focused tests.

### Task 7: Performance-import placeholder and Work handoff

**Files:**
- Create: `tools/import_adobe_performance.py`
- Create: `queues/incoming/.gitkeep`
- Create: `work_io/topic_finder/incoming/.gitkeep`
- Create: `work_io/monthly_review/outgoing/.gitkeep`
- Create: `work_io/quarterly_calibration/outgoing/.gitkeep`
- Modify: `README.md`
- Test: `tests/test_stock_pipeline.py`

1. Write failing tests for performance CSV import validation and registry updates.
2. Confirm failures, implement the small importer, and re-run tests.
3. Document the queue contract, local/ChatGPT Work boundary, dry-run pilot, resume behavior, output layout, and legacy XHS commands.

### Task 8: Legacy regression and full verification

**Files:**
- Modify: `xhs_wallpaper_workflow.py`
- Modify: `requirements.txt`

1. Use the already-failing Windows-path test to fix `get_target_output_name` portably.
2. Run `uv run --with pytest --with-requirements requirements.txt python -m pytest -q`.
3. Run `node --test tests/*.mjs`.
4. Run `python run_stock_pipeline.py --help` and a no-network dry-run fixture.
5. Inspect `git diff --check`, `git status`, and the requirements checklist before committing.
