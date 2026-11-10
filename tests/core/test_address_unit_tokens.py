from decimal import Decimal

import pytest

from docket.config import Signal, load_records
from docket.core.candidates import address_strength, normalize_address, shortlist
from docket.core.scoring import Tier, load_scoring_config, score_choice


@pytest.mark.parametrize("source", ["text", "subject", "filename"])
def test_hyphen_joined_address_conflicts_with_another_shipped_record(source):
    records = load_records()
    text = "UNIT-0002\nTenant: Bianca Lee\nBuilding: CEDAR_01"
    address = "1010-Cedar-Street-Apt-1.pdf"
    inputs = {"text": text}
    inputs[source] = f"{text}\n{address}" if source == "text" else address
    result = shortlist(records, **inputs)
    first = next(item for item in result.candidates if item.record_id == "UNIT-0001")
    assert normalize_address(address) == "1010 cedar st unit 1 pdf"
    assert first.signals[Signal.ADDRESS] == 1
    assert result.conflict_record_ids == ("UNIT-0001", "UNIT-0002")
    assert result.conflict
    scored = score_choice(result, "UNIT-0002", load_scoring_config())
    assert scored.raw_score == Decimal(".65")
    assert scored.decision == "review" and scored.reason == "conflicting_records"
    assert scored.tier == Tier.NONE and scored.score == 0


@pytest.mark.parametrize(
    "expected,actual", [("A1", "B1"), ("B1", "A1"), ("1A", "1"), ("12", "1-2"), ("1", "1-2")]
)
def test_whole_unit_identity_is_required_for_address_support(expected, actual):
    shipped = load_records()[0]
    record = shipped.model_copy(
        update={"address": shipped.address.removesuffix("1") + expected, "aliases": []}
    )
    address = shipped.address.removesuffix("1") + actual
    assert normalize_address(record.address) != normalize_address(address)
    assert address_strength([record.address], [address], 0.88) == 0
    result = shortlist([record], text=f"{record.record_id}\nAddress: {address}")
    assert result.candidates[0].signals[Signal.ADDRESS] == 0
    assert not result.conflict
    scored = score_choice(result, record.record_id, load_scoring_config())
    assert scored.independent_signals == frozenset({Signal.EXPLICIT_ID})
    assert scored.score == Decimal(".30")
    assert scored.tier == Tier.MEDIUM and scored.decision == "review"


@pytest.mark.parametrize("unit", ["A1", "B1", "1A", "12", "1-2"])
@pytest.mark.parametrize("designator", ["Apt.", "Unit", "#"])
def test_whole_unit_identity_keeps_abbreviation_matches(unit, designator):
    shipped = load_records()[0]
    record = shipped.model_copy(
        update={"address": shipped.address.removesuffix("1") + unit, "aliases": []}
    )
    address = f"1010-Cedar-St-{designator}-{unit}.pdf"
    assert normalize_address(address) == f"1010 cedar st unit {unit.casefold()} pdf"
    result = shortlist([record], text=record.record_id, filename=address)
    assert result.candidates[0].signals[Signal.ADDRESS] == 1
    assert not result.conflict
    assert score_choice(result, record.record_id, load_scoring_config()).decision == "file"
