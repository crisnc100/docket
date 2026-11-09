from decimal import Decimal

import pytest

from docket.config import Signal, load_records
from docket.core.candidates import shortlist
from docket.core.scoring import Tier, load_scoring_config, score_choice


@pytest.mark.parametrize(
    "signals,expected_score,expected_decision",
    [
        ((Signal.EXPLICIT_ID, Signal.TENANT_NAME), ".55", "review"),
        ((Signal.ADDRESS, Signal.TENANT_NAME), ".55", "review"),
        ((Signal.EXPLICIT_ID, Signal.ADDRESS), ".60", "file"),
    ],
)
def test_two_signal_rows_use_unmodified_shipped_config(signals, expected_score, expected_decision):
    config = load_scoring_config()
    record = load_records()[0]
    lines = {
        Signal.EXPLICIT_ID: record.record_id,
        Signal.ADDRESS: record.address,
        Signal.TENANT_NAME: f"Tenant: {record.current_tenants[0]}",
    }
    candidates = shortlist([record], text="\n".join(lines[signal] for signal in signals))
    result = score_choice(candidates, record.record_id, config)
    assert config.strong_threshold == Decimal(".55")
    assert config.auto_file_threshold == Decimal(".5857142")
    assert result.score == result.raw_score == Decimal(expected_score)
    assert result.tier == Tier.STRONG
    assert result.independent_signals == frozenset(signals)
    assert result.decision == expected_decision
