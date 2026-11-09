from typer.testing import CliRunner

from docket.cli import app
from docket.llm.spike import FirstEnum, SecondEnum


def test_spike_command_without_key_makes_no_calls(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    result = CliRunner().invoke(app, ["spike-models"])
    assert result.exit_code == 0
    assert "PENDING" in result.stdout
    assert "no live calls" in result.stdout


def test_spike_schemas_have_distinct_per_call_enums():
    first = FirstEnum.model_json_schema()["properties"]["record_id"]["enum"]
    second = SecondEnum.model_json_schema()["properties"]["record_id"]["enum"]
    assert first == ["UNIT-0001", "none"]
    assert second == ["UNIT-0199", "none"]


async def test_spike_writes_measured_adr_from_usage_without_live_model(
    postgres_engine, tmp_path, monkeypatch
):
    import json
    from decimal import Decimal

    from langchain_core.messages import AIMessage
    from pydantic import SecretStr

    from docket.llm import spike as spike_module
    from docket.settings import Settings

    async def transport(prompt, schema):
        allowed = schema.model_json_schema()["properties"]["record_id"]["enum"][0]
        return {
            "raw": AIMessage(
                content=json.dumps({"record_id": allowed}),
                response_metadata={"token_usage": {"cost": "0.00001"}},
            ),
            "parsed": {"record_id": allowed},
        }

    monkeypatch.setattr(spike_module, "create_engine", lambda url: postgres_engine)
    monkeypatch.setattr(spike_module, "OpenRouterTransport", lambda *args: transport)
    adr = tmp_path / "adr.md"
    adr.write_text("Status: PENDING\n\n## Result\n\nPENDING\n\n## Decision\n\nValidate enums.\n")
    output = tmp_path / "measurement.json"
    report = await spike_module.run_spike(
        Settings(_env_file=None, openrouter_api_key=SecretStr("test-key")), output, adr
    )
    assert report["status"] == "PASS" and report["calls"] == 2
    assert Decimal(report["cost_usd"]) == Decimal("0.00002")
    assert json.loads(output.read_text()) == report
    assert [item["record_id"] for item in report["results"]] == ["UNIT-0001", "UNIT-0199"]
    assert "PENDING" not in adr.read_text()
    assert "0.00002" in adr.read_text() and "Validate enums." in adr.read_text()
