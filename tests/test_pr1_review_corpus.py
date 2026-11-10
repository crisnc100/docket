import csv
from collections import Counter, defaultdict

import pytest

from docket.config import load_records
from docket.core.candidates import normalize_text, shortlist
from docket.core.scoring import load_scoring_config, score_choice
from docket.corpus import CASE_TYPES, generate
from scripts.unique_tenants import unique_tenants


def write_records(path, records):
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=records[0].model_dump().keys())
        writer.writeheader()
        for record in records:
            row = record.model_dump()
            for field in ("current_tenants", "former_tenants", "aliases"):
                row[field] = "|".join(row[field])
            writer.writerow(row)


@pytest.mark.parametrize("change", ["empty_former", "single_unit_building"])
def test_corpus_selects_eligible_records_for_each_case(tmp_path, change):
    records = load_records()
    index = 93 if change == "empty_former" else 54
    updates = {"former_tenants": []} if change == "empty_former" else {"building": "SOLO"}
    records[index] = records[index].model_copy(update=updates)
    source = tmp_path / "records.csv"
    write_records(source, records)
    by_id = {record.record_id: record for record in load_records(source)}
    labels = generate(7, tmp_path / "corpus", source)
    assert len([label for label in labels if label.case_type != "graded_evidence"]) == 170
    assert len(labels) == 180
    assert Counter(label.case_type for label in labels) == dict.fromkeys(CASE_TYPES, 10)
    for label in labels:
        if label.case_type == "former_tenant":
            assert by_id[label.record_id].former_tenants
        elif label.case_type == "decoy_near_miss_address":
            record = by_id[label.record_id]
            assert any(
                other.record_id != record.record_id and other.building == record.building
                for other in records
            )


@pytest.mark.parametrize("missing", ["former", "neighbour"])
def test_corpus_rejects_missing_case_eligibility_before_writing(tmp_path, missing):
    records = load_records()[:2]
    records = [
        record.model_copy(
            update={"former_tenants": []} if missing == "former" else {"building": f"SOLO_{index}"}
        )
        for index, record in enumerate(records)
    ]
    source = tmp_path / "records.csv"
    write_records(source, records)
    output = tmp_path / "corpus"
    with pytest.raises(ValueError, match=missing):
        generate(7, output, source)
    assert not output.exists()


def test_shipped_tenant_names_belong_to_only_one_unit():
    owners = defaultdict(set)
    for record in load_records():
        for name in record.current_tenants + record.former_tenants:
            owners[normalize_text(name)].add(record.record_id)
    assert {name: units for name, units in owners.items() if len(units) > 1} == {}


@pytest.mark.parametrize("record_id", ["UNIT-0197", "UNIT-0198"])
def test_shipped_shared_name_cases_file_with_agreeing_id(record_id):
    records = load_records()
    record = next(record for record in records if record.record_id == record_id)
    name = record.current_tenants[0] if record_id == "UNIT-0197" else record.former_tenants[0]
    result = shortlist(records, text=f"{record.record_id}\n{record.address}\nTenant: {name}")
    assert not result.conflict
    assert score_choice(result, record_id, load_scoring_config()).decision == "file"


def test_tenant_regeneration_is_deterministic_and_keeps_unrelated_fields(tmp_path):
    records = load_records()[:2]
    records[1] = records[1].model_copy(update={"former_tenants": records[0].current_tenants})
    paths = [tmp_path / "first.csv", tmp_path / "second.csv"]
    for path in paths:
        write_records(path, records)
        unique_tenants(path)
    assert paths[0].read_bytes() == paths[1].read_bytes()
    first_pass = paths[0].read_bytes()
    unique_tenants(paths[0])
    assert paths[0].read_bytes() == first_pass
    updated = load_records(paths[0])
    assert updated[0] == records[0]
    assert updated[1].former_tenants != records[1].former_tenants
    assert updated[1].model_dump(exclude={"former_tenants"}) == records[1].model_dump(
        exclude={"former_tenants"}
    )
