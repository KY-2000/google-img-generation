"""Local runtime boundaries: safe Git sync, single instance lock and log redaction."""
from __future__ import annotations

import os
import re
import subprocess
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


def git_sync(root: Path, run=subprocess.run) -> tuple[str, str]:
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
    return head, git("show", "-s", "--format=%cI", head)


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
            return response.status == 200
    except (OSError, ValueError):
        return False


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
