"""Local runtime boundaries: safe Git sync, single instance lock and log redaction."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener


def redactor(root: Path):
    secrets = {v for k, v in os.environ.items() if re.search(r"KEY|TOKEN|SECRET|PASS|COOKIE|AUTHORIZATION", k, re.I)}
    dotenv = root / ".env"
    if dotenv.is_file():
        for line in dotenv.read_text(encoding="utf-8-sig").splitlines():
            key, sep, value = line.partition("=")
            if sep and re.search(r"KEY|TOKEN|SECRET|PASS|COOKIE|AUTHORIZATION", key, re.I):
                secrets.add(value.strip().strip("\"'"))

    def redact(message):
        value = str(message)
        for secret in sorted(secrets, key=len, reverse=True):
            if len(secret) >= 4:
                value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"(?i)(https?://)[^\s/@]+@", r"\1[REDACTED]@", value)
        value = re.sub(r"(?i)(bearer\s+)[^\s,;\"']+", r"\1[REDACTED]", value)
        return re.sub(
            r"""(?i)(['"]?(?:[a-z0-9_-]*(?:api[_-]?key|token|password|secret|cookie)|authorization)['"]?\s*[:=]\s*)("[^"]*"|'[^']*'|[^\s,;}]+)""",
            r"\1[REDACTED]", value,
        )
    return redact


def recent_error_log(root: Path, current_run: Path | None) -> str:
    reports = sorted((root / "output" / "stock_manager" / "runs").glob("*/report.json"),
                     key=lambda path: path.stat().st_mtime_ns, reverse=True)
    if current_run:
        reports.insert(0, current_run / "report.json")
    for path in dict.fromkeys(reports):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(report, dict) or report.get("status") not in {"FAILED", "BLOCKED", "UPLOAD_INCOMPLETE"}:
            continue
        lines = [
            "Please troubleshoot this error from Adobe Stock Batch Manager.",
            f"Run {path.parent.name} | {report['status']} | Commit {report.get('git_commit') or 'unavailable'}",
            f"Error: {report.get('last_error') or 'See activity below.'}",
        ]
        try:
            with path.with_name("activity.log").open(encoding="utf-8", errors="replace") as stream:
                tail = "".join(deque(stream, maxlen=30))[-6000:].strip()
        except OSError:
            tail = ""
        if tail:
            lines.extend(("", "Recent activity", tail))
        return redactor(root)("\n".join(lines))
    return ""


def git_sync(root: Path, run=subprocess.run, *, incoming_changes: dict | None = None,
             commit_state: dict | None = None) -> tuple[str, str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"}
    env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"

    def git(*args):
        result = run(["git", *args], cwd=root, env=env, capture_output=True, text=True,
                     encoding="utf-8", errors="replace", timeout=90)
        if result.returncode:
            # Remote command errors can include credential-bearing URLs.
            raise RuntimeError(f"Git {args[0]} failed (exit {result.returncode}); check GitHub access or diverging branches")
        return result.stdout.strip()

    if Path(git("rev-parse", "--show-toplevel")).resolve() != root.resolve():
        raise RuntimeError("Open the google-img-generation repository root")
    if git("status", "--porcelain", "--untracked-files=normal"):
        raise RuntimeError("Git worktree is dirty. Commit or move local changes/untracked files before Start; nothing was reset")
    if git("branch", "--show-current") != "main":
        raise RuntimeError("Switch to the local main branch before Start")
    remote = git("remote", "get-url", "origin")
    if not re.fullmatch(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)KY-2000/google-img-generation(?:\.git)?/?", remote, re.I):
        raise RuntimeError("origin is not github.com/KY-2000/google-img-generation")
    previous = git("rev-parse", "HEAD") if incoming_changes is not None else None
    git("fetch", "--no-tags", "origin", "refs/heads/main:refs/remotes/origin/main")
    upstream = git("rev-parse", "origin/main")
    if git("status", "--porcelain", "--untracked-files=normal") or git("branch", "--show-current") != "main":
        raise RuntimeError("Git worktree or branch changed during fetch; start again")
    # A clean local main may already contain upstream plus a local commit.
    # A divergent branch fails; merge never creates a merge commit.
    git("merge", "--ff-only", upstream)
    head = git("rev-parse", "HEAD")
    git("merge-base", "--is-ancestor", upstream, head)
    if git("status", "--porcelain", "--untracked-files=normal") or git("branch", "--show-current") != "main":
        raise RuntimeError("Git worktree or branch changed during sync; start again")
    if incoming_changes is not None and previous != head:
        changes = git("diff", "--name-status", "-z", "--no-renames", previous, head, "--", "queues/incoming")
        fields = changes.rstrip("\0").split("\0") if changes else []
        for kind, name in zip(fields[::2], fields[1::2]):
            if kind in {"A", "M", "T"} and name.casefold().endswith(".json"):
                incoming_changes[name] = "New" if kind == "A" else "Updated"
    if commit_state is not None:
        commit_state["on_origin_main"] = head == upstream
    return head, git("show", "-s", "--format=%cI", head)


def verify_reviewed_checkout(root: Path, commit: str) -> None:
    result = subprocess.run(["git", "status", "--porcelain=v2", "--branch", "--untracked-files=normal"],
                            cwd=root, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=30)
    lines = result.stdout.splitlines()
    if (result.returncode or f"# branch.oid {commit}" not in lines or "# branch.head main" not in lines
            or any(not line.startswith("# ") for line in lines)):
        raise RuntimeError("Git checkout changed since preview. Close it and check queues again.")


def validate_cdp(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "http" or parts.hostname not in {"localhost", "127.0.0.1", "::1"} or (
        parts.port is None or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/")
    ):
        raise ValueError("CDP must be a local HTTP endpoint such as http://127.0.0.1:9222")
    return url.rstrip("/")


def cdp_connected(url: str) -> bool:
    try:
        with build_opener(ProxyHandler({})).open(validate_cdp(url) + "/json/version", timeout=2) as response:
            version = json.load(response)
            return (response.status == 200 and isinstance(version, dict)
                    and bool(version.get("webSocketDebuggerUrl")))
    except (OSError, ValueError):
        return False


def ensure_edge_cdp(root: Path, url: str, profile: str | None, *, log, should_stop) -> bool:
    url = validate_cdp(url)
    if should_stop():
        return False
    if cdp_connected(url):
        log("Connected to the existing Edge session.", browser="Connected")
        return True
    if os.name != "nt":
        raise RuntimeError(f"Edge CDP is unavailable at {url}. Automatic startup requires Windows.")

    executable = shutil.which("msedge.exe")
    if not executable:
        for key in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
            folder = os.environ.get(key)
            if not folder:
                continue
            candidate = Path(folder) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if candidate.is_file():
                executable = str(candidate)
                break
    if not executable:
        raise RuntimeError("Microsoft Edge executable was not found. Launch the automation profile manually.")

    directory = Path(profile).expanduser() if profile else Path.home() / ".edge-xhs-adobe-automation"
    directory = (root / directory).resolve()
    port = urlsplit(url).port
    log(f"Starting Edge for Adobe upload on port {port} with profile {directory}", browser="Starting Edge")
    subprocess.Popen(
        [executable, f"--remote-debugging-port={port}", f"--user-data-dir={directory}",
         "--no-first-run", "--new-window", "about:blank"],
        cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if should_stop():
            return False
        if cdp_connected(url):
            log("Edge started and is ready for Adobe upload.", browser="Connected")
            return True
        time.sleep(0.4)
    log(f"Edge did not open its CDP endpoint at {url}.", browser="Disconnected")
    raise RuntimeError(f"Edge CDP did not become ready at {url}. If the automation profile is already open "
                       "without remote debugging, close that window and retry.")


@contextmanager
def manager_lock(path: Path):
    """OS file lock releases on crash; the lock file is never deleted."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Another Batch Manager is running") from exc
        yield
