from enum import Enum
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, create_model, field_validator

from docket.config import Category


class Classification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Category

    @field_validator("category", mode="before")
    @classmethod
    def unknown_is_misc(cls, value: Any) -> Any:
        return value if value in {category.value for category in Category} else "misc"


def choice_schema(ids: list[str]) -> type[BaseModel]:
    choices = cast(
        type[Enum],
        Enum("RecordChoice", {f"choice_{i}": value for i, value in enumerate([*ids, "none"])}),
    )
    return create_model(
        "CandidateChoice", __config__=ConfigDict(extra="forbid"), record_id=(choices, ...)
    )
