from decimal import Decimal

import pytest

from docket.config import Signal, load_records
from docket.core.candidates import address_strength, normalize_address, shortlist
from docket.core.scoring import ScoringConfig, Tier, load_scoring_config, score_choice


@pytest.mark.parametrize("suffix", ["lease", "inspection-report"])
@pytest.mark.parametrize("source", ["text", "subject", "filename"])
def test_document_words_after_slugged_unit_do_not_hide_conflicts(suffix, source):
    filename = f"1010-Cedar-Street-Apt-1-{suffix}.pdf"
    inputs = {"text": "UNIT-0002"}
    inputs[source] = f"UNIT-0002\n{filename}" if source == "text" else filename
    result = shortlist(load_records(), **inputs)
    assert result.conflict and result.conflict_record_ids == ("UNIT-0001", "UNIT-0002")
    assert score_choice(result, "UNIT-0002", load_scoring_config()).decision == "review"
    assert normalize_address(filename) == f"1010 cedar st unit 1 {suffix.replace('-', ' ')} pdf"


@pytest.mark.parametrize("unit", ["1-B", "1-2", "A1"])
def test_slugged_document_word_preserves_whole_unit_identity(unit):
    record = load_records()[0].model_copy(
        update={"address": f"1010 Cedar Street Apt {unit}", "aliases": []}
    )
    result = shortlist(
        [record], text=record.record_id, filename=f"1010-Cedar-Street-Apt-{unit}-lease.pdf"
    )
    assert result.candidates[0].signals[Signal.ADDRESS] == 1
    assert score_choice(result, record.record_id, load_scoring_config()).decision == "file"


@pytest.mark.parametrize("unit", ["1/B", "1_B", "1.B", "1+B", "1•B", "1 B"])
def test_punctuation_cannot_turn_another_unit_into_unit_one(unit):
    record = load_records()[0]
    address = f"1010 Cedar Street Apt {unit}"
    assert address_strength([record.address, *record.aliases], [address], 0.88) == 0
    result = shortlist([record], text=f"{record.record_id}\nAddress: {address}")
    assert result.candidates[0].signals[Signal.ADDRESS] == 0
    scored = score_choice(result, record.record_id, load_scoring_config())
    assert scored.independent_signals == frozenset({Signal.EXPLICIT_ID})
    assert scored.tier == Tier.MEDIUM and scored.decision == "review"


@pytest.mark.parametrize("designator", ["Suite ", ""])
def test_letter_leading_units_with_or_without_designator_stay_distinct(designator):
    record = load_records()[0].model_copy(
        update={"address": f"1010 Cedar Street {designator}A1", "aliases": []}
    )
    address = f"1010 Cedar Street {designator}B1"
    assert address_strength([record.address], [address], 0.88) == 0
    result = shortlist([record], text=f"{record.record_id}\nAddress: {address}")
    assert result.candidates[0].signals[Signal.ADDRESS] == 0
    assert score_choice(result, record.record_id, load_scoring_config()).decision == "review"


@pytest.mark.parametrize("designator", ["Suite ", "", "Apt ", "#"])
def test_equivalent_letter_leading_units_keep_exact_support(designator):
    record = load_records()[0].model_copy(
        update={"address": "1010 Cedar Street Unit A1", "aliases": []}
    )
    address = f"1010 Cedar Street {designator}A1"
    assert address_strength([record.address], [address], 0.88) == 1
    result = shortlist([record], text=f"{record.record_id}\n{address}")
    assert result.candidates[0].signals[Signal.ADDRESS] == 1


@pytest.mark.parametrize(
    "other",
    [
        "999 Cedar Street Apt 9",
        "999 Cedar Road Suite B1",
        "999 Cedar Streeet Unit 9",
        "999 Cedar Avenue",
    ],
)
def test_building_from_another_address_is_not_independent(other):
    record = load_records()[0].model_copy(
        update={"address": "1010 Pine Street Apt 1", "aliases": [], "building": "Cedar"}
    )
    result = shortlist([record], text=f"{record.address}\nOther address: {other}")
    candidate = result.candidates[0]
    assert candidate.signals[Signal.ADDRESS] == candidate.signals[Signal.BUILDING] == 1
    assert not candidate.building_is_explicit and not result.conflict
    config = ScoringConfig.model_validate(
        load_scoring_config().model_dump()
        | {"strong_threshold": Decimal(".35"), "auto_file_threshold": Decimal(".35")}
    )
    scored = score_choice(result, record.record_id, config)
    assert scored.raw_score == Decimal(".30")
    assert scored.independent_signals == frozenset({Signal.ADDRESS})
    assert scored.tier == Tier.MEDIUM and scored.decision == "review"


def test_building_mention_outside_other_address_still_counts():
    record = load_records()[0].model_copy(update={"building": "Cedar"})
    result = shortlist(
        [record],
        text=f"{record.address}\nOther address: 999 Cedar Road Suite B1 Building: Cedar",
    )
    assert result.candidates[0].building_is_explicit
    scored = score_choice(result, record.record_id, load_scoring_config())
    assert scored.independent_signals == frozenset({Signal.ADDRESS, Signal.BUILDING})
