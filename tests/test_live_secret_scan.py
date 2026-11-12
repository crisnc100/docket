"""The secret check treats live request digests as public and scans live request text."""

import hashlib
import json
import shutil
import subprocess

from docket.llm.archive import pack_live
from scripts.check_secrets import live_archived_findings, remaining_findings
from tests.test_anthropic_transport import FakeApi, choose, gateway, message


async def test_live_digests_are_public_but_other_findings_block(postgres_engine, tmp_path):
    await gateway(FakeApi(message()), postgres_engine, tmp_path).call(**choose())
    archive = tmp_path / "recordings/anthropic-exchange.jsonl.gz"
    pack_live(tmp_path / "pending", tmp_path / "anthropic.jsonl", archive)
    shutil.rmtree(tmp_path / "pending")
    (tmp_path / "pending").mkdir()
    key = json.loads((tmp_path / "anthropic.jsonl").read_text())["key"]
    unknown = hashlib.sha256(b"test-only nonpublic value that must remain blocked").hexdigest()
    fixture = tmp_path / "fixture.jsonl"
    fixture.write_text(json.dumps({"key": key, "output": {"password": unknown}}) + "\n")
    scanned = subprocess.run(
        ["detect-secrets", "scan", "--all-files", str(fixture)],
        check=True,
        capture_output=True,
        text=True,
    )
    remaining, count = remaining_findings(
        json.loads(scanned.stdout)["results"], tmp_path / "pending"
    )
    assert count >= 1
    assert any(
        finding["hashed_secret"] == hashlib.sha1(unknown.encode()).hexdigest()
        for findings in remaining.values()
        for finding in findings
    )
    assert all(
        finding["hashed_secret"] != hashlib.sha1(key.encode()).hexdigest()
        for findings in remaining.values()
        for finding in findings
    )


async def test_live_request_text_is_scanned(postgres_engine, tmp_path):
    # Assembled at run time so the source holds no key-shaped string.
    planted = "AKIA" + "Q" * 16
    await gateway(FakeApi(message()), postgres_engine, tmp_path).call(**choose(planted))
    archive = tmp_path / "anthropic-exchange.jsonl.gz"
    pack_live(tmp_path / "pending", tmp_path / "anthropic.jsonl", archive)
    findings = live_archived_findings(archive)
    assert findings and all(name.startswith(str(archive)) for name in findings)
