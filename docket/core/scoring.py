import math
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from docket.config import Signal
from docket.core.candidates import Candidate, CandidateResult

type UnitDecimal = Annotated[Decimal, Field(ge=0, le=1, allow_inf_nan=False)]
QUANTUM = Decimal("0.0000001")


class Weights(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    explicit_id: UnitDecimal
    address: UnitDecimal
    tenant_name: UnitDecimal
    building: UnitDecimal
    filename_id: UnitDecimal

    def by_signal(self) -> dict[Signal, Decimal]:
        return {
            Signal.EXPLICIT_ID: self.explicit_id,
            Signal.ADDRESS: self.address,
            Signal.TENANT_NAME: self.tenant_name,
            Signal.BUILDING: self.building,
            Signal.FILENAME_ID: self.filename_id,
        }


class ScoringConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1]
    calibration: str = Field(min_length=1)
    weights: Weights
    medium_threshold: UnitDecimal
    strong_threshold: UnitDecimal
    auto_file_threshold: UnitDecimal

    @model_validator(mode="after")
    def validate_policy(self) -> Self:
        if sum(self.weights.by_signal().values()) != Decimal(1):
            raise ValueError("Signal weights must sum to 1")
        if not 0 < self.medium_threshold < self.strong_threshold:
            raise ValueError("Thresholds must satisfy 0 < medium < strong <= 1")
        if self.strong_threshold - self.medium_threshold < QUANTUM:
            raise ValueError("Tier thresholds must be separated by at least one score quantum")
        if self.auto_file_threshold <= 0:
            raise ValueError("Auto-file threshold must be positive")
        return self


def load_scoring_config(path: Path = Path("config/scoring.json")) -> ScoringConfig:
    return ScoringConfig.model_validate_json(path.read_text())


class Tier(StrEnum):
    NONE = "none"
    MEDIUM = "medium"
    STRONG = "strong"


class InvalidChoice(ValueError):
    pass


@dataclass(frozen=True)
class ScoreResult:
    record_id: str | None
    score: Decimal
    raw_score: Decimal
    tier: Tier
    independent_signals: frozenset[Signal]
    conflict: bool
    decision: Literal["file", "review"]
    reason: str


def weighted_evidence(
    candidate: Candidate, config: ScoringConfig
) -> tuple[Decimal, frozenset[Signal]]:
    if set(candidate.signals) != set(Signal):
        raise ValueError("Candidate must contain exactly all five signal scores")
    raw_score = Decimal(0)
    independent: set[Signal] = set()
    for signal, weight in config.weights.by_signal().items():
        strength = candidate.signals[signal]
        if not math.isfinite(strength) or not 0 <= strength <= 1:
            raise ValueError("Signal scores must be finite and in [0, 1]")
        if signal == Signal.BUILDING and not candidate.building_is_explicit:
            continue
        raw_score += weight * Decimal(str(strength))
        if strength > 0 and weight > 0:
            independent.add(signal)
    return raw_score.quantize(QUANTUM, rounding=ROUND_DOWN), frozenset(independent)


def score_choice(
    shortlist: CandidateResult, record_id: str | None, config: ScoringConfig
) -> ScoreResult:
    if record_id is None or record_id == "none":
        return ScoreResult(
            None,
            Decimal(0),
            Decimal(0),
            Tier.NONE,
            frozenset(),
            shortlist.conflict,
            "review",
            "no_candidate_chosen",
        )
    candidate = next((item for item in shortlist.candidates if item.record_id == record_id), None)
    if candidate is None:
        raise InvalidChoice("Chosen record must be in the shortlist")
    raw_score, independent = weighted_evidence(candidate, config)
    if shortlist.conflict:
        return ScoreResult(
            record_id,
            Decimal(0),
            raw_score,
            Tier.NONE,
            independent,
            True,
            "review",
            "conflicting_records",
        )
    value = raw_score
    # The numeric cap keeps a threshold-only consumer from treating one signal as strong.
    if len(independent) < 2:
        value = min(value, config.strong_threshold - QUANTUM)
    if value >= config.strong_threshold and len(independent) >= 2:
        tier = Tier.STRONG
    elif value >= config.medium_threshold:
        tier = Tier.MEDIUM
    else:
        tier = Tier.NONE
    can_file = value >= config.auto_file_threshold and tier != Tier.NONE
    return ScoreResult(
        record_id,
        value,
        raw_score,
        tier,
        independent,
        False,
        "file" if can_file else "review",
        "threshold_met" if can_file else "insufficient_corroboration",
    )
