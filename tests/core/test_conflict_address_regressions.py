import pytest

from docket.config import Signal, load_records
from docket.core.candidates import address_strength, normalize_address, shortlist
from docket.core.scoring import Tier, load_scoring_config, score_choice


@pytest.mark.parametrize("k", [1, 5])
@pytest.mark.parametrize("former", [False, True])
@pytest.mark.parametrize("explicit", [False, True])
def test_x1_full_strength_names_conflict_across_all_shipped_records(k, former, explicit):
    records = load_records()
    first, second = records[:2]
    names = [
        " ".join(reversed((record.former_tenants if former else record.current_tenants)[0].split()))
        for record in [first, second]
    ]
    prefix = "Former tenant" if former else "Tenant"
    text = "\n".join(
        [
            first.record_id if explicit else "",
            f"Building: {first.building}",
            *(f"{prefix}: {name}" for name in names),
        ]
    )
    result = shortlist(records, text=text, k=k)
    first_candidate = next(item for item in result.candidates if item.record_id == first.record_id)
    assert first_candidate.signals[Signal.TENANT_NAME] == 1
    assert result.conflict
    assert result.conflict_record_ids == (first.record_id, second.record_id)
    if k == 5:
        second_candidate = next(
            item for item in result.candidates if item.record_id == second.record_id
        )
        assert second_candidate.signals[Signal.TENANT_NAME] == 1
    for candidate in result.candidates:
        config = load_scoring_config()
        scored = score_choice(result, candidate.record_id, config)
        if explicit and candidate.record_id == first.record_id:
            assert scored.raw_score > config.auto_file_threshold
        assert scored.decision == "review" and scored.tier == Tier.NONE
        assert scored.reason == "conflicting_records" and scored.score == 0


def test_x1_explicit_id_conflicts_with_full_name_outside_selected_candidates():
    records = load_records()
    first, second = records[:2]
    name = " ".join(reversed(second.current_tenants[0].split()))
    result = shortlist(records, text=f"{first.record_id}\nTenant: {name}", k=1)
    assert [item.record_id for item in result.candidates] == [first.record_id]
    assert result.conflict and result.conflict_record_ids == (first.record_id, second.record_id)
    scored = score_choice(result, first.record_id, load_scoring_config())
    assert scored.decision == "review" and scored.reason == "conflicting_records"


@pytest.mark.parametrize("suffix", ["1A", "1-2"])
def test_x2_shipped_unit_one_cannot_gain_address_support_from_another_unit(suffix):
    records = load_records()
    first = records[0]
    address = first.address.removesuffix("1") + suffix
    assert address_strength([first.address, *first.aliases], [address], 0.88) == 0
    result = shortlist(records, text=f"{first.record_id}\nAddress: {address}")
    chosen = next(item for item in result.candidates if item.record_id == first.record_id)
    assert chosen.signals[Signal.ADDRESS] == 0
    assert not result.conflict
    scored = score_choice(result, first.record_id, load_scoring_config())
    assert scored.independent_signals == frozenset({Signal.EXPLICIT_ID})
    assert scored.tier == Tier.MEDIUM and scored.decision == "review"


def test_x2_range_one_two_is_not_shipped_unit_twelve_or_unit_one():
    records = load_records()
    twelve = next(record for record in records if record.record_id == "UNIT-0012")
    address = twelve.address.removesuffix("12") + "1-2"
    result = shortlist(records, text=f"{twelve.record_id}\nAddress: {address}")
    assert [item.record_id for item in result.candidates] == [twelve.record_id]
    assert result.candidates[0].signals[Signal.ADDRESS] == 0
    assert not result.conflict
    assert normalize_address(address) != normalize_address(twelve.address)


def test_x2_shipped_house_number_suffix_does_not_match_numeric_house():
    records = load_records()
    first = records[0]
    house, rest = first.address.split(" ", 1)
    address = f"{house}A {rest}"
    result = shortlist(records, text=f"{first.record_id}\nAddress: {address}")
    assert result.candidates[0].signals[Signal.ADDRESS] == 0
    assert not result.conflict


@pytest.mark.parametrize(
    "address", ["1010 Cedar St #1", "1010 Cedar Street Apt. 1", "1010 Cedar St Unit 1"]
)
def test_x2_shipped_abbreviations_keep_exact_address_support(address):
    records = load_records()
    first = records[0]
    result = shortlist(records, text=f"{first.record_id}\nAddress: {address}")
    assert result.candidates[0].signals[Signal.ADDRESS] == 1
    assert not result.conflict
    assert score_choice(result, first.record_id, load_scoring_config()).decision == "file"
