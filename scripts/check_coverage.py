#!/usr/bin/env python3
"""Enforce branch-only module floors from coverage.py JSON."""

import argparse
import json
from pathlib import Path
from typing import Any

FLOORS = {
    "docket/core/candidates.py": 95,
    "docket/core/scoring.py": 95,
    "docket/core/ledger.py": 95,
    "docket/core/storage.py": 90,
}
ROOT = Path(__file__).resolve().parents[1]


def check(report: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if report.get("meta", {}).get("branch_coverage") is not True:
        return ["FAIL: report was not collected with branch coverage enabled"]
    files = {str((ROOT / filename).resolve()): value for filename, value in report["files"].items()}
    for module, floor in FLOORS.items():
        data = files.get(str(ROOT / module))
        if data is None:
            failures.append(f"FAIL {module}: missing module in coverage report")
            continue
        summary = data["summary"]
        total = summary.get("num_branches", 0)
        covered = summary.get("covered_branches", 0)
        if not isinstance(total, int) or not isinstance(covered, int) or not 0 <= covered <= total:
            failures.append(f"FAIL {module}: invalid branch counters")
            continue
        if total == 0:
            failures.append(f"FAIL {module}: no measured branches")
            continue
        percentage = 100 * covered / total
        passed = covered * 100 >= total * floor
        message = (
            f"{'PASS' if passed else 'FAIL'} {module}: "
            f"{covered}/{total} branches = {percentage:.2f}% (required {floor}%)"
        )
        print(message)
        if not passed:
            failures.append(message)
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, nargs="?", default=Path(".cache/coverage.json"))
    args = parser.parse_args()
    try:
        failures = check(json.loads(args.report.read_text()))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"FAIL invalid coverage report: {exc}\n")
    for failure in failures:
        print(failure)
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
