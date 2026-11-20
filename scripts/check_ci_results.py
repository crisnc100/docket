#!/usr/bin/env python3
"""Fail unless every CI test job left its results and together they ran every collected test.

coverage combine skips a missing file whenever another one matches its glob, so without
this check a lost job would only shrink the coverage and the test count, not fail CI.
"""

import argparse
import re
import subprocess
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path

RESULT_FILES = ("coverage", "junit.xml")


def collected_count() -> int:
    output = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "--collect-only", "-q", "-p", "no:cacheprovider"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    match = re.search(r"^(\d+) tests? collected", output, re.MULTILINE)
    if match is None:
        raise SystemExit("FAIL: pytest did not report a collected count")
    return int(match.group(1))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path, help="folder holding one subfolder per job")
    parser.add_argument("jobs", nargs="+", help="every job folder that must be present")
    args = parser.parse_args()
    missing = [
        path
        for job in args.jobs
        for path in (args.results / job / name for name in RESULT_FILES)
        if not path.is_file() or path.stat().st_size == 0
    ]
    for path in missing:
        print(f"FAIL: {path} is missing")
    if missing:
        return 1
    ran = sum(
        len(ElementTree.parse(args.results / job / "junit.xml").getroot().findall(".//testcase"))
        for job in args.jobs
    )
    expected = collected_count()
    if ran != expected:
        print(f"FAIL: the jobs ran {ran} tests, but pytest collects {expected}")
        return 1
    print(f"PASS: the jobs ran all {ran} collected tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
