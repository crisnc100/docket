import json

import pytest

from docket.activities_schema import Classification, choice_schema
from docket.llm.errors import ExportPending, ParseError, RecordRefused
from docket.llm.exchange import Exchange, PendingRequest, validate_schema
from docket.llm.gateway import Gateway, Mode
from docket.llm.replay import ReplayStore
from docket.prompts import choose_request


def make_gateway(tmp_path, mode):
    return Gateway(
        mode=mode,
        model="target-model",
        store=ReplayStore(tmp_path / "calls.jsonl"),
        exchange=Exchange(tmp_path / "pending", tmp_path / "answers"),
    )


async def export(tmp_path):
    gateway = make_gateway(tmp_path, Mode.EXPORT)
    with pytest.raises(ExportPending):
        await gateway.call(**choose_request("synthetic", ["UNIT-0001"]), source="synthetic_corpus")
    assert gateway.call_count == 0
    paths = list((tmp_path / "pending").glob("*.json"))
    assert len(paths) == 1
    request = PendingRequest.model_validate_json(paths[0].read_text())
    request.verify_key()
    assert request.system_prompt and request.messages[0]["content"].endswith("synthetic")
    assert request.response_schema["$defs"]["RecordChoice"]["enum"] == ["UNIT-0001", "none"]
    assert request.activity == "choose" and request.prompt_version == "v1"
    assert request.target_model == "target-model"
    return gateway, request


def answer(tmp_path, request, output, **overrides):
    folder = tmp_path / "answers"
    folder.mkdir(exist_ok=True)
    body = {
        "output": output,
        "provenance": {
            "simulated_by": "operator-test-fixture",
            "standing_in_for": request.target_model,
        },
    }
    body.update(overrides)
    (folder / f"{request.key}.json").write_text(json.dumps(body))


async def test_export_import_replay_preserves_schema_and_provenance(tmp_path):
    gateway, request = await export(tmp_path)
    await export(tmp_path)
    assert not gateway.store.path.exists()
    answer(tmp_path, request, {"record_id": "UNIT-0001"})
    imported = make_gateway(tmp_path, Mode.IMPORT)
    result = await imported.call(
        **choose_request("synthetic", ["UNIT-0001"]), source="synthetic_corpus"
    )
    assert result.model_dump(mode="json") == {"record_id": "UNIT-0001"}
    entry = gateway.store.get(request.key)
    assert entry.cost_usd is None
    assert entry.provenance == {
        "simulated_by": "operator-test-fixture",
        "standing_in_for": "target-model",
    }
    replay = make_gateway(tmp_path, Mode.REPLAY)
    replayed = await replay.call(
        **choose_request("synthetic", ["UNIT-0001"]), source="synthetic_corpus"
    )
    assert replayed.model_dump(mode="json") == result.model_dump(mode="json")
    assert imported.call_count == replay.call_count == 0


@pytest.mark.parametrize(
    "output",
    [
        {"record_id": "UNIT-9999"},
        {},
        {"record_id": None},
        {"record_id": 1},
        {"record_id": "UNIT-0001", "extra": True},
    ],
)
async def test_import_rejects_answers_outside_request_schema(tmp_path, output):
    gateway, request = await export(tmp_path)
    answer(tmp_path, request, output)
    with pytest.raises(ParseError):
        gateway.exchange.import_answers([request.key], gateway.store)
    assert not gateway.store.path.exists()


async def test_import_rejects_wrong_provenance_and_tampered_request(tmp_path):
    gateway, request = await export(tmp_path)
    answer(
        tmp_path,
        request,
        {"record_id": "none"},
        provenance={"simulated_by": "test", "standing_in_for": "wrong"},
    )
    with pytest.raises(ParseError, match="different model"):
        gateway.exchange.import_answers([request.key], gateway.store)
    request.messages[0]["content"] = "tampered"
    (gateway.exchange.pending / f"{request.key}.json").write_text(request.model_dump_json())
    with pytest.raises(ParseError, match="digest"):
        gateway.exchange.import_answers([request.key], gateway.store)
    assert not gateway.store.path.exists()


async def test_export_non_corpus_refused_and_missing_answers_remain_pending(tmp_path):
    gateway, request = await export(tmp_path)
    assert gateway.exchange.import_answers([request.key], gateway.store) == {
        "imported": 0,
        "pending": 1,
    }
    with pytest.raises(RecordRefused):
        await gateway.call(**choose_request("private", ["UNIT-0001"]), source="http")
    assert not gateway.store.path.exists()


def test_unknown_classification_answer_is_rejected_on_import_not_normalized():
    with pytest.raises(ParseError):
        validate_schema({"category": "not-a-category"}, Classification.model_json_schema())
    validate_schema({"category": "misc"}, Classification.model_json_schema())
    with pytest.raises(ParseError):
        validate_schema(
            {"record_id": "none"}, {**choice_schema(["UNIT-0001"]).model_json_schema(), "not": {}}
        )


async def test_invalid_answer_in_batch_admits_no_recording(tmp_path):
    gateway, first = await export(tmp_path)
    with pytest.raises(ExportPending):
        await gateway.call(
            **choose_request("another synthetic", ["UNIT-0002"]), source="synthetic_corpus"
        )
    requests = [
        PendingRequest.model_validate_json(p.read_text())
        for p in gateway.exchange.pending.glob("*.json")
    ]
    second = next(item for item in requests if item.key != first.key)
    answer(tmp_path, first, {"record_id": "UNIT-0001"})
    answer(tmp_path, second, {"record_id": "UNIT-9999"})
    with pytest.raises(ParseError):
        gateway.exchange.import_answers([item.key for item in requests], gateway.store)
    assert not gateway.store.path.exists()


def test_schema_validator_checks_nested_types_and_fails_closed():
    validate_schema(
        {"x": [1, None]},
        {
            "type": "object",
            "required": ["x"],
            "properties": {
                "x": {"type": "array", "items": {"anyOf": [{"type": "integer"}, {"type": "null"}]}}
            },
            "additionalProperties": False,
        },
    )
    with pytest.raises(ParseError):
        validate_schema(True, {"type": "integer"})
    with pytest.raises(ParseError):
        validate_schema("wrong", {"const": "right"})
    with pytest.raises(ParseError):
        validate_schema("x", {"$ref": "external"})
