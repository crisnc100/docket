import hashlib
import json
import subprocess

from docket.llm.errors import ExportPending
from docket.llm.exchange import Exchange
from docket.llm.gateway import Gateway, Mode
from docket.llm.replay import ReplayStore
from docket.prompts import choose_request
from scripts.check_secrets import remaining_findings


async def test_real_scanner_blocks_unknown_secrets_but_accepts_verified_public_digest(tmp_path):
    gateway = Gateway(
        mode=Mode.EXPORT,
        model="test-target",
        store=ReplayStore(tmp_path / "unused"),
        exchange=Exchange(tmp_path / "pending", tmp_path / "answers"),
    )
    try:
        await gateway.call(**choose_request("synthetic", ["UNIT-0001"]), source="synthetic_corpus")
    except ExportPending:
        pass
    paths = list((tmp_path / "pending").glob("*.json"))
    assert len(paths) == 1
    key = paths[0].stem
    unknown = hashlib.sha256(b"test-only nonpublic value that must remain blocked").hexdigest()
    # A replay row has a public key and may still contain another secret on that line.
    fixture = tmp_path / "fixture.jsonl"
    fixture.write_text(json.dumps({"key": key, "output": {"password": unknown}}) + "\n")
    scanned = subprocess.run(
        ["detect-secrets", "scan", "--all-files", str(fixture)],
        check=True,
        capture_output=True,
        text=True,
    )
    results = json.loads(scanned.stdout)["results"]
    assert results
    remaining, count = remaining_findings(results, tmp_path / "pending")
    assert count >= 1
    findings = [finding for values in remaining.values() for finding in values]
    assert findings
    assert any(
        finding["hashed_secret"] == hashlib.sha1(unknown.encode()).hexdigest()
        for finding in findings
    )
    fixture.write_text(json.dumps({"key": key, "output": {"record_id": "UNIT-0001"}}) + "\n")
    scanned = subprocess.run(
        ["detect-secrets", "scan", "--all-files", str(fixture)],
        check=True,
        capture_output=True,
        text=True,
    )
    remaining, count = remaining_findings(
        json.loads(scanned.stdout)["results"], tmp_path / "pending"
    )
    assert remaining == {} and count >= 1
