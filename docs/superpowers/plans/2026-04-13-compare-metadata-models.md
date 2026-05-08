# Compare Metadata Models Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a standalone script that compares exactly two metadata models across all original images in one existing run folder, without regenerating images.

**Architecture:** Keep the comparison flow isolated in a dedicated `compare_metadata_models.py` script and reuse the existing metadata-generation functions from `main.py`. Add small helpers for parsing model specs and discovering original images in a run folder, then write comparison artifacts into a timestamped subfolder under `comparisons/`.

**Tech Stack:** Python standard library, existing `main.py`, `unittest`

---

### Task 1: Add failing tests for run-folder discovery and model-spec parsing

**Files:**
- Modify: `tests/test_main.py`
- Test: `tests/test_main.py`

- [ ] **Step 1: Write the failing tests**
- [ ] **Step 2: Run tests to verify they fail**
- [ ] **Step 3: Implement minimal helpers in `main.py`**
- [ ] **Step 4: Run tests to verify they pass**

### Task 2: Add standalone comparison script

**Files:**
- Create: `compare_metadata_models.py`
- Modify: `main.py`
- Test: `tests/test_main.py`

- [ ] **Step 1: Add CLI that accepts one run folder and two model specs**
- [ ] **Step 2: Reuse existing metadata generation against saved `img-<n>.png` files**
- [ ] **Step 3: Write side-by-side output files into `comparisons/<timestamp>/`**
- [ ] **Step 4: Run focused tests**

### Task 3: Update docs

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add usage example for model comparison**
- [ ] **Step 2: Document output location and file layout**

### Task 4: Verification

**Files:**
- Modify: `tests/test_main.py` (if needed)

- [ ] **Step 1: Run targeted unit tests**
- [ ] **Step 2: Run `py_compile` on `main.py` and `compare_metadata_models.py`**
- [ ] **Step 3: Report any remaining unrelated test-environment issues explicitly**
