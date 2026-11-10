import signal
from statistics import median
from time import perf_counter

import pytest

from docket.config import Signal, load_records
from docket.core.candidates import address_strength, normalize_address, shortlist


def timed_shortlist(records, text):
    def deadline(signum, frame):
        raise TimeoutError("shortlist exceeded 0.5 seconds")

    previous = signal.signal(signal.SIGALRM, deadline)
    signal.setitimer(signal.ITIMER_REAL, 0.5)
    started = perf_counter()
    try:
        shortlist(records, text=text)
        return perf_counter() - started
    except TimeoutError:
        return None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def test_matching_work_is_bounded_for_all_filler_generators():
    records = load_records()
    generators = (
        [("", filler, "x") for filler in ("st.", "st-", "st_", "st ", "dr.", "rd-", "ln.", "ave_")]
        + [("Address: 1 Cedar St", filler, "x") for filler in (".", "-", "_", " ", "._ ")]
        + [("Address:", " ", "x"), ("Apt ", "x", "")]
    )
    failures = []
    for prefix, filler, suffix in generators:
        times = []
        for size in (200000, 400000):
            text = prefix + (filler * (size // len(filler) + 1))[:size] + suffix
            samples = []
            for _ in range(3):
                elapsed = timed_shortlist(records, text)
                if elapsed is None:
                    break
                samples.append(elapsed)
            times.append(median(samples) if len(samples) == 3 else None)
        print(f"prefix={prefix!r}; filler={filler!r}; 200k={times[0]}; 400k={times[1]}")
        if any(value is None for value in times):
            failures.append(f"{prefix!r}/{filler!r}: exceeded 0.5 seconds")
        else:
            small, large = times
            assert small is not None and large is not None
            if max(small, large) >= 0.5 or large > 2.5 * small:
                failures.append(f"{prefix!r}/{filler!r}: times={times}")
    assert not failures, "\n".join(failures)


@pytest.mark.parametrize("sentence", ["1. I am writing", "1, a two-bedroom"])
def test_sentence_initial_letter_does_not_become_a_unit_suffix(sentence):
    record = load_records()[0]
    address = f"1010 Cedar Street Apt {sentence}"
    normalized = normalize_address(address)
    assert "unit 1-i" not in normalized and "unit 1-a" not in normalized
    assert address_strength([record.address], [address], 0.88) == 1
    result = shortlist([record], text=f"{record.record_id}\n{address}")
    assert result.candidates[0].signals[Signal.ADDRESS] == 1


@pytest.mark.parametrize("unit", ["1A", "1-A"])
def test_adjacent_unit_suffix_still_has_its_own_identity(unit):
    record = load_records()[0].model_copy(
        update={"address": f"1010 Cedar Street Apt {unit}", "aliases": []}
    )
    assert address_strength([record.address], [record.address], 0.88) == 1
    assert address_strength([record.address], ["1010 Cedar Street Apt 1"], 0.88) == 0


@pytest.mark.parametrize("source", ["text", "subject", "filename"])
def test_id_conflicts_after_a_long_line_still_use_the_full_source(source):
    inputs = {"text": "UNIT-0001"}
    tail = "filler " * 2000 + "UNIT-0002"
    inputs[source] = f"UNIT-0001 {tail}" if source == "text" else tail
    result = shortlist(load_records(), **inputs)
    assert result.conflict
    assert result.explicit_ids == ("UNIT-0001", "UNIT-0002")


def test_ambiguous_spaced_unit_does_not_hide_another_address_on_the_line():
    first, second = load_records()[:2]
    result = shortlist(
        [first, second], text=f"{first.record_id}\n{first.address} B; {second.address}"
    )
    candidates = {candidate.record_id: candidate for candidate in result.candidates}
    assert candidates[first.record_id].signals[Signal.ADDRESS] == 0
    assert candidates[second.record_id].signals[Signal.ADDRESS] == 1
    assert result.conflict


@pytest.mark.parametrize("boundary", ["token", "line"])
def test_matching_bounds_cannot_turn_a_longer_unit_into_unit_one(boundary):
    record = load_records()[0]
    if boundary == "token":
        address = record.address + "." * 200000 + "B"
    else:
        address = record.address + "B"
        prefix_length = 8192 - address.index("1B") - 1
        prefix = (
            "x " * (prefix_length // 2)
            if prefix_length % 2 == 0
            else "xx " + "x " * ((prefix_length - 3) // 2)
        )
        address = prefix + address
    result = shortlist([record], text=address)
    assert all(candidate.signals[Signal.ADDRESS] == 0 for candidate in result.candidates)
