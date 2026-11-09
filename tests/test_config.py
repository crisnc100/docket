import csv
from pathlib import Path

import pytest
from pydantic import ValidationError

from docket.config import Category, Record, load_categories, load_records


def test_records_config_has_real_units_names_and_safe_components():
    records = load_records()
    assert len(records) == 200
    assert len({record.record_id for record in records}) == 200
    assert len({record.address for record in records}) == 200
    assert {record.record_id for record in records} == {f"UNIT-{i:04d}" for i in range(1, 201)}
    assert len({record.building for record in records}) == 10
    for record in records:
        assert record.current_tenants and record.former_tenants and record.aliases
        assert record.current_tenants != record.former_tenants
        assert record.address not in record.aliases


def test_categories_are_versioned_and_require_known_signals():
    config = load_categories()
    assert config.schema_version == 1
    assert set(config.categories) == set(Category)
    assert all(value.required_signals for value in config.categories.values())


@pytest.mark.parametrize(
    "changes", [{"record_id": "../UNIT-0001"}, {"building": "../cedar"}, {"current_tenants": []}]
)
def test_invalid_record_config_is_rejected(changes):
    record = load_records()[0].model_dump() | changes
    with pytest.raises(ValidationError):
        Record.model_validate(record)


def test_duplicate_ids_are_rejected(tmp_path):
    source = Path("config/records.csv")
    with source.open() as file:
        rows = list(csv.DictReader(file))
    output = tmp_path / "records.csv"
    with output.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows([rows[0], rows[0]])
    with pytest.raises(ValueError, match="Duplicate"):
        load_records(output)


@pytest.mark.parametrize(
    "text", ["schema_version: 2\ncategories: {}", "schema_version: 1\ncategories: {}"]
)
def test_invalid_category_config_rejected(tmp_path, text):
    path = tmp_path / "categories.yaml"
    path.write_text(text)
    with pytest.raises(ValidationError):
        load_categories(path)
