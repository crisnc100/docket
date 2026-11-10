import csv
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Category(StrEnum):
    LEASE = "lease"
    INSPECTION = "inspection"
    INVOICE = "invoice"
    NOTICE = "notice"
    MISC = "misc"


class Signal(StrEnum):
    EXPLICIT_ID = "explicit_id"
    ADDRESS = "address"
    TENANT_NAME = "tenant_name"
    BUILDING = "building"
    FILENAME_ID = "filename_id"


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")
    record_id: str = Field(pattern=r"^UNIT-[0-9]{4}$")
    address: str = Field(min_length=1)
    building: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    current_tenants: list[str] = Field(min_length=1)
    former_tenants: list[str]
    aliases: list[str]


class CategoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1)
    required_signals: list[Signal]


class CategoriesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    categories: dict[Category, CategoryConfig]

    @model_validator(mode="after")
    def all_categories(self) -> Self:
        if set(self.categories) != set(Category):
            raise ValueError("Config must define exactly all five categories")
        return self


def load_records(path: Path = Path("config/records.csv")) -> list[Record]:
    with path.open(newline="") as file:
        records = [
            Record.model_validate(
                row
                | {
                    column: row[column].split("|") if row[column] else []
                    for column in ["current_tenants", "former_tenants", "aliases"]
                }
            )
            for row in csv.DictReader(file)
        ]
    if len({record.record_id for record in records}) != len(records):
        raise ValueError("Duplicate record_id in config")
    return records


def load_categories(path: Path = Path("config/categories.yaml")) -> CategoriesConfig:
    return CategoriesConfig.model_validate(yaml.safe_load(path.read_text()))
