from decimal import Decimal

import pytest

from docket.config import Signal, load_records
from docket.core.candidates import shortlist
from docket.core.scoring import ScoringConfig, Tier, load_scoring_config, score_choice


@pytest.mark.parametrize("unit", ["1-B", "1-2", "1A"])
def test_different_unit_cannot_corroborate_shipped_unit_one(unit):
    records = load_records()
    result = shortlist(records, text=f"UNIT-0001\nAddress: 1010 Cedar Street Apt {unit}")
    first = next(item for item in result.candidates if item.record_id == "UNIT-0001")
    assert first.signals[Signal.ADDRESS] == 0
    assert not result.conflict
    scored = score_choice(result, "UNIT-0001", load_scoring_config())
    assert scored.independent_signals == frozenset({Signal.EXPLICIT_ID})
    assert scored.tier == Tier.MEDIUM and scored.decision == "review"


@pytest.mark.parametrize("source", ["text", "subject", "filename"])
def test_hyphen_joined_address_still_conflicts_with_another_id(source):
    inputs = {"text": "UNIT-0002"}
    inputs[source] = (
        "UNIT-0002\n1010-Cedar-Street-Apt-1.pdf"
        if source == "text"
        else "1010-Cedar-Street-Apt-1.pdf"
    )
    result = shortlist(load_records(), **inputs)
    assert result.conflict and result.conflict_record_ids == ("UNIT-0001", "UNIT-0002")


@pytest.mark.parametrize("address", ["1010 Cedar Street Apt 1", "1010 Cedar St #1"])
@pytest.mark.parametrize("repeated", [False, True])
def test_address_derived_building_is_not_independent(address, repeated):
    record = load_records()[0].model_copy(update={"building": "Cedar"})
    result = shortlist([record], text=address + (f"\n{address}" if repeated else ""))
    candidate = result.candidates[0]
    assert candidate.signals[Signal.ADDRESS] == candidate.signals[Signal.BUILDING] == 1
    assert not candidate.building_is_explicit
    config = ScoringConfig.model_validate(
        load_scoring_config().model_dump()
        | {"strong_threshold": Decimal(".35"), "auto_file_threshold": Decimal(".35")}
    )
    scored = score_choice(result, record.record_id, config)
    assert scored.raw_score == Decimal(".30")
    assert scored.independent_signals == frozenset({Signal.ADDRESS})
    assert scored.tier == Tier.MEDIUM and scored.decision == "review"


@pytest.mark.parametrize("source", ["text", "subject", "filename"])
def test_building_mentioned_outside_address_remains_independent(source):
    record = load_records()[0].model_copy(update={"building": "Cedar"})
    inputs = {"text": record.address}
    if source == "text":
        inputs[source] += " Building: Cedar"
    else:
        inputs[source] = "Building: Cedar"
    result = shortlist([record], **inputs)
    assert result.candidates[0].building_is_explicit
    scored = score_choice(result, record.record_id, load_scoring_config())
    assert scored.independent_signals == frozenset({Signal.ADDRESS, Signal.BUILDING})
