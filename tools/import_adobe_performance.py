#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from stock_pipeline.performance import import_performance_csv
from stock_pipeline.registry import Registry


def main() -> int:
    parser = argparse.ArgumentParser(description="Import an Adobe performance snapshot into the stock registry.")
    parser.add_argument("performance_csv", type=Path)
    parser.add_argument("--database", type=Path, default=PROJECT_ROOT / "data/stock_pipeline.sqlite")
    args = parser.parse_args()
    with Registry(args.database) as registry:
        count = import_performance_csv(registry, args.performance_csv)
    print(f"Imported {count} performance rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

