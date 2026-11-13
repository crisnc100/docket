"""Diagnostic only: send each refused live request once more and keep the full response.

Results go to builds/v02/refusal-resend.jsonl, never to the live archive, so they count
toward no gate. A hard $0.05 budget is checked against each call's worst case before
it is sent.
"""

import asyncio
import json
import sys
import time
from decimal import Decimal
from pathlib import Path

import anthropic

from docket.llm.anthropic import AnthropicTransport
from docket.llm.budget import load_prices
from docket.llm.replay import ReplayStore, canonical, replay_key
from docket.prompts import choose_request, classify_request
from docket.settings import Settings

BUDGET = Decimal("0.05")
OUT = Path("builds/v02/refusal-resend.jsonl")


async def main() -> None:
    settings = Settings(docket_model_provider="anthropic", docket_model="claude-haiku-5-5")
    prices = load_prices().ceiling(settings.docket_model)
    transport = AnthropicTransport(settings, 256)
    client = anthropic.AsyncAnthropic(
        api_key=settings.anthropic_api_key.get_secret_value(), max_retries=0, timeout=15.0
    )
    refused = [
        entry
        for entry in ReplayStore(Path(settings.docket_replay_path)).entries()
        if entry.outcome == "unreadable"
    ]
    reserved = Decimal(0)
    spent = Decimal(0)
    rows = []
    for entry in refused:
        pending = json.loads(Path(f"evals/pending/{entry.key}.json").read_text())
        inputs = pending["inputs"]
        call = (
            classify_request(inputs["text"])
            if pending["activity"] == "classify"
            else choose_request(inputs["text"], inputs["ids"])
        )
        request = {
            "inputs": call["inputs"],
            "prompt": call["prompt"],
            "schema": call["schema"].model_json_schema(),
            "system_prompt": call["system_prompt"],
        }
        key = replay_key(call["activity"], call["prompt_version"], settings.docket_model, request)
        if key != entry.key:
            sys.exit(f"Rebuilt request does not match its key: {entry.key}")
        worst = prices.worst_case(canonical(request), 256)
        if reserved + worst > BUDGET:
            sys.exit(f"Stopping: next call would pass the ${BUDGET} diagnostic budget")
        reserved += worst
        body = transport.body(call["prompt"], call["schema"], call["system_prompt"])
        started = time.monotonic()
        message = await client.messages.create(**body)
        latency_ms = round((time.monotonic() - started) * 1000)
        usage = message.usage
        cost = prices.cost(usage.input_tokens, usage.output_tokens)
        spent += cost
        reserved += cost - worst
        metadata = message.model_dump(mode="json")
        rows.append(
            {
                "key": entry.key,
                "activity": call["activity"],
                "first_reason": entry.reason,
                "first_input_tokens": entry.input_tokens,
                "first_output_tokens": entry.output_tokens,
                "stop_reason": message.stop_reason,
                "refused_again": message.stop_reason == "refusal",
                "cost_usd": str(cost),
                "latency_ms": latency_ms,
                "response": metadata,
            }
        )
    OUT.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
    again = sum(row["refused_again"] for row in rows)
    print(f"Resent {len(rows)} refused requests: {again} refused again; spent ${spent}")


if __name__ == "__main__":
    asyncio.run(main())
