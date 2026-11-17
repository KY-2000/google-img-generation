"""Local skip snapshot; neither production nor Adobe success evidence."""
import json
from hashlib import sha256
from pathlib import Path

TEST_QUEUE = "queues/incoming/commercial_background_queue_2026W44_r02.json"
BASELINE = Path("work_io/topic_finder/manager_baseline.json")


def load_baseline(root: Path) -> set[str]:
    path = root / BASELINE
    if not path.exists():
        return set()
    files = json.loads(path.read_text(encoding="utf-8"))["files"]
    if not isinstance(files, dict) or any(not isinstance(v, str) or len(v) != 64 for v in files.values()):
        raise ValueError("Invalid manager baseline snapshot")
    return set(files.values())


def snapshot_baseline(root: Path) -> int:
    incoming = root / "queues/incoming"
    if not (root / TEST_QUEUE).is_file():
        raise ValueError(f"Test queue not found: {TEST_QUEUE}")
    files = {
        p.relative_to(root).as_posix(): sha256(p.read_bytes()).hexdigest()
        for p in sorted(incoming.rglob("*.json"))
        if p.relative_to(root).as_posix() != TEST_QUEUE
        and p.resolve().is_relative_to(incoming.resolve())
    }
    path = root / BASELINE
    path.parent.mkdir(parents=True, exist_ok=True)
    # Never silently absorb a later batch into the completed baseline.
    with path.open("x", encoding="utf-8") as stream:
        json.dump({"files": files, "test_queue": TEST_QUEUE}, stream, indent=2)
        stream.write("\n")
    return len(files)
