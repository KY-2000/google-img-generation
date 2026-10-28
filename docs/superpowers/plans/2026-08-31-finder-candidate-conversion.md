# Finder Candidate Conversion Layer Implementation Plan

> **For Codex:** Apply this plan test-first on `codex/stock-icon-pipeline-v1`. Preserve the existing production Topic Queue contract and all unrelated dirty-worktree changes.

**Goal:** Add a strict, idempotent conversion boundary from research-oriented Finder Candidate arrays to production Topic Queue objects with permanent SQLite-backed AST allocation and lineage.

**Architecture:** A new candidate contract module validates and normalizes the top-level array independently of the production queue validator. The registry persists every imported research candidate and atomically reserves one permanent asset ID per `(strategy, candidate_id)`. A thin CLI filters candidates, maps selected records to the unchanged production queue shape, validates that queue with the existing validator, and only then atomically publishes the JSON file.

**Tech Stack:** Python 3 standard library (`argparse`, `json`, `sqlite3`, `pathlib`, `zoneinfo`), JSON Schema documents, `unittest`.

---

### Task 1: Candidate contract and normalization

**Files:**
- Create: `schemas/finder_candidates.schema.json`
- Create: `stock_pipeline/candidate_import.py`
- Create: `tests/test_candidate_import.py`

1. Write failing tests for valid arrays, top-level object rejection, missing/unknown fields, enum failures, duplicate `(strategy, candidate_id)` identities, normalization, and research-field preservation.
2. Run `python -m unittest tests.test_candidate_import -v` and confirm the new tests fail because the module/schema do not exist.
3. Add the strict JSON Schema and runtime validator/loader that mirrors it without accepting production queues.
4. Run the focused tests and confirm they pass.

### Task 2: SQLite candidate registry and permanent allocation

**Files:**
- Modify: `stock_pipeline/registry.py`
- Modify: `tests/test_candidate_import.py`

1. Write failing tests for candidate persistence, research lineage, repeat-import idempotency, repeated conversion stability, cross-file identity stability, unique sequential allocation, and allocation after existing production assets.
2. Add `candidate_topics` with `(strategy, candidate_id)` primary identity and the requested research/production fields.
3. Make allocator access transaction-safe and add an atomic candidate-to-asset reservation method that never derives IDs from candidate text.
4. Run focused registry and existing registry tests.

### Task 3: Candidate-to-queue converter CLI

**Files:**
- Create: `stock_pipeline/candidate_conversion.py`
- Create: `tools/convert_candidates_to_queue.py`
- Create: `tests/test_convert_candidates_to_queue.py`

1. Write failing tests for exact mapping, generated timestamps/run IDs, explicit run IDs, all default candidates, min-score/risk/strategy filters, no selected candidates, strict queue validation, CLI success, and no partial output on any failure.
2. Implement deterministic run-ID derivation, explicit filters, import-before-selection, permanent allocation, strict queue mapping, existing `validate_topic_queue` validation, and atomic output replacement.
3. Ensure the converter writes only the unchanged production shape and `run_stock_pipeline.py --queue` still rejects candidate arrays.
4. Run focused converter tests and the existing command tests.

### Task 4: Documentation and end-to-end verification

**Files:**
- Modify: `README.md`
- Modify: `docs/STOCK_ICON_PIPELINE_V1.md`
- Create: `queues/finder/incoming/finder_candidates.regional_commercial.example.json`
- Create: `queues/production/.gitkeep`

1. Document the two contracts, ownership boundary, import/convert/run commands, filters, lineage, timestamps, and idempotency.
2. Add a representative regional-commercial candidate file and convert it using a temporary database/output during verification.
3. Run all Python tests and relevant Node tests, inspect `git diff`, and confirm no image-generation, metadata-generation, uploader, filename, or staging behavior was modified for this feature.
4. Report files changed, schema and mapping behavior, tests, and a converted regional queue example.
