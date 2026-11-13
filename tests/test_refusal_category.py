"""A refusal's safety category is kept with the answer and comes back in replay."""

import json

import pytest

from docket.llm.errors import UnreadableAnswer
from docket.llm.gateway import Gateway, Mode
from docket.llm.replay import ReplayStore
from tests.test_anthropic_transport import FakeApi, choose, gateway, message


def refusal(category):
    body = message("", stop="refusal", output_tokens=0)
    body["content"] = []
    if category is not None:
        body["stop_details"] = {"type": "refusal", "category": category, "explanation": "x"}
    return body


@pytest.mark.parametrize("category", ["bio", None])
async def test_refusal_category_is_recorded_and_replayed(postgres_engine, tmp_path, category):
    store = ReplayStore(tmp_path / "anthropic.jsonl")
    live = gateway(FakeApi(refusal(category)), postgres_engine, tmp_path, store=store)
    with pytest.raises(UnreadableAnswer) as raised:
        await live.call(**choose())
    assert raised.value.reason == "refusal"
    assert raised.value.refusal_category == category
    [entry] = store.entries()
    assert entry.reason == "refusal" and entry.refusal_category == category
    line = json.loads((tmp_path / "anthropic.jsonl").read_text())
    if category is None:
        # Entries without a category keep the format written before the field existed.
        assert "refusal_category" not in line
    else:
        assert line["refusal_category"] == category
    replay = Gateway(mode=Mode.REPLAY, model="claude-haiku-5-5", store=store)
    with pytest.raises(UnreadableAnswer) as replayed:
        await replay.call(**choose())
    assert replayed.value.refusal_category == category
    assert replay.call_count == 0


def test_only_a_refusal_carries_a_category():
    with pytest.raises(ValueError):
        UnreadableAnswer("cut", "bio")
