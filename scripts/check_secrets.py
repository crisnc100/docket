"""Keep public request digests out of secret findings, including replay JSONL."""

import hashlib
import json
import sys
from pathlib import Path

from docket.llm.exchange import PendingRequest


def remaining_findings(results: dict, pending: Path) -> tuple[dict, int]:
    public_digests = set()
    for path in pending.glob("*.json"):
        request = PendingRequest.model_validate_json(path.read_text())
        request.verify_key()
        if path.stem != request.key:
            raise ValueError("Request filename differs from its verified digest")
        public_digests.add(hashlib.sha1(request.key.encode()).hexdigest())
    remaining = {}
    public_count = 0
    for filename, findings in results.items():
        for finding in findings:
            if (
                finding["type"] == "Hex High Entropy String"
                and finding["hashed_secret"] in public_digests
            ):
                public_count += 1
            else:
                remaining.setdefault(filename, []).append(finding)
    return remaining, public_count


def main():
    results = json.loads(Path(sys.argv[1]).read_text())["results"]
    remaining, count = remaining_findings(results, Path("evals/pending"))
    print("detect-secrets:", remaining)
    if count:
        print(
            f"Verified public request SHA-256 digests: {count}; all other findings remain blocking"
        )
    raise SystemExit(bool(remaining))


if __name__ == "__main__":
    main()
