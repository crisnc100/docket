"""The spike with the Anthropic transport, against a fake HTTP layer."""

import json
from decimal import Decimal

import httpx2
from pydantic import SecretStr
from typer.testing import CliRunner

from docket.cli import app
from docket.llm import spike as spike_module
from docket.llm.anthropic import AnthropicTransport, OutputMode
from docket.settings import Settings

ADR = (
    "# ADR 008\n\nStatus: accepted; predictions committed.\n\n## Predictions\n\nP1.\n\n"
    "## Spike\n\nPending.\n\n## Result\n\nPending the live pass.\n"
)


def test_spike_command_without_anthropic_key_makes_no_calls(monkeypatch):
    monkeypatch.setenv("DOCKET_MODEL_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    def forbidden(*args, **kwargs):
        raise AssertionError("no spike may run without a key")

    monkeypatch.setattr("docket.llm.spike.spike", forbidden)
    result = CliRunner().invoke(app, ["spike-models"])
    assert result.exit_code == 0
    assert "PENDING: ANTHROPIC_API_KEY is not set; no live calls made." in result.stdout


async def test_anthropic_spike_makes_two_calls_on_a_five_cent_budget(
    postgres_engine, tmp_path, monkeypatch
):
    bodies = []

    async def api(request):
        body = json.loads(request.content)
        bodies.append(body)
        allowed = body["output_config"]["format"]["schema"]["properties"]["record_id"]["enum"][0]
        return httpx2.Response(
            200,
            json={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": "claude-haiku-5-5",
                "content": [{"type": "text", "text": json.dumps({"record_id": allowed})}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 40, "output_tokens": 8},
            },
        )

    def transport(settings, max_output_tokens):
        return AnthropicTransport(
            settings,
            max_output_tokens,
            httpx2.AsyncClient(transport=httpx2.MockTransport(api)),
            mode=OutputMode(),
        )

    budgets = []
    original = spike_module.RunBudget

    def run_budget(limit):
        budgets.append(limit)
        return original(limit)

    monkeypatch.setattr(spike_module, "create_engine", lambda url: postgres_engine)
    monkeypatch.setattr(spike_module, "AnthropicTransport", transport)
    monkeypatch.setattr(spike_module, "RunBudget", run_budget)
    adr = tmp_path / "008.md"
    adr.write_text(ADR)
    output = tmp_path / "spike.json"
    settings = Settings(
        _env_file=None, anthropic_api_key=SecretStr("test-key"), docket_model_provider="anthropic"
    )
    report = await spike_module.run_spike(settings, output, adr)
    assert budgets == [Decimal("0.05")]
    assert len(bodies) == 2 and report["calls"] == 2
    assert report["provider"] == "anthropic" and report["model"] == "claude-haiku-5-5"
    assert report["output_modes"] == ["json_schema"]
    assert (
        Decimal(report["cost_usd"])
        == 2 * (Decimal(40) * Decimal("0.10") + 8 * Decimal("0.50")) / 10**6
    )
    assert json.loads(output.read_text()) == report
    text = adr.read_text()
    spike_section = text.split("## Spike\n\n")[1].split("## Result")[0]
    assert '"output_modes"' in spike_section and "Pending." not in spike_section
    assert text.endswith("## Result\n\nPending the live pass.\n")
    assert "Status: accepted; predictions committed." in text
