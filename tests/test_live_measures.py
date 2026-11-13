"""D26: a live report's unreadable answers, no-answers, spend and latency, from the recordings."""

import hashlib
from types import SimpleNamespace
from typing import Any

import pytest

from docket.evaluation import Outcome, live_measures, percentile
from docket.llm.replay import Recording
from tests.test_graded_evidence import corpus_labels


def key(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest()


def answer(name: str, cost: str, latency: int, *, reason: str | None = None) -> Recording:
    return Recording(
        key=key(name),
        outcome="unreadable" if reason else "answer",
        reason=reason,
        output=None if reason else {"category": "invoice"},
        raw_text="" if reason else '{"category":"invoice"}',
        cost_usd=cost,
        input_tokens=1_000,
        output_tokens=0 if reason else 8,
        latency_ms=latency,
        output_mode="json_schema",
        source="synthetic_corpus",
        provenance={"provider": "anthropic", "model": "claude-haiku-5-5", "recorded": "d"},
    )


def calls(document: str) -> dict[str, Any]:
    return {
        key(document + "-classify"): SimpleNamespace(activity="classify"),
        key(document + "-choose"): SimpleNamespace(activity="choose"),
    }


def outcome(label, status="would_review", reason="below_threshold", category=None, failure=None):
    suggestion: dict[str, str | None] = {"record_id": None}
    if failure:
        suggestion["suggestion_failure_reason"] = failure
    return Outcome(
        label.document_id,
        status,
        reason,
        None,
        category or label.category.value,
        "0",
        "none",
        suggestion,
    )


def test_live_measures_count_failures_spend_and_latency():
    _, labels = corpus_labels(7)
    a, b, c, d, e = labels[:5]
    requests = {
        a.document_id: calls("a"),
        b.document_id: calls("b"),
        c.document_id: {},
        d.document_id: calls("d"),
        e.document_id: calls("e"),
    }
    recordings = [
        answer("a-classify", "0.0002", 100),
        answer("a-choose", "0.0001", 300),
        answer("b-classify", "0.0003", 200, reason="refusal"),
        answer("e-classify", "0.0001", 150),
    ]
    outcomes = [
        outcome(a),
        outcome(b, "failed", "parse_error", category="misc"),
        outcome(c, "rejected", "corrupt"),
        outcome(d, "failed", "no_answer", category="misc"),
        outcome(e, failure="no_answer"),
    ]
    legacy = [outcome(a), outcome(b), outcome(c, "rejected", "corrupt"), outcome(d), outcome(e)]
    measures = live_measures(list(labels[:5]), outcomes, legacy, requests, recordings)

    assert measures["requests"] == 8
    assert measures["requests_answered"] == 4
    # b's choose call never ran after its category call failed; d's never ran after no answer.
    assert measures["requests_not_asked"] == 2
    assert measures["unreadable_count"] == 1
    assert measures["unreadable_by_reason"] == {"refusal": 1}
    assert measures["unreadable_by_activity"] == {"classify": 1}
    assert measures["refusal_categories"] == {"unrecorded": 1}
    assert measures["category_unreadable_to_review"] == 0
    assert measures["no_answer_count"] == 2
    assert measures["maybe_billed_count"] == 0
    assert measures["output_modes"] == {"json_schema": 4}
    assert measures["spend_usd"] == "0.0007"
    assert measures["spend_by_activity_usd"] == {"choose": "0.0001", "classify": "0.0006"}
    assert measures["documents"] == 5
    assert measures["documents_not_rejected"] == 4
    assert measures["documents_reaching_model"] == 4
    assert measures["cost_per_document_reaching_model_usd"] == "0.000175"
    assert measures["cost_per_document_not_rejected_usd"] == "0.000175"
    assert measures["cost_per_document_usd"] == "0.00014"
    # a and e keep their gold category; b and d failed, so they fell back to misc.
    fallbacks_right = (b.category.value == "misc") + (d.category.value == "misc")
    assert measures["category_correct_reaching_model"] == 2 + fallbacks_right
    assert measures["latency_ms_median"] == 150
    assert measures["latency_ms_p95"] == 300


def test_percentile_is_nearest_rank():
    assert percentile([], 0.5) is None
    assert percentile([5], 0.95) == 5
    assert percentile(list(range(1, 21)), 0.95) == 19
    assert percentile(list(range(1, 21)), 0.5) == 10


def test_a_retried_document_counts_its_request_once():
    _, labels = corpus_labels(7)
    a = labels[0]
    duplicate = answer("a-classify", "0.0002", 100)
    measures = live_measures(
        [a], [outcome(a)], [outcome(a)], {a.document_id: calls("a")}, [duplicate, duplicate]
    )
    assert measures["requests_answered"] == 1
    assert measures["spend_usd"] == "0.0002"


def test_refused_category_is_counted_by_safety_category_and_routed_to_review():
    _, labels = corpus_labels(7)
    a, b = labels[:2]
    bio = answer("a-classify", "0.0003", 200, reason="refusal").model_copy(
        update={"refusal_category": "bio"}
    )
    measures = live_measures(
        [a, b],
        [outcome(a, reason="category_unreadable", category="misc"), outcome(b)],
        [outcome(a), outcome(b)],
        {a.document_id: calls("a"), b.document_id: calls("b")},
        [bio, answer("a-choose", "0.0001", 100), answer("b-classify", "0.0001", 100)],
    )
    assert measures["refusal_categories"] == {"bio": 1}
    assert measures["category_unreadable_to_review"] == 1
    assert measures["requests_not_asked"] == 1


async def test_a_maybe_billed_call_counts_at_the_worst_case_the_gateway_charged(
    postgres_engine, tmp_path
):
    import httpx2

    from docket.evaluation import pending_requests
    from docket.llm.budget import load_prices
    from docket.llm.errors import MaybeBilled
    from docket.prompts import classify_request
    from docket.settings import Settings
    from tests.test_anthropic_transport import FakeApi, gateway

    folder, labels = corpus_labels(7)
    label = labels[0]
    settings = Settings(
        _env_file=None, docket_model_provider="anthropic", docket_model="claude-haiku-5-5"
    )
    requests = pending_requests([label], folder, settings)
    classify = next(request for request in requests.values() if request.activity == "classify")
    live = gateway(FakeApi(httpx2.ReadTimeout("slow")), postgres_engine, tmp_path)
    with pytest.raises(MaybeBilled):
        await live.call(**classify_request(classify.inputs["text"]), source="synthetic_corpus")
    charged = live.actual_cost_usd
    assert charged > 0

    failed = outcome(label, "failed", "maybe_billed", category="misc")
    measures = live_measures(
        [label],
        [failed],
        [outcome(label)],
        {label.document_id: requests},
        [],
        prices=load_prices().ceiling("claude-haiku-5-5"),
    )
    assert measures["maybe_billed_count"] == 1
    assert measures["maybe_billed_spend_usd"] == str(charged)
    assert measures["spend_usd"] == str(charged)
    assert measures["spend_by_activity_usd"] == {"classify": str(charged)}
    assert measures["cost_per_document_reaching_model_usd"] == str(charged)
