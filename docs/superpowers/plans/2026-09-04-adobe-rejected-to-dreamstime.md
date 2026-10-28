# Adobe Rejected to Dreamstime Automation Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a conservative, resumable workflow that scans recent Adobe Stock rejections, matches each original filename to local image/metadata, generates a populated Dreamstime legacy XLS import file, uploads the images, and prepares each Dreamstime commercial submission with the requested licensing fields.

**Architecture:** A Python orchestration/data module owns cutoff calculation, normalized rejection records, local asset resolution, Dreamstime row construction, manifests, and subprocess coordination. A Playwright Node script owns both read-only Adobe scanning and Dreamstime browser interaction over the existing CDP session. A small PowerShell Excel-COM helper copies and fills the real `.xls` template while preserving the Image Legend sheet and formatting. Browser submission is conservative: upload and form preparation are supported, but the final `Submit commercial` click requires an explicit `--submit-commercial` flag.

**Tech Stack:** Python 3 standard library, SQLite/JSON asset metadata, Node.js Playwright, PowerShell Excel COM, legacy `.xls`, `unittest`, Node test runner.

---

## Chunk 1: Deterministic data preparation

### Task 1: Rejection records, cutoff, and local asset lookup

**Files:**
- Create: `stock_pipeline/dreamstime_rejections.py`
- Create: `tests/test_dreamstime_rejections.py`

- [ ] Write failing tests for parsing absolute/relative Adobe submitted dates, inclusive 14-day cutoff, rejection-record validation, AST filename normalization, SQLite lookup, filesystem fallback, missing/ambiguous assets, and metadata keyword loading.
- [ ] Run `python -m unittest tests.test_dreamstime_rejections -v` and confirm missing-module failures.
- [ ] Implement normalized records and deterministic lookup without browser or spreadsheet dependencies.
- [ ] Re-run focused tests and confirm green.

### Task 2: Dreamstime category selection and row mapping

**Files:**
- Modify: `stock_pipeline/dreamstime_rejections.py`
- Modify: `tests/test_dreamstime_rejections.py`

- [ ] Write failing tests for legend parsing, deterministic title/keyword category scoring, three distinct category codes where available, description construction, keyword serialization, and all requested fixed license values.
- [ ] Implement category scoring against the template-derived legend and exact 15-column row output.
- [ ] Verify focused tests.

## Chunk 2: Spreadsheet and browser adapters

### Task 3: Legacy XLS template writer

**Files:**
- Create: `tools/write_dreamstime_xls.ps1`
- Create: `tests/test_write_dreamstime_xls.py`

- [ ] Write a failing integration test using a copied template and JSON rows when Excel is available.
- [ ] Implement hidden Excel COM automation that preserves all sheets/styles, clears example rows only on `Enter data here`, writes rows from row 2, formats IDs as text, saves a new `.xls`, and always closes Excel.
- [ ] Verify workbook headers, legends, row values, and absence of formulas/errors.

### Task 4: Adobe scanner and Dreamstime preparer

**Files:**
- Create: `tools/adobe_rejected_dreamstime_playwright.mjs`
- Create: `tests/adobe_rejected_dreamstime_playwright.test.mjs`

- [ ] Write failing unit tests for CLI validation, relative-date parsing, cutoff eligibility, Adobe detail extraction, CDP upload chunking under the 50 MB transport limit, keyword clearing/addition plans, and safe default no-submit behavior.
- [ ] Implement Adobe rejected-page pagination/scroll scanning with screenshots and JSON output.
- [ ] Implement Dreamstime upload using file chooser chunking and per-image form preparation: retain AI Contributor, leave auto categories, replace keywords, enable W-EL/P-EL/SR-EL, select custom SR price 2, choose Commercial, leave RF-LL and exclusive unchecked.
- [ ] Require `--submit-commercial` before clicking final submission; log and screenshot every image.
- [ ] Run Node tests.

## Chunk 3: End-to-end command and documentation

### Task 5: Python CLI orchestrator

**Files:**
- Create: `tools/process_adobe_rejections_to_dreamstime.py`
- Modify: `README.md`
- Modify: `docs/STOCK_ICON_PIPELINE_V1.md`
- Test: `tests/test_dreamstime_rejections.py`

- [ ] Write failing CLI tests for default 14 days, explicit `--since`, CDP normalization, scan-only, spreadsheet-only, prepare-upload, explicit submit, output manifest paths, and subprocess failures.
- [ ] Implement one command that scans Adobe, resolves local assets, writes JSON/CSV audit artifacts and the populated XLS, then optionally prepares Dreamstime.
- [ ] Document exact safe and submit commands, browser/login prerequisites, output files, resume behavior, and final-submit warning.
- [ ] Run focused suites, all Python tests, relevant Node tests, compilation, and `git diff --check`.
