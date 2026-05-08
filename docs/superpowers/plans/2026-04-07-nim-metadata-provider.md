# NVIDIA NIM Metadata Provider Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add hosted NVIDIA NIM as a metadata provider so the repo can keep Google for image generation while using a NIM-hosted model such as `moonshotai/kimi-k2.5` for tagging.

**Architecture:** Extend the existing provider pattern in `main.py` with a new `nim` branch that mirrors the current OpenRouter HTTP flow. Keep the integration minimal: env var for `NIM_API_KEY`, hosted endpoint constant, provider-specific client builder, request payload builder, and metadata generation function.

**Tech Stack:** Python standard library, existing `main.py`, `unittest`

---

### Task 1: Add failing tests for provider/model wiring

**Files:**
- Modify: `tests/test_main.py`
- Test: `tests/test_main.py`

- [ ] **Step 1: Write failing tests for `nim` provider defaults and run-path wiring**
- [ ] **Step 2: Run tests to verify they fail**
- [ ] **Step 3: Implement minimal provider plumbing**
- [ ] **Step 4: Run tests to verify they pass**

### Task 2: Add failing test for hosted NIM payload/request

**Files:**
- Modify: `tests/test_main.py`
- Test: `tests/test_main.py`

- [ ] **Step 1: Write failing test for hosted NIM payload shape**
- [ ] **Step 2: Run test to verify it fails**
- [ ] **Step 3: Implement minimal request builder/caller**
- [ ] **Step 4: Run test to verify it passes**

### Task 3: Update docs

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add `NIM_API_KEY` setup**
- [ ] **Step 2: Add example command using `--metadata-provider nim --metadata-model kimik25`**

### Task 4: Verification

**Files:**
- Modify: `tests/test_main.py` (if needed)

- [ ] **Step 1: Run targeted unit tests**
- [ ] **Step 2: Run `py_compile` on `main.py`**
- [ ] **Step 3: Report any remaining unrelated test-environment issues explicitly**
