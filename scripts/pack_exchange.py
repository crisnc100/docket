import argparse
from pathlib import Path

from docket.llm.archive import check_archive, pack_exchange
from docket.llm.errors import ParseError


def main() -> None:
    parser = argparse.ArgumentParser(description="Pack or verify saved simulation evidence.")
    parser.add_argument("--check", action="store_true", help="Check the archive against recordings")
    args = parser.parse_args()
    archive = Path("evals/recordings/exchange.jsonl.gz")
    try:
        if not args.check:
            count = pack_exchange(Path("evals/pending"), Path("evals/answers"), archive)
            print(f"Packed {count} exchanges: {archive}")
        count = check_archive(archive, Path("evals/recordings/calls.jsonl"))
    except (OSError, ValueError, ParseError) as exc:
        parser.exit(1, f"FAIL: {exc}\n")
    print(f"PASS: {count} recordings match archived answers and provenance")


if __name__ == "__main__":
    main()
