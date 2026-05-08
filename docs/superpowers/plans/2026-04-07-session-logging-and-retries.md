# Session Logging And Retries Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add persistent terminal/file logging for each run and image session, including timings, retries, wait time, and failure details, while automatically retrying transient `429 RESOURCE_EXHAUSTED` API failures.

**Architecture:** Keep the existing single-file structure in `main.py` and add focused helper functions for retry classification, retry execution, and structured log persistence. Extend `run()` to collect per-run and per-session telemetry without changing its external CLI contract.

**Tech Stack:** Python standard library, `unittest`, existing `main.py`

---

## Chunk 1: Logging Helpers

### Task 1: Add failing tests for persisted run/session logs

**Files:**
- Modify: `tests/test_main.py`
- Test: `tests/test_main.py`

- [ ] **Step 1: Write the failing test**
- [ ] **Step 2: Run test to verify it fails**
- [ ] **Step 3: Implement minimal logging helpers in `main.py`**
- [ ] **Step 4: Run test to verify it passes**

### Task 2: Persist human-readable and JSON logs

**Files:**
- Modify: `main.py`
- Test: `tests/test_main.py`

- [ ] **Step 1: Add helper(s) to write `session_log.txt` and `session_log.json`**
- [ ] **Step 2: Emit run/session start and finish records**
- [ ] **Step 3: Re-run focused tests**

## Chunk 2: Retry Handling

### Task 3: Add failing tests for retryable 429 handling

**Files:**
- Modify: `tests/test_main.py`
- Test: `tests/test_main.py`

- [ ] **Step 1: Write the failing test for retry classification/execution**
- [ ] **Step 2: Run test to verify it fails**
- [ ] **Step 3: Implement retry helper(s) in `main.py`**
- [ ] **Step 4: Run test to verify it passes**

### Task 4: Wire retries into generation flow

**Files:**
- Modify: `main.py`
- Test: `tests/test_main.py`

- [ ] **Step 1: Wrap image generation and Google metadata generation in retry helper**
- [ ] **Step 2: Log attempt durations, wait time, and final outcome**
- [ ] **Step 3: Re-run focused tests**

## Chunk 3: Verification

### Task 5: Run regression tests

**Files:**
- Modify: `tests/test_main.py` (if needed)

- [ ] **Step 1: Run targeted `unittest` coverage for new behavior**
- [ ] **Step 2: Run full `tests/test_main.py`**
- [ ] **Step 3: Confirm no regressions before completion**
