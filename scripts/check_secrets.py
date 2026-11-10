"""Keep public request digests out of secret findings, including replay JSONL."""

import hashlib
import json
import sys
import tempfile
from pathlib import Path

from detect_secrets.core.secrets_collection import SecretsCollection
from detect_secrets.settings import default_settings

from docket.llm.archive import load_archive
from docket.llm.exchange import PendingRequest


def remaining_findings(results: dict, pending: Path) -> tuple[dict, int]:
    public_digests = set()
    archive = pending.parent / "recordings/exchange.jsonl.gz"
    if archive.exists():
        for key in load_archive(archive):
            public_digests.add(hashlib.sha1(key.encode()).hexdigest())
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


def archived_findings(archive: Path) -> dict:
    entries = load_archive(archive)
    with tempfile.TemporaryDirectory() as directory, default_settings():
        secrets = SecretsCollection(root=directory)
        for key, entry in entries.items():
            for kind in ("request", "answer"):
                filename = f"{key}-{kind}.json"
                (Path(directory) / filename).write_bytes(getattr(entry, f"{kind}_json").encode())
                secrets.scan_file(filename)
        return {f"{archive}:{name}": findings for name, findings in secrets.json().items()}


def main():
    results = json.loads(Path(sys.argv[1]).read_text())["results"]
    results.update(archived_findings(Path("evals/recordings/exchange.jsonl.gz")))
    remaining, count = remaining_findings(results, Path("evals/pending"))
    print("detect-secrets:", remaining)
    if count:
        print(
            f"Verified public request SHA-256 digests: {count}; all other findings remain blocking"
        )
    raise SystemExit(bool(remaining))


if __name__ == "__main__":
    main()
