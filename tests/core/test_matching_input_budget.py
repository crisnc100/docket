import signal
from time import process_time

import pytest

from docket.config import Signal, load_records
from docket.core.candidates import normalize_text, shortlist
from docket.core.scoring import load_scoring_config, score_choice
from tests.matching_inputs import distinct_capped_text


def capped_attack(kind):
    text = distinct_capped_text(kind)
    assert len(text) == 64000
    assert len(text.splitlines()) == 2000
    assert len(set(text.splitlines())) == 2000
    return text


@pytest.mark.parametrize(
    "kind", ["whitespace", "street_separators", "street_keywords", "unit_lines"]
)
def test_matching_at_both_caps_with_double_records_finishes_under_one_second(kind):
    records = load_records()
    records += [
        record.model_copy(update={"record_id": f"UNIT-{len(records) + index + 1:04d}"})
        for index, record in enumerate(records)
    ]
    assert len(records) == 400
    text = capped_attack(kind)

    def deadline(signum, frame):
        raise TimeoutError(f"{kind}: shortlist exceeded one CPU second")

    previous = signal.signal(signal.SIGPROF, deadline)
    signal.setitimer(signal.ITIMER_PROF, 1)
    started = process_time()
    try:
        result = shortlist(records, text=text)
        elapsed = process_time() - started
    finally:
        signal.setitimer(signal.ITIMER_PROF, 0)
        signal.signal(signal.SIGPROF, previous)
    print(f"{kind}: {elapsed:.6f} CPU seconds; 64000 characters; 2000 lines; 400 records")
    assert elapsed < 1
    assert not result.over_budget


@pytest.mark.parametrize("limit", ["characters", "lines", "nfkc", "casefold"])
def test_over_budget_body_requires_review_and_keeps_late_id_conflict(limit):
    first, second = load_records()[:2]
    padding = {
        "characters": "x" * 64001,
        "lines": "x\n" * 2001,
        "nfkc": "\ufb03" * 22000,
        "casefold": "\u00df" * 33000,
    }[limit]
    result = shortlist(
        [first, second],
        text=f"{first.record_id}\n{second.address}\n{second.current_tenants[0]}\n"
        + padding
        + f"\n{second.record_id}",
        subject=f"{first.address}\n{first.current_tenants[0]}",
    )
    assert result.over_budget
    assert result.conflict
    assert result.explicit_ids == (first.record_id, second.record_id)
    candidates = {item.record_id: item for item in result.candidates}
    assert candidates[first.record_id].signals[Signal.ADDRESS] == 1
    assert candidates[second.record_id].signals[Signal.ADDRESS] == 0
    assert candidates[second.record_id].signals[Signal.TENANT_NAME] == 0
    for chosen in (first.record_id, None):
        decision = score_choice(result, chosen, load_scoring_config())
        assert decision.decision == "review"
        assert decision.reason == "input_too_large"
        assert decision.conflict


def test_over_budget_body_cannot_file_even_without_conflict():
    record = load_records()[0]
    result = shortlist(
        [record],
        text="x" * 64001,
        subject=f"{record.record_id}\n{record.address}\n{record.current_tenants[0]}",
    )
    decision = score_choice(result, record.record_id, load_scoring_config())
    assert not result.conflict
    assert decision.raw_score >= load_scoring_config().auto_file_threshold
    assert decision.decision == "review"
    assert decision.reason == "input_too_large"


@pytest.mark.parametrize("padding", ["x" * 1001, "\ufb03" * 334, "\u00df" * 501])
def test_over_budget_subject_keeps_only_its_explicit_id_signal(padding):
    record = load_records()[0]
    result = shortlist(
        [record],
        text="",
        subject=f"{record.record_id}\n{record.address}\n{record.current_tenants[0]}\n{padding}",
    )
    assert result.over_budget
    assert result.candidates[0].signals[Signal.EXPLICIT_ID] == 1
    assert result.candidates[0].signals[Signal.ADDRESS] == 0
    assert result.candidates[0].signals[Signal.TENANT_NAME] == 0
    assert score_choice(result, record.record_id, load_scoring_config()).reason == "input_too_large"


def test_filename_is_basename_only_and_does_not_use_body_token_caps():
    record = load_records()[0]
    filename = "2026-11-01-notice-of-rent-increase-1010-Cedar-Street-Apt-1-signed.pdf"
    result = shortlist([record], text="x" * 64001, filename=f"/UNIT-0002/{filename}")
    assert result.over_budget
    assert not result.conflict
    assert result.explicit_ids == ()
    assert result.candidates[0].signals[Signal.ADDRESS] == 1
    assert result.candidates[0].record_id == record.record_id
    assert score_choice(result, record.record_id, load_scoring_config()).reason == "input_too_large"
    within_budget = shortlist([record], text=record.record_id, filename=filename)
    assert not within_budget.over_budget
    assert within_budget.candidates[0].signals[Signal.ADDRESS] == 1
    assert within_budget.candidates[0].signals[Signal.EXPLICIT_ID] == 1


@pytest.mark.parametrize("padding", ["x" * 256, "\ufb03" * 86, "\u00df" * 128])
def test_over_budget_filename_drops_only_its_match_signals(padding):
    record = load_records()[0]
    result = shortlist(
        [record],
        text=f"{record.record_id}\n{record.address}",
        filename=record.record_id + "_" + padding,
    )
    assert result.over_budget
    assert result.candidates[0].signals[Signal.FILENAME_ID] == 0
    assert result.candidates[0].signals[Signal.ADDRESS] == 1
    assert result.candidates[0].signals[Signal.EXPLICIT_ID] == 1
    assert score_choice(result, record.record_id, load_scoring_config()).reason == "input_too_large"


def test_limits_are_inclusive_and_fields_cannot_crowd_out_each_other():
    record = load_records()[0]
    filename = "x" * (255 - len(record.address) - 1) + " " + record.address
    result = shortlist([record], text="x" * 64000, subject="x" * 1000, filename=filename)
    assert not result.over_budget
    assert result.candidates[0].signals[Signal.ADDRESS] == 1


def test_record_alias_normalization_does_not_bound_tokens():
    alias = "a" * 100
    assert normalize_text(alias) == alias


@pytest.mark.parametrize("separator", ["\n", "\r\n", "\r", "\u2028"])
def test_body_line_budget_counts_line_breaks_without_rejecting_trailing_break(separator):
    record = load_records()[0]
    for lines, expected in ((2000, False), (2001, True)):
        result = shortlist([record], text=("x" + separator) * lines, filename=record.record_id)
        assert result.over_budget is expected
