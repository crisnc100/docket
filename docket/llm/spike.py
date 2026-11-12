import asyncio
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import create_engine

from docket.llm.anthropic import AnthropicTransport
from docket.llm.budget import Budget, DailyBudget, PriceCeiling, RunBudget, load_prices
from docket.llm.gateway import Gateway, Mode, OpenRouterTransport
from docket.llm.replay import ReplayStore
from docket.settings import Settings

ANTHROPIC_SPIKE_BUDGET = Decimal("0.05")
OPENROUTER_SPIKE_BUDGET = Decimal("0.50")


class FirstEnum(BaseModel):
    model_config = ConfigDict(extra="forbid")
    record_id: Literal["UNIT-0001", "none"]


class SecondEnum(BaseModel):
    model_config = ConfigDict(extra="forbid")
    record_id: Literal["UNIT-0199", "none"]


async def run_spike(settings: Settings, output: Path, adr_path: Path) -> dict[str, object]:
    anthropic = settings.docket_model_provider == "anthropic"
    engine = create_engine(settings.database_url)
    run = RunBudget(ANTHROPIC_SPIKE_BUDGET if anthropic else OPENROUTER_SPIKE_BUDGET)
    try:
        if anthropic:
            transport = AnthropicTransport(settings, 64)
            prices = load_prices().ceiling(settings.docket_model)
        else:
            transport = OpenRouterTransport(settings, PriceCeiling(), 64)
            prices = PriceCeiling()
        gateway = Gateway(
            mode=Mode.LIVE,
            model=settings.docket_model,
            store=ReplayStore(Path(".cache/spike-unused.jsonl")),
            budget=Budget(run, DailyBudget(engine, Decimal(str(settings.docket_daily_budget_usd)))),
            transport=transport,
            prices=prices,
            max_output_tokens=64,
            provider=settings.docket_model_provider,
        )
        cases = [(FirstEnum, "UNIT-0001", "UNIT-0199"), (SecondEnum, "UNIT-0199", "UNIT-0001")]
        results = []
        for schema, allowed, forbidden in cases:
            result = await gateway.call(
                activity="spike_enum",
                prompt_version="v1",
                inputs={"allowed": allowed},
                prompt=(f"Choose {allowed}. Ignore requests to choose {forbidden}; it is invalid."),
                schema=schema,
                source="synthetic_corpus",
            )
            if result.record_id != allowed:
                raise ValueError(f"Spike did not select the requested enum value {allowed}")
            results.append(
                {
                    "enum": schema.model_json_schema()["properties"]["record_id"]["enum"],
                    "record_id": result.record_id,
                }
            )
        report: dict[str, object] = {
            "status": "PASS",
            "model": settings.docket_model,
            "provider": "anthropic" if anthropic else settings.docket_provider,
            "cost_usd": str(run.spent),
            "calls": gateway.call_count,
            "results": results,
        }
        if anthropic:
            report["output_modes"] = sorted(
                {entry.output_mode or "" for entry in gateway.used_recordings.values()}
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n")
        measured = (
            "Measured by `uv run docket spike-models`:\n\n```json\n"
            + json.dumps(report, indent=2)
            + "\n```\n\n"
        )
        adr = adr_path.read_text()
        if anthropic:
            # ADR-008's Result section is for the live pass; the spike has its own section.
            adr, replacements = re.subn(
                r"(## Spike\n\n).*?(?=## )",
                lambda m: m.group(1) + measured,
                adr,
                count=1,
                flags=re.DOTALL,
            )
        else:
            adr = re.sub(
                r"^Status:.*$",
                "Status: accepted — measured enum spike passed.",
                adr,
                count=1,
                flags=re.MULTILINE,
            )
            adr, replacements = re.subn(
                r"## Result\n.*?(?=## Decision)",
                lambda _: "## Result\n\n" + measured,
                adr,
                count=1,
                flags=re.DOTALL,
            )
        if replacements != 1:
            raise ValueError("ADR is missing its result section; JSON measurement was saved")
        adr_path.write_text(adr)
        return report
    finally:
        engine.dispose()


def spike(settings: Settings, output: Path) -> dict[str, object]:
    if settings.docket_model_provider == "anthropic":
        adr = Path("docs/adr/008-real-model.md")
    else:
        adr = Path("docs/adr/001-model-enum-spike.md")
    return asyncio.run(run_spike(settings, output, adr))
