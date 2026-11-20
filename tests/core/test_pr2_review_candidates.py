import pytest

from docket.config import Signal, load_records
from docket.core.candidates import normalize_address, shortlist


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
