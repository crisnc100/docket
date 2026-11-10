import signal
from time import perf_counter

import pytest

from docket.config import Signal, load_records
from docket.core.candidates import normalize_address, shortlist


def test_address_with_128000_spaces_finishes_within_half_a_second():
    records = load_records()

    def deadline(signum, frame):
        raise AssertionError("shortlist exceeded 0.5 seconds on trailing whitespace")

    previous = signal.signal(signal.SIGALRM, deadline)
    started = perf_counter()
    signal.setitimer(signal.ITIMER_REAL, 0.5)
    try:
        shortlist(records, text="Address: a" + " " * 128000 + "x")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    assert perf_counter() - started < 0.5


@pytest.mark.parametrize("ending", [")", '"', "]", "}", "'", "”", "’"])
def test_closing_quotes_and_brackets_do_not_change_unit_evidence(ending):
    record = load_records()[0]
    address = f"1010 Cedar Street Apt 1{ending}"
    assert normalize_address(address) == normalize_address(record.address)
    result = shortlist([record], text=address)
    assert result.candidates[0].signals[Signal.ADDRESS] == 1


def test_same_line_address_label_keeps_separate_building_evidence():
    record = load_records()[0]
    result = shortlist([record], text=f"Address: {record.address} Building: {record.building}")
    assert result.candidates[0].building_is_explicit
