from decimal import Decimal
from pathlib import Path

import pytest

from docket.llm.budget import PriceCeiling, load_prices

SNAPSHOT = Path("config/model_prices.yaml")


def test_snapshot_has_date_source_and_haiku_prices():
    prices = load_prices(SNAPSHOT)
    assert prices.snapshot_date == "2026-11-12"
    assert prices.source.startswith("https://")
    haiku = prices.ceiling("claude-haiku-5-5")
    assert haiku.prompt_per_million == Decimal("0.10")
    assert haiku.completion_per_million == Decimal("0.50")
    assert haiku.long_prompt_tokens == 100_000
    assert haiku.long_prompt_per_million == Decimal("0.50")
    assert haiku.long_completion_per_million == Decimal("2.50")


def test_openrouter_ceiling_keeps_the_v01_values():
    assert load_prices(SNAPSHOT).ceiling("openai/gpt-4o-mini") == PriceCeiling()


def test_unknown_model_has_no_price():
    with pytest.raises(KeyError, match="no price"):
        load_prices(SNAPSHOT).ceiling("claude-unknown")


def test_cost_is_tokens_times_snapshot_prices():
    haiku = load_prices(SNAPSHOT).ceiling("claude-haiku-5-5")
    assert haiku.cost(1_000, 10) == Decimal("0.000105")
    assert haiku.cost(0, 0) == Decimal(0)
    # Above the long-prompt line every token bills at the long prices.
    assert haiku.cost(100_001, 10) == Decimal("0.0500255")
    with pytest.raises(ValueError):
        haiku.cost(-1, 0)


def test_worst_case_uses_snapshot_prices_and_long_tier():
    haiku = load_prices(SNAPSHOT).ceiling("claude-haiku-5-5")
    # 1 byte + 4096 framing tokens of input, 256 tokens of output.
    assert haiku.worst_case("a", 256) == Decimal("0.0005377")
    long = "a" * 100_000
    assert haiku.worst_case(long, 256) == (
        Decimal(104_096) * Decimal("0.50") + Decimal(256) * Decimal("2.50")
    ) / Decimal(1_000_000)


def test_snapshot_rejects_bad_prices(tmp_path):
    path = tmp_path / "prices.yaml"
    path.write_text(
        'snapshot_date: "2026-11-12"\nsource: https://example.test\nmodels:\n'
        "  m:\n    provider: anthropic\n    kind: list_price\n"
        '    input_per_million: "-1"\n    output_per_million: "0.5"\n'
    )
    with pytest.raises(ValueError):
        load_prices(path)
