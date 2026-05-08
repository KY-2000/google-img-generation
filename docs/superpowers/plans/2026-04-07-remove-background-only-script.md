# Remove Background Only Script Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a standalone script that scans every `output/<timestamp>/` run folder and background-removes only the original images that do not already have a matching rembg output.

**Architecture:** Keep the new behavior isolated in a dedicated `remove_background_only.py` script and reuse `remove_background_ffmpeg()` from `main.py`. Add a small helper for discovering missing rembg work and cover it with focused unit tests.

**Tech Stack:** Python standard library, existing `main.py`, `unittest`

---

### Task 1: Add failing test for scan-and-match behavior

**Files:**
- Modify: `tests/test_main.py`
- Test: `tests/test_main.py`

- [ ] **Step 1: Write the failing test**
- [ ] **Step 2: Run test to verify it fails**
- [ ] **Step 3: Implement minimal helper in `main.py`**
- [ ] **Step 4: Run test to verify it passes**

### Task 2: Add standalone CLI script

**Files:**
- Create: `remove_background_only.py`
- Modify: `main.py`
- Test: `tests/test_main.py`

- [ ] **Step 1: Create script entrypoint that scans `output/*`**
- [ ] **Step 2: Reuse `remove_background_ffmpeg()` for missing outputs only**
- [ ] **Step 3: Print per-file status and summary totals**
- [ ] **Step 4: Run focused tests**

### Task 3: Document usage

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add command example and behavior notes**
- [ ] **Step 2: Verify docs match current filename conventions**

### Task 4: Verification

**Files:**
- Modify: `tests/test_main.py` (if needed)

- [ ] **Step 1: Run targeted unit tests**
- [ ] **Step 2: Run `py_compile` on the new script and `main.py`**
- [ ] **Step 3: Report any remaining unrelated test-environment issues clearly**
