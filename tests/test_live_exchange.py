"""The live exchange file: the exact request sent for every live recording."""

import gzip
import json

import pytest

from docket.llm.archive import check_live_archive, load_live_archive, pack_live
from docket.llm.errors import UnreadableAnswer
from docket.llm.exchange import PendingRequest
from tests.test_anthropic_transport import FakeApi, choose, gateway, message


async def record_two(postgres_engine, tmp_path):
    live = gateway(FakeApi(message(), message("not json")), postgres_engine, tmp_path)
    await live.call(**choose("first synthetic document"))
    with pytest.raises(UnreadableAnswer):
        await live.call(**choose("second synthetic document"))
    return tmp_path / "pending", tmp_path / "anthropic.jsonl", tmp_path / "exchange.jsonl.gz"


async def test_record_mode_writes_each_request_and_packs_them(postgres_engine, tmp_path):
    pending, recordings, archive = await record_two(postgres_engine, tmp_path)
    saved = sorted(pending.glob("*.json"))
    assert len(saved) == 2
    for path in saved:
        request = PendingRequest.model_validate_json(path.read_text())
        request.verify_key()
        assert request.target_model == "claude-haiku-5-5"
    assert pack_live(pending, recordings, archive) == 2
    first = archive.read_bytes()
    assert check_live_archive(archive, recordings) == 2
    entries = load_live_archive(archive)
    assert {path.stem for path in saved} == set(entries)
    assert all(entries[path.stem].request_json == path.read_text() for path in saved)
    # Packing again, even without the working folder, gives the same bytes.
    for path in saved:
        path.unlink()
    assert pack_live(pending, recordings, archive) == 2
    assert archive.read_bytes() == first


async def test_missing_request_refuses_to_pack(postgres_engine, tmp_path):
    pending, recordings, archive = await record_two(postgres_engine, tmp_path)
    next(pending.glob("*.json")).unlink()
    with pytest.raises(ValueError, match="Missing request"):
        pack_live(pending, recordings, archive)
    assert not archive.exists()


async def test_edited_request_fails_its_digest(postgres_engine, tmp_path):
    pending, recordings, archive = await record_two(postgres_engine, tmp_path)
    path = next(pending.glob("*.json"))
    path.write_text(path.read_text().replace("synthetic document", "edited document"))
    with pytest.raises(Exception, match="digest"):
        pack_live(pending, recordings, archive)


async def test_check_finds_tampering_and_unarchived_recordings(postgres_engine, tmp_path):
    pending, recordings, archive = await record_two(postgres_engine, tmp_path)
    pack_live(pending, recordings, archive)
    rows = [json.loads(line) for line in gzip.open(archive, "rt")]
    rows[0]["request_json"] = rows[0]["request_json"].replace("document", "DOCUMENT")
    archive.write_bytes(gzip.compress(("\n".join(json.dumps(r) for r in rows) + "\n").encode()))
    with pytest.raises(ValueError, match="SHA-256"):
        check_live_archive(archive, recordings)
    pack_live(pending, recordings, tmp_path / "fresh.jsonl.gz")
    with recordings.open("a") as file:
        line = json.loads(recordings.read_text().splitlines()[0])
        line["key"] = "f" * 64
        file.write(json.dumps(line) + "\n")
    with pytest.raises(ValueError, match="Missing archive entry"):
        check_live_archive(tmp_path / "fresh.jsonl.gz", recordings)
