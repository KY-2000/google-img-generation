#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from stock_pipeline.candidate_conversion import convert_candidates, write_queue_atomic
from stock_pipeline.candidate_import import SIMILARITY_RISKS, STRATEGIES


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Convert Finder Candidate JSON to a production Topic Queue.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--topic-finder-version", default="TF_V1_1")
    parser.add_argument("--min-score", type=float, default=0)
    parser.add_argument("--include-risk", nargs="+", choices=sorted(SIMILARITY_RISKS), default=sorted(SIMILARITY_RISKS))
    parser.add_argument("--strategy", choices=sorted(STRATEGIES))
    parser.add_argument("--database", type=Path, default=Path("data/stock_pipeline.sqlite"))
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    source = args.input if args.input.is_absolute() else PROJECT_ROOT / args.input
    output = args.output if args.output.is_absolute() else PROJECT_ROOT / args.output
    database = args.database if args.database.is_absolute() else PROJECT_ROOT / args.database
    queue = convert_candidates(
        source, database, run_id=args.run_id, topic_finder_version=args.topic_finder_version,
        min_score=args.min_score, include_risk=args.include_risk, strategy=args.strategy,
    )
    write_queue_atomic(queue, output)
    print(f"Converted {len(queue['assets'])} candidates to {output} ({queue['run_id']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
