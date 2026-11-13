"""D28: the committed cost summary is rebuilt from the recordings and the price snapshot."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from docket.cost_summary import build_summary
from docket.llm.replay import Recording

TOLERANCE = Decimal("0.000001")


def split(value, path=""):
    """Money fields (names ending in _usd) apart from everything else."""
    money, rest = {}, {}
    for key, item in value.items():
        name = f"{path}.{key}"
        if key.endswith("_usd") and isinstance(item, dict):
            money.update({f"{name}.{k}": v for k, v in item.items()})
        elif key.endswith("_usd"):
            money[name] = item
        elif isinstance(item, dict):
            inner_money, rest[key] = split(item, name)
            money.update(inner_money)
        else:
            rest[key] = item
    return money, rest


def test_committed_summary_matches_the_recordings_and_prices():
    [path] = sorted(Path("evals/cost").glob("*-claude-haiku-5-5.json"))
    committed = json.loads(path.read_text())
    rebuilt = json.loads(json.dumps(build_summary()))
    assert path.name == f"{rebuilt['recorded'][-1]}-claude-haiku-5-5.json"
    committed_money, committed_rest = split(committed)
    rebuilt_money, rebuilt_rest = split(rebuilt)
    assert committed_rest == rebuilt_rest
    assert committed_money.keys() == rebuilt_money.keys()
    assert ".total.spend_usd" in committed_money
    for name, value in committed_money.items():
        assert value is not None, name
        assert abs(Decimal(value) - Decimal(rebuilt_money[name])) <= TOLERANCE, name


def test_a_recorded_cost_that_differs_from_tokens_times_prices_is_refused(tmp_path):
    source = Path("evals/recordings/anthropic.jsonl")
    lines = source.read_text().splitlines()
    first = Recording.model_validate_json(lines[0])
    changed = first.model_copy(
        update={"cost_usd": str(Decimal(first.cost_usd or "0") + Decimal("0.001"))}
    )
    target = tmp_path / "anthropic.jsonl"
    target.write_text("\n".join([changed.line(), *lines[1:]]) + "\n")
    with pytest.raises(ValueError, match="Recorded cost differs"):
        build_summary(recordings=target)
