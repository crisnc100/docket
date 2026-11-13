import argparse
from pathlib import Path

from docket.llm.archive import check_archive, check_live_archive, pack_exchange, pack_live
from docket.llm.errors import ParseError


def main() -> None:
    parser = argparse.ArgumentParser(description="Pack or verify saved simulation evidence.")
    parser.add_argument("--check", action="store_true", help="Check the archive against recordings")
    parser.add_argument(
        "--live", action="store_true", help="Pack the live requests for the live recordings"
    )
    args = parser.parse_args()
    archive = Path("evals/history/simulated/exchange.jsonl.gz")
    live_archive = Path("evals/recordings/anthropic-exchange.jsonl.gz")
    live_recordings = Path("evals/recordings/anthropic.jsonl")
    try:
        if args.live:
            count = pack_live(Path("evals/pending"), live_recordings, live_archive)
            print(f"Packed {count} live requests: {live_archive}")
        elif not args.check:
            count = pack_exchange(Path("evals/pending"), Path("evals/answers"), archive)
            print(f"Packed {count} exchanges: {archive}")
        count = check_archive(archive, archive.with_name("calls.jsonl"))
        print(f"PASS: {count} recordings match archived answers and provenance")
        if live_archive.exists() or live_recordings.exists():
            count = check_live_archive(live_archive, live_recordings)
            print(f"PASS: {count} live recordings match their archived requests")
    except (OSError, ValueError, ParseError) as exc:
        parser.exit(1, f"FAIL: {exc}\n")


if __name__ == "__main__":
    main()
