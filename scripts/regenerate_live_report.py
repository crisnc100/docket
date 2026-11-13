"""Regenerate a live report from the recordings, keeping its record runs' evidence.

    uv run python scripts/regenerate_live_report.py --seed 7 RUN_REPORT [RUN_REPORT ...] \
        --sources "git <sha>:<path>" ...

Each RUN_REPORT is the JSON report a paid `docket eval --mode record` run wrote, in
the order the runs happened. Replay rebuilds every figure with no model call; the
runs' budgets and costs are carried forward so the report still shows they were paid.
"""

import argparse
import asyncio
import json
from decimal import Decimal
from pathlib import Path

from docket.evaluation import evaluate, with_record_runs, write_report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument(
        "--sources", nargs="+", help="Where each run report is kept, such as a git commit and path"
    )
    args = parser.parse_args()
    runs = [json.loads(path.read_text()) for path in args.runs]
    replayed = asyncio.run(evaluate(args.seed, "replay", Decimal(0), date=runs[-1]["date"]))
    sources = args.sources or [path.name for path in args.runs]
    report = with_record_runs(replayed, runs, sources)
    print(f"{report['status']}: {write_report(report)}")


if __name__ == "__main__":
    main()
