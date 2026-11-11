import signal
from time import perf_counter

import pytest
from rapidfuzz import fuzz

from docket.config import Signal, load_records
from docket.core.candidates import name_strength, normalize_address, normalize_text, shortlist


@pytest.mark.parametrize("separator", [".", "_", " ", "._ "])
@pytest.mark.parametrize(
    "prefix", ["Address: 1 Cedar St", "Address: 1 Cedar Street Apt", "Apt", "#"]
)
def test_street_followed_by_100000_separators_finishes_within_half_a_second(separator, prefix):
    records = load_records()
    padding = (separator * 100000)[:100000]
    text = prefix + padding + "x"

    def deadline(signum, frame):
        raise AssertionError(f"shortlist exceeded 0.5 seconds; separator={separator!r}")

    previous = signal.signal(signal.SIGALRM, deadline)
    started = perf_counter()
    signal.setitimer(signal.ITIMER_REAL, 0.5)
    try:
        shortlist(records, text=text)
    finally:
        elapsed = perf_counter() - started
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        print(f"prefix={prefix!r}; separator={separator!r}; seconds={elapsed:.6f}")
    assert elapsed < 0.5


@pytest.mark.parametrize("ending", [")", '"', "]", "}", "'", "”", "’"])
def test_closing_punctuation_before_pdf_extension_keeps_address_evidence(ending):
    record = load_records()[0]
    filename = f"Notice (1010 Cedar Street Apt 1{ending}.pdf"
    assert normalize_address(filename) == "notice 1010 cedar st unit 1 pdf"
    result = shortlist([record], text=record.record_id, filename=filename)
    assert result.candidates[0].signals[Signal.ADDRESS] == 1


@pytest.mark.parametrize("former", [False, True])
@pytest.mark.parametrize("variant", ["exact", "swapped", "typo", "unrelated"])
def test_cached_name_sorting_preserves_original_continuous_scores(former, variant):
    record = load_records()[0]
    names = record.former_tenants if former else record.current_tenants
    normalized = normalize_text(names[0])
    chunk = {
        "exact": normalized,
        "swapped": " ".join(reversed(normalized.split())),
        "typo": normalized[:-1] + "z",
        "unrelated": "another person",
    }[variant]
    expected = max(
        max(
            fuzz.ratio(normalize_text(name), chunk),
            fuzz.token_sort_ratio(normalize_text(name), chunk),
        )
        / 100
        for name in names
    )
    assert name_strength(names, [chunk], 0.88) == (expected if expected >= 0.88 else 0)


def test_long_separator_run_does_not_hide_a_late_conflicting_id():
    text = "UNIT-0001\nAddress: 1 Cedar St" + "._ " * 33334 + "x\nUNIT-0002"
    result = shortlist(load_records(), text=text)
    assert result.conflict
    assert result.explicit_ids == ("UNIT-0001", "UNIT-0002")
