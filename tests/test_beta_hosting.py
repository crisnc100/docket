"""Running the hosted beta (plan PR C): the site-root rule, the gateway cache, the image,
the example settings and the production compose file (C2)."""

import os
import re
import secrets
import subprocess
import sys
import uuid
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from docket import runtime as runtime_module
from docket.api import create_app
from docket.beta import UnsafeConfig, refuse_unsafe_start, startup_problems
from docket.runtime import MAX_GATEWAYS, Runtime
from docket.settings import Settings
from docket.worker import run_worker
from tests.test_beta_gate import (
    BASE,
    FakeRuntime,
    FakeTemporal,
    StartedRuntime,
    beta_settings,
    signed_in,
)

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("DOCKET_FAILPOINT", "DOCKET_DAILY_BUDGET_USD", "UVICORN_")):
            monkeypatch.delenv(name)


# 4b: the beta is served at the site root only


@pytest.mark.parametrize("path", ["/healthz", "/", "/home/home.css", "/review", "/reviewer"])
async def test_beta_refuses_every_request_under_a_root_path(path):
    app = create_app(StartedRuntime(beta_settings()), FakeTemporal())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app, root_path="/home")
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as http:
            response = await http.get(path, headers=signed_in())
    assert response.status_code == 500
    assert response.text == "The beta must be served at the site root."


async def test_local_mode_still_serves_under_a_root_path():
    settings = Settings(_env_file=None, docket_review_token=SecretStr("t" * 20))
    app = create_app(StartedRuntime(settings), FakeTemporal())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app, root_path="/docket")
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as http:
            assert (await http.get("/healthz")).json() == {"status": "ok"}


async def test_a_root_path_setting_stops_every_beta_process(monkeypatch):
    monkeypatch.setenv("UVICORN_ROOT_PATH", "/docket")
    problems = startup_problems(beta_settings(), os.environ)
    assert problems == ["UVICORN_ROOT_PATH must be unset: the beta is served at the site root."]
    with pytest.raises(UnsafeConfig):
        refuse_unsafe_start(beta_settings())
    app = create_app(FakeRuntime(beta_settings()), FakeTemporal())
    with pytest.raises(UnsafeConfig):
        async with app.router.lifespan_context(app):
            pass
    with pytest.raises(UnsafeConfig):
        await run_worker(FakeRuntime(beta_settings()))
    assert startup_problems(Settings(_env_file=None), os.environ) == []


# Build 2: the worker's gateways are bounded


def test_the_worker_keeps_a_bounded_number_of_gateways_and_one_transport(monkeypatch):
    built = []

    def transport(settings, max_output_tokens):
        built.append(settings.docket_model)
        return object()

    monkeypatch.setattr(runtime_module, "AnthropicTransport", transport)
    runtime = Runtime(
        Settings(_env_file=None, anthropic_api_key=SecretStr("test-key"), s3_bucket="unused")
    )
    try:

        def request(document_id):
            return {
                "document_id": document_id,
                "run_id": "live",
                "model_mode": "live",
                "model": "claude-haiku-5-5",
                "budget": "1.00",
            }

        first = runtime.gateway(request("first"))
        for index in range(MAX_GATEWAYS + 50):
            runtime.gateway(request(str(index)))
            # A document still being read keeps its gateway while newer ones arrive.
            assert runtime.gateway(request("first")) is first
        assert len(runtime.gateways) == MAX_GATEWAYS
        assert runtime.gateway(request("0")) is not None and len(runtime.gateways) == MAX_GATEWAYS
        assert built == ["claude-haiku-5-5"]
        assert len({id(gateway.transport) for gateway in runtime.gateways.values()}) == 1
    finally:
        runtime.close()


# Build 5: the image


def test_the_image_pins_its_bases_runs_the_app_unprivileged_and_ships_the_example():
    dockerfile = (ROOT / "Dockerfile").read_text()
    bases = re.findall(r"^FROM (\S+)", dockerfile, re.MULTILINE)
    pinned = [base for base in bases if "@sha256:" in base]
    assert len(pinned) == 2 and all(re.search(r"@sha256:[0-9a-f]{64}$", base) for base in pinned)
    assert set(bases) - set(pinned) == {"web-deps"}
    stages = re.split(r"^FROM ", dockerfile, flags=re.MULTILINE)
    browser = next(stage for stage in stages if " AS browser" in stage.splitlines()[0])
    app = stages[-1]
    assert "USER" not in browser
    assert app.rstrip().splitlines()[-2] == "USER docket"
    ignored = (ROOT / ".dockerignore").read_text().splitlines()
    assert ignored.index("!.env.beta.example") > ignored.index(".env*")


# Build 4: the example settings


def example_values() -> dict[str, str]:
    values = {}
    for line in (ROOT / ".env.beta.example").read_text().splitlines():
        name, sep, value = line.partition("=")
        if sep and not line.startswith("#"):
            values[name] = value
    return values


def test_the_example_names_every_beta_setting_and_its_placeholders_are_refused():
    values = example_values()
    assert values["DOCKET_DAILY_BUDGET_USD"] == "1.00"
    assert values["DOCKET_TRUST_PROXY"] == "1"
    assert values["DOCKET_REVIEW_TIMEOUT_SECONDS"] == "86400"
    assert values["DOCKET_MODEL_MODE"] == "live"
    assert "DOCKET_REVIEW_TOKEN" not in values
    compose = (ROOT / "compose.prod.yaml").read_text()
    assert set(re.findall(r"\$\{(\w+)", compose)) == set(values)
    filled = {name: value for name, value in values.items() if value}
    settings = Settings(
        _env_file=None,
        **{name.lower(): value for name, value in filled.items() if name != "DOCKET_PORT"},
        docket_env="beta",
    )
    problems = startup_problems(settings, {})
    assert "An invite code is a placeholder from .env.beta.example." in problems
    assert any(problem.startswith("DOCKET_SESSION_KEY") for problem in problems)


# C2: the production compose file, rendered by a script that prints no values


def real_env_file(tmp_path: Path) -> tuple[Path, list[str]]:
    secrets_used = []
    lines = []
    for name, value in example_values().items():
        if name in {"DOCKET_INVITE_CODES", "DOCKET_SESSION_KEY", "ANTHROPIC_API_KEY"} or (
            name in {"POSTGRES_PASSWORD", "S3_ACCESS_KEY", "S3_SECRET_KEY"}
        ):
            value = "secret" + secrets.token_hex(16)
            secrets_used.append(value)
        lines.append(f"{name}={value}")
    path = tmp_path / ".env.beta"
    path.write_text("\n".join(lines) + "\n")
    return path, secrets_used


def check(env_file: Path, compose: Path | None = None) -> subprocess.CompletedProcess:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in example_values() and not key.startswith("DOCKET_")
    }
    return subprocess.run(
        [sys.executable, "scripts/check_prod_compose.py", str(env_file)],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=compose or ROOT,
        env=environment,
    )


def test_the_compose_check_passes_and_prints_no_value(tmp_path):
    env_file, values = real_env_file(tmp_path)
    result = check(env_file)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.startswith("OK: 8 services.")
    for value in values:
        assert value not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (('      - "127.0.0.1:${DOCKET_PORT', '      - "0.0.0.0:${DOCKET_PORT'), "the api port"),
        (
            (
                "    mem_limit: 512m\n    cpus: 1.0\n    logging: *logging\n    healthcheck:\n"
                '      test: ["CMD-SHELL"',
                '    cpus: 1.0\n    logging: *logging\n    healthcheck:\n      test: ["CMD-SHELL"',
            ),
            "postgres has no memory or CPU limit",
        ),
        (
            (
                "    environment: *docket-environment\n    ports:",
                "    environment:\n"
                "      <<: *docket-environment\n      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}\n"
                "    ports:",
            ),
            "api receives the model key",
        ),
        (
            ('"$$S3_ACCESS_KEY" "$$S3_SECRET_KEY"', '"${S3_ACCESS_KEY}" "${S3_SECRET_KEY}"'),
            "seaweedfs has S3_SECRET_KEY on its command line",
        ),
    ],
)
def test_the_compose_check_names_each_broken_rule_without_its_value(tmp_path, change, problem):
    env_file, values = real_env_file(tmp_path)
    text = (ROOT / "compose.prod.yaml").read_text()
    assert change[0] in text
    (tmp_path / "compose.prod.yaml").write_text(text.replace(change[0], change[1], 1))
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "check_prod_compose.py").write_bytes(
        (ROOT / "scripts" / "check_prod_compose.py").read_bytes()
    )
    (tmp_path / "Dockerfile").write_text("FROM scratch\n")
    result = check(env_file, tmp_path)
    assert result.returncode == 1
    assert problem in result.stdout
    for value in values:
        assert value not in result.stdout + result.stderr


def test_the_compose_check_refuses_settings_shadowed_by_the_shell(tmp_path):
    env_file, _ = real_env_file(tmp_path)
    environment = {key: value for key, value in os.environ.items() if key != "DOCKET_PORT"}
    result = subprocess.run(
        [sys.executable, "scripts/check_prod_compose.py", str(env_file)],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=ROOT,
        env=environment | {"DOCKET_PORT": "9" + uuid.uuid4().hex[:3]},
    )
    assert result.returncode == 1 and "DOCKET_PORT" in result.stderr
