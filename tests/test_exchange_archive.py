import gzip
import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

from docket.llm.archive import check_archive, load_archive, pack_exchange
from docket.llm.errors import ParseError
from docket.llm.exchange import Exchange, PendingRequest
from docket.llm.replay import ReplayStore, replay_key
from scripts.check_secrets import archived_findings, remaining_findings
from scripts.pack_exchange import main


@pytest.fixture
def exchange_files(tmp_path):
    root = tmp_path / "evals"
    exchange = Exchange(root / "pending", root / "answers")
    exchange.answers.mkdir(parents=True)
    store = ReplayStore(root / "recordings/calls.jsonl")
    for text in ("synthetic café\r\n", "another synthetic request"):
        schema = {
            "type": "object",
            "properties": {"record_id": {"type": "string", "enum": ["UNIT-0001", "none"]}},
            "required": ["record_id"],
            "additionalProperties": False,
        }
        inputs = {"text": text}
        key = replay_key(
            "choose", "v1", "test-target", {"inputs": inputs, "prompt": text, "schema": schema}
        )
        request = PendingRequest(
            key=key,
            activity="choose",
            prompt_version="v1",
            target_model="test-target",
            system_prompt="",
            messages=[{"role": "user", "content": text}],
            response_schema=schema,
            inputs=inputs,
            source="synthetic_corpus",
        )
        exchange.export(request)
        request_path = exchange.pending / f"{key}.json"
        request_path.write_bytes(request_path.read_bytes().replace(b"\n", b"\r\n"))
        answer = {
            "output": {"record_id": "UNIT-0001"},
            "provenance": {"simulated_by": "test-simulator", "standing_in_for": "test-target"},
        }
        (exchange.answers / f"{key}.json").write_bytes(
            (json.dumps(answer, indent=4, ensure_ascii=False) + "\r\n").encode()
        )
    exchange.import_answers(sorted(path.stem for path in exchange.pending.glob("*.json")), store)
    archive = root / "recordings/exchange.jsonl.gz"
    return exchange, store, archive


def test_archive_keeps_exact_bytes_and_is_sorted_and_deterministic(exchange_files):
    exchange, store, archive = exchange_files
    assert pack_exchange(exchange.pending, exchange.answers, archive) == 2
    original = archive.read_bytes()
    assert original[4:8] == b"\x00" * 4
    assert original[3] == 0
    entries = load_archive(archive)
    assert list(entries) == sorted(path.stem for path in exchange.pending.glob("*.json"))
    for key, entry in entries.items():
        for name, directory in (("request", exchange.pending), ("answer", exchange.answers)):
            raw = (directory / f"{key}.json").read_bytes()
            assert getattr(entry, f"{name}_json").encode() == raw
            assert getattr(entry, f"{name}_sha256") == hashlib.sha256(raw).hexdigest()
        assert entry.provenance == entry.answer().provenance
    for path in exchange.pending.glob("*.json"):
        path.touch()
    other = archive.with_name("different-name.gz")
    pack_exchange(exchange.pending, exchange.answers, other)
    assert other.read_bytes() == original
    pack_exchange(exchange.pending, exchange.answers, archive)
    assert archive.read_bytes() == original
    shutil.rmtree(exchange.pending)
    shutil.rmtree(exchange.answers)
    assert check_archive(archive, store.path) == 2


@pytest.mark.parametrize("field", ["request_json", "answer_json"])
def test_archive_rejects_changed_raw_bytes(exchange_files, field):
    exchange, _, archive = exchange_files
    pack_exchange(exchange.pending, exchange.answers, archive)
    rows = [json.loads(line) for line in gzip.decompress(archive.read_bytes()).splitlines()]
    rows[0][field] += " "
    archive.write_bytes(gzip.compress("\n".join(json.dumps(row) for row in rows).encode()))
    with pytest.raises(ValueError, match="bytes differ"):
        load_archive(archive)


@pytest.mark.parametrize("change", ["request_key", "schema", "provenance", "target"])
def test_packing_rejects_invalid_request_or_answer(exchange_files, change):
    exchange, _, archive = exchange_files
    path = sorted(exchange.pending.glob("*.json"))[0]
    if change == "request_key":
        row = json.loads(path.read_bytes())
        row["inputs"] = {"text": "changed"}
    else:
        path = exchange.answers / path.name
        row = json.loads(path.read_bytes())
        if change == "schema":
            row["output"]["record_id"] = "UNIT-9999"
        elif change == "provenance":
            row["provenance"]["simulated_by"] = ""
        else:
            row["provenance"]["standing_in_for"] = "another-target"
    path.write_text(json.dumps(row))
    with pytest.raises((ValueError, ParseError)):
        pack_exchange(exchange.pending, exchange.answers, archive)
    assert not archive.exists()


def test_packing_rejects_unpaired_working_files(exchange_files):
    exchange, _, archive = exchange_files
    sorted(exchange.answers.glob("*.json"))[0].unlink()
    with pytest.raises(ValueError, match="one answer per key"):
        pack_exchange(exchange.pending, exchange.answers, archive)


@pytest.mark.parametrize("change", ["missing", "output", "provenance"])
def test_check_rejects_missing_or_different_recording_evidence(exchange_files, change):
    exchange, store, archive = exchange_files
    pack_exchange(exchange.pending, exchange.answers, archive)
    rows = [json.loads(line) for line in store.path.read_text().splitlines()]
    if change == "missing":
        rows[0]["key"] = "a" * 64
    elif change == "output":
        rows[0]["output"] = {"record_id": "none"}
    else:
        rows[0]["provenance"]["simulated_by"] = "another-simulator"
    store.path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    with pytest.raises(ValueError, match="Missing archive entry|differs from recording"):
        check_archive(archive, store.path)


def test_secret_filter_uses_verified_archive_without_working_files(exchange_files):
    exchange, store, archive = exchange_files
    pack_exchange(exchange.pending, exchange.answers, archive)
    key = store.entries()[0].key
    shutil.rmtree(exchange.pending)
    shutil.rmtree(exchange.answers)
    public = {
        "type": "Hex High Entropy String",
        "hashed_secret": hashlib.sha1(key.encode()).hexdigest(),
    }
    unknown = {
        "type": "Hex High Entropy String",
        "hashed_secret": hashlib.sha1(b"unknown").hexdigest(),
    }
    other_type = {**public, "type": "Secret Keyword"}
    remaining, count = remaining_findings(
        {"calls.jsonl": [public, unknown, other_type]}, exchange.pending
    )
    assert count == 1
    assert remaining == {"calls.jsonl": [unknown, other_type]}
    archive.write_bytes(b"invalid gzip")
    with pytest.raises(OSError):
        remaining_findings({"calls.jsonl": [public]}, exchange.pending)


def test_check_command_works_without_working_files(exchange_files, monkeypatch, capsys):
    exchange, _, archive = exchange_files
    pack_exchange(exchange.pending, exchange.answers, archive)
    shutil.rmtree(exchange.pending)
    shutil.rmtree(exchange.answers)
    monkeypatch.chdir(archive.parents[2])
    monkeypatch.setattr(sys, "argv", ["pack_exchange.py", "--check"])
    main()
    assert "PASS: 2 recordings match archived answers and provenance" in capsys.readouterr().out


def test_secret_scan_checks_decompressed_answer_content(exchange_files):
    exchange, store, archive = exchange_files
    unknown = hashlib.sha256(b"test-only nonpublic value in compressed evidence").hexdigest()
    path = sorted(exchange.answers.glob("*.json"))[0]
    answer = json.loads(path.read_text())
    answer["provenance"]["simulated_by"] = unknown
    path.write_text(json.dumps(answer, indent=2))
    rows = [
        row.model_copy(update={"provenance": answer["provenance"]}) if row.key == path.stem else row
        for row in store.entries()
    ]
    store.path.write_text("".join(row.model_dump_json() + "\n" for row in rows))
    pack_exchange(exchange.pending, exchange.answers, archive)
    shutil.rmtree(exchange.pending)
    shutil.rmtree(exchange.answers)
    results = archived_findings(archive)
    remaining, _ = remaining_findings(results, exchange.pending)
    assert any(
        finding["hashed_secret"] == hashlib.sha1(unknown.encode()).hexdigest()
        for findings in remaining.values()
        for finding in findings
    )


def test_saved_exchange_covers_all_recordings():
    assert (
        check_archive(
            Path("evals/recordings/exchange.jsonl.gz"), Path("evals/recordings/calls.jsonl")
        )
        == 444
    )
