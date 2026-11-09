import asyncio
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import create_engine

from docket.llm.budget import Budget, DailyBudget, PriceCeiling, RunBudget
from docket.llm.gateway import Gateway, Mode, OpenRouterTransport
from docket.llm.replay import ReplayStore
from docket.settings import Settings


class FirstEnum(BaseModel):
    model_config = ConfigDict(extra="forbid")
    record_id: Literal["UNIT-0001", "none"]


class SecondEnum(BaseModel):
    model_config = ConfigDict(extra="forbid")
    record_id: Literal["UNIT-0199", "none"]


async def run_spike(settings: Settings, output: Path, adr_path: Path) -> dict[str, object]:
    engine = create_engine(settings.database_url)
    run = RunBudget(Decimal("0.50"))
    try:
        gateway = Gateway(
            mode=Mode.LIVE,
            model=settings.docket_model,
            store=ReplayStore(Path(".cache/spike-unused.jsonl")),
            budget=Budget(run, DailyBudget(engine, Decimal(str(settings.docket_daily_budget_usd)))),
            transport=OpenRouterTransport(settings, PriceCeiling(), 64),
            max_output_tokens=64,
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
        report = {
            "status": "PASS",
            "model": settings.docket_model,
            "provider": settings.docket_provider,
            "cost_usd": str(run.spent),
            "calls": gateway.call_count,
            "results": results,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n")
        adr = adr_path.read_text()
        adr = re.sub(
            r"^Status:.*$",
            "Status: accepted — measured enum spike passed.",
            adr,
            count=1,
            flags=re.MULTILINE,
        )
        measured = (
            "## Result\n\nMeasured by `uv run docket spike-models`:\n\n```json\n"
            + json.dumps(report, indent=2)
            + "\n```\n\n"
        )
        adr, replacements = re.subn(
            r"## Result\n.*?(?=## Decision)", lambda _: measured, adr, count=1, flags=re.DOTALL
        )
        if replacements != 1:
            raise ValueError("ADR is missing its result section; JSON measurement was saved")
        adr_path.write_text(adr)
        return report
    finally:
        engine.dispose()


def spike(settings: Settings, output: Path) -> dict[str, object]:
    return asyncio.run(run_spike(settings, output, Path("docs/adr/001-model-enum-spike.md")))
