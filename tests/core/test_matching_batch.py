import signal
from time import perf_counter

import pytest

from docket.config import Signal, load_records
from docket.core.candidates import address_strength, name_strength, normalize_text, shortlist
from tests.matching_inputs import distinct_capped_text, double_records


def test_distinct_realistic_lines_at_caps_finish_under_one_second():
    records = double_records()
    text = distinct_capped_text()
    assert len(records) == 400

    def deadline(signum, frame):
        raise TimeoutError("distinct-line shortlist exceeded one second")

    previous = signal.signal(signal.SIGALRM, deadline)
    signal.setitimer(signal.ITIMER_REAL, 1)
    started = perf_counter()
    try:
        result = shortlist(records, text=text)
        elapsed = perf_counter() - started
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    print(f"distinct realistic lines: {elapsed:.6f}s; 64000 chars; 2000 lines; 400 records")
    assert not result.over_budget
    assert elapsed < 1


@pytest.mark.parametrize("separator", ["／", "＼", "℅"])
def test_basename_is_selected_before_compatibility_normalization(separator):
    filename = f"UNIT-0002 notice 11{separator}01{separator}2026.pdf"
    result = shortlist(load_records(), text="", filename=filename)
    assert result.explicit_ids == ("UNIT-0002",)
    assert result.candidates[0].record_id == "UNIT-0002"
    assert result.candidates[0].signals[Signal.FILENAME_ID] == 1
    assert not result.over_budget


@pytest.mark.parametrize("former", [False, True])
@pytest.mark.parametrize("cutoff", [0.5, 0.88, 1.0])
@pytest.mark.parametrize(
    "variant", ["exact", "swapped", "typo", "phrase", "wrong_unit", "unrelated"]
)
def test_batched_signals_equal_scalar_scores(former, cutoff, variant):
    record = load_records()[0]
    name = (record.former_tenants if former else record.current_tenants)[0]
    address = record.address
    if variant == "swapped":
        name = " ".join(reversed(name.split()))
        address = "1010 Cedar St #1"
    elif variant == "typo":
        name = "z" + name[1:]
        address = "1010 Zedar Street Apt 1"
    elif variant == "phrase":
        name = f"Please file {name} now"
    elif variant == "wrong_unit":
        address = "1010 Cedar Street Apt 1A"
    elif variant == "unrelated":
        name, address = "Different Tenant", "1313 Maple Road Apt 9"
    lines = [record.record_id, address, name]
    chunks = [normalize_text(line) for line in lines]
    candidate = shortlist([record], text="\n".join(lines), tenant_cutoff=cutoff).candidates[0]
    assert candidate.signals[Signal.TENANT_NAME] == name_strength(
        [*record.current_tenants, *record.former_tenants], chunks, cutoff
    )
    assert candidate.former_tenant_score == name_strength(record.former_tenants, chunks, cutoff)
    assert candidate.signals[Signal.ADDRESS] == address_strength(
        [record.address, *record.aliases], lines, cutoff
    )


def test_empty_tenant_names_and_former_tenants_supply_no_batch_signal():
    record = load_records()[0].model_copy(update={"current_tenants": [""], "former_tenants": []})
    candidate = shortlist([record], text=record.record_id).candidates[0]
    assert candidate.signals[Signal.TENANT_NAME] == candidate.former_tenant_score == 0


def test_sorted_name_evidence_alone_does_not_expand_the_binary_shortlist():
    record = load_records()[0]
    text = " ".join(reversed(record.current_tenants[0].split()))
    assert name_strength(record.current_tenants, [normalize_text(text)], 0.88) == 1
    assert shortlist([record], text=text).candidates == ()
