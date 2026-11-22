"""The hosted beta's gate: the route table, closed write paths, the Origin check, the body
cap and the startup refusals (plan PR A, A1 to A6, as sandbox PR 1 leaves them)."""

import asyncio
import json
import math
import os
import secrets
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.routing import Mount

from docket.api import create_app
from docket.beta import UnsafeConfig, client_address, refuse_unsafe_start, startup_problems
from docket.db import Document, Event, Sandbox
from docket.sandbox import SANDBOX_COOKIE, token_hash
from docket.settings import Settings
from docket.sweep import record_complete
from docket.worker import run_worker
from tests.sandboxes import held_in, make_sandbox, sandbox_cookie

BASE = "https://docket.example.test"
KEY = secrets.token_hex(32)
# The visitor's sandbox cookie. Its sandbox exists only in tests that make it (open_sandbox).
SANDBOX_TOKEN = secrets.token_urlsafe(16)
PUBLIC = {
    ("GET", "/"),
    ("GET", "/home/{path:path}"),
    ("GET", "/healthz"),
    ("GET", "/reviewer"),
    ("GET", "/assets/{path:path}"),
}
SANDBOX_SCOPED = {
    ("GET", "/review"),
    ("POST", "/review/{document_id}/{action}"),
    ("GET", "/documents/{document_id}"),
    ("GET", "/documents/{document_id}/file"),
    ("GET", "/beta/samples"),
    ("POST", "/beta/samples"),
    ("GET", "/sandbox"),
    ("DELETE", "/sandbox"),
}
CLOSED = {("POST", "/documents"), ("POST", "/inbound")}
BUILT = Path(__file__).parent.parent / "web" / "dist"
BUILT_ASSETS = BUILT / "assets"
# FastAPI's own pages. The beta serves none of them: each gets the gate's 404.
REFUSED = {
    (method, path)
    for method in ("GET", "HEAD")
    for path in ("/docs", "/docs/oauth2-redirect", "/openapi.json", "/redoc")
}


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in list(os.environ):
        # An address key in the shell would fill the "address key missing" case.
        if name.startswith(("DOCKET_FAILPOINT", "DOCKET_DAILY_BUDGET_USD", "DOCKET_ADDRESS_KEY")):
            monkeypatch.delenv(name)


def beta_settings(drop: tuple[str, ...] = (), **overrides) -> Settings:
    values = {
        "docket_env": "beta",
        "docket_address_key": SecretStr(KEY),
        "docket_trust_proxy": True,
        "docket_base_url": BASE,
        "docket_daily_budget_usd": 1.00,
        # Set here, so a longer timeout in the shell never makes the safe settings unsafe.
        "docket_review_timeout_seconds": 3000,
    } | overrides
    return Settings(_env_file=None, **{key: values[key] for key in values if key not in drop})


class FakeRuntime:
    def __init__(self, settings: Settings, engine=None):
        self.settings, self.engine = settings, engine

    def ensure_bucket(self):
        raise AssertionError("A refused process must stop before touching storage")

    def close(self):
        pass


class StartedRuntime(FakeRuntime):
    def ensure_bucket(self):
        pass


class FakeTemporal:
    def __init__(self):
        self.updates = []

    def get_workflow_handle(self, workflow_id):
        temporal = self

        class Handle:
            async def execute_update(self, name, value):
                temporal.updates.append((workflow_id, name, value))
                return True

        return Handle()


@asynccontextmanager
async def beta_client(settings: Settings | None = None, engine=None):
    # Writes wait for a recent sweep, and these tests run none.
    if engine is not None:
        record_complete(engine)
    runtime = StartedRuntime(settings or beta_settings(), engine)
    temporal = FakeTemporal()
    app = create_app(runtime, temporal)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as http:
            yield http, runtime, temporal, app


def cookie():
    return {"Cookie": f"{SANDBOX_COOKIE}={SANDBOX_TOKEN}"}


def open_sandbox(engine) -> uuid.UUID:
    """Make the sandbox the visitor's cookie names."""
    with Session(engine) as session, session.begin():
        sandbox = Sandbox(
            token_hash=token_hash(SANDBOX_TOKEN),
            expires_at=session.scalar(text("SELECT now() + interval '50 minutes'")),
        )
        session.add(sandbox)
        session.flush()
        return sandbox.id


def with_sandbox(**extra):
    return cookie() | {"Origin": BASE} | extra


def concrete(path: str) -> str:
    return (
        path.replace("{document_id}", str(uuid.uuid4()))
        .replace("{action}", "approve")
        .replace("{path:path}", "home.css")
    )


def route_table(app) -> list[tuple[str, str]]:
    routes = []
    for route in app.routes:
        if isinstance(route, Mount):
            routes.append(("GET", route.path + "/{path:path}"))
        elif hasattr(route, "methods"):
            routes += [(method, route.path) for method in sorted(route.methods or [])]
    return routes


# A1


class NoIngress:
    async def submit(self, *args, **kwargs):
        raise AssertionError("A request without a case type must not reach ingress")


async def test_every_route_answers_as_its_pinned_class_without_a_sandbox(postgres_engine):
    async with beta_client(engine=postgres_engine) as (http, _, _, app):
        app.state.ingress = NoIngress()
        table = route_table(app)
        assert set(table) == PUBLIC | SANDBOX_SCOPED | CLOSED | REFUSED
        for method, template in table:
            response = await http.request(
                method, concrete(template), headers={"Origin": BASE}, content=b"{}"
            )
            route = (method, template)
            if route in REFUSED:
                assert response.status_code == 404, route
                assert response.headers["cache-control"] == "no-store"
            elif route in CLOSED:
                assert response.status_code == 403, route
                assert response.headers["cache-control"] == "no-store"
            elif route in SANDBOX_SCOPED:
                # Served, with nothing of anyone's: a document route finds no document.
                assert response.status_code in {200, 404, 422}, (route, response.text)
                assert response.headers["cache-control"] == "no-store"
                if "{document_id}" in template:
                    assert response.json() == {"detail": "Unknown document"}, route
            else:
                if template == "/assets/{path:path}":
                    # A file the review page's build really has.
                    built = min(path.name for path in BUILT_ASSETS.iterdir())
                    response = await http.get("/assets/" + built)
                assert response.status_code == 200, (route, response.text)
                if template == "/healthz":
                    assert response.json() == {"status": "ok"}
                if template == "/":
                    assert response.content == (BUILT / "home" / "index.html").read_bytes()
                if template == "/home/{path:path}":
                    assert response.content == (BUILT / "home" / "home.css").read_bytes()
                if template == "/reviewer":
                    assert response.headers["cache-control"] == "no-store"
                    assert 'id="review-token"' not in response.text
        assert (await http.get("/review")).json() == {"items": []}
        for method, path in [("GET", "/not-a-route"), ("HEAD", "/"), ("POST", "/healthz")]:
            response = await http.request(method, path, headers={"Origin": BASE})
            assert response.status_code == 404, (method, path)
            assert response.headers["cache-control"] == "no-store"


async def test_a_valid_bearer_token_grants_nothing_in_beta(postgres_engine):
    document = held_document(postgres_engine)
    # A document in no sandbox, as a local-mode upload or an old shared seed would be.
    unscoped = held_document(postgres_engine, sandboxed=False)
    async with beta_client(engine=postgres_engine) as (http, runtime, _, _):
        token = secrets.token_hex(16)
        runtime.settings = runtime.settings.model_copy(
            update={"docket_review_token": SecretStr(token)}
        )
        bearer = {"Authorization": "Bearer " + token, "Origin": BASE}
        assert (await http.get("/review", headers=bearer)).json() == {"items": []}
        for held in (document, unscoped):
            response = await http.get(f"/documents/{held}", headers=bearer)
            assert response.status_code == 404
            assert response.json() == {"detail": "Unknown document"}
        page = await http.get("/reviewer", headers=bearer)
        assert page.status_code == 200
        assert 'id="review-token"' not in page.text
        assert token not in page.text


async def test_the_missing_token_503_never_answers_in_beta(postgres_engine):
    async with beta_client(engine=postgres_engine) as (http, _, _, _):
        response = await http.get("/review")
        assert response.status_code == 200
        assert "DOCKET_REVIEW_TOKEN" not in response.text


async def test_api_refuses_to_start_with_a_review_token():
    runtime = FakeRuntime(beta_settings(docket_review_token=SecretStr("set")))
    app = create_app(runtime, FakeTemporal())
    with pytest.raises(UnsafeConfig, match="DOCKET_REVIEW_TOKEN must be unset"):
        async with app.router.lifespan_context(app):
            pass


# A3


def test_the_address_is_the_last_forwarded_hop_only_when_the_proxy_is_trusted():
    def scope(*values: str):
        return {
            "headers": [(b"x-forwarded-for", value.encode()) for value in values],
            "client": ("172.18.0.5", 443),
        }

    trusted, direct = beta_settings(), beta_settings(docket_trust_proxy=False)
    assert client_address(scope("1.1.1.1, 2.2.2.2"), trusted) == "2.2.2.2"
    assert client_address(scope("1.1.1.1", "2.2.2.2"), trusted) == "2.2.2.2"
    assert client_address(scope(), trusted) == "172.18.0.5"
    assert client_address(scope("1.1.1.1, "), trusted) == "172.18.0.5"
    assert client_address(scope("1.1.1.1, 2.2.2.2"), direct) == "172.18.0.5"
    # An IPv6 address counts by its /64; an IPv4-mapped one as the IPv4 address.
    assert client_address(scope("2001:db8:1:2:aaaa::1"), trusted) == "2001:db8:1:2::/64"
    assert client_address(scope("2001:db8:1:2:bbbb::9"), trusted) == "2001:db8:1:2::/64"
    assert client_address(scope("2001:db8:1:3::1"), trusted) == "2001:db8:1:3::/64"
    assert client_address(scope("::ffff:192.0.2.7"), trusted) == "192.0.2.7"


# A4


@pytest.mark.parametrize("origin", [None, "https://evil.example", "null", BASE + ".evil.example"])
async def test_posts_without_the_beta_origin_are_refused(origin):
    document = uuid.uuid4()
    async with beta_client() as (http, _, temporal, _):
        headers = cookie() | ({"Origin": origin} if origin else {})
        for path, body in [
            ("/beta/samples", {"case_type": "clean"}),
            (f"/review/{document}/approve", {"record_id": "UNIT-0001"}),
            (f"/review/{document}/dismiss", {"reason": "spam"}),
        ]:
            response = await http.post(path, json=body, headers=headers)
            assert response.status_code == 403, (path, response.text)
            assert "set-cookie" not in response.headers
        assert temporal.updates == []


async def test_two_origin_headers_are_refused():
    async with beta_client() as (http, _, _, _):
        headers = [("Cookie", cookie()["Cookie"]), ("Origin", BASE), ("Origin", BASE)]
        response = await http.post("/beta/samples", json={"case_type": "clean"}, headers=headers)
        assert response.status_code == 403


# A5


async def call_raw(app, method: str, path: str, headers: list[tuple[bytes, bytes]]):
    """Send a request whose body never ends, counting how often the app reads it."""
    reads, messages = 0, []

    async def receive():
        nonlocal reads
        reads += 1
        await asyncio.sleep(0)
        return {"type": "http.request", "body": b"x" * 1024, "more_body": True}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"docket.example.test"), *headers],
        "client": ("127.0.0.1", 1),
        "server": ("docket.example.test", 443),
    }
    await asyncio.wait_for(app(scope, receive, send), 5)
    start = next(message for message in messages if message["type"] == "http.response.start")
    return start["status"], reads


@pytest.mark.parametrize("path", ["/documents", "/inbound"])
async def test_visitor_documents_are_refused_before_any_body_is_read(path):
    async with beta_client() as (_, _, _, app):
        session = [(b"cookie", cookie()["Cookie"].encode()), (b"origin", BASE.encode())]
        chunked = [(b"transfer-encoding", b"chunked")]
        assert await call_raw(app, "POST", path, session + chunked) == (403, 0)
        assert await call_raw(app, "POST", path, chunked) == (403, 0)


async def test_a_document_route_checks_ownership_before_reading_any_of_the_body(
    postgres_engine,
):
    document = held_document(postgres_engine)
    other, _ = make_sandbox(postgres_engine)
    others_document = held_in(postgres_engine, other)
    expired, expired_token = make_sandbox(postgres_engine, minutes_left=-1)
    expired_document = held_in(postgres_engine, expired)
    async with beta_client(engine=postgres_engine) as (http, _, temporal, app):
        large = json.dumps({"reason": "x" * 17 * 1024}).encode()
        origin = {"Origin": BASE}
        visitor = cookie()
        refused = [
            ("no such document", uuid.uuid4(), visitor),
            ("another visitor's", others_document, visitor),
            ("no cookie", document, {}),
            ("an expired sandbox", expired_document, {"Cookie": sandbox_cookie(expired_token)}),
        ]
        for case, target, headers in refused:
            path = f"/review/{target}/dismiss"
            raw = [
                (key.lower().encode(), value.encode()) for key, value in (headers | origin).items()
            ]
            assert await call_raw(app, "POST", path, raw) == (404, 0), case
            http.cookies.clear()
            response = await http.post(path, content=large, headers=headers | origin)
            assert response.status_code == 404, case
            assert response.json() == {"detail": "Unknown document"}, case
        # The visitor's own document: the body is read, and refused once it passes 16 KB.
        path = f"/review/{document}/dismiss"
        raw = [(b"cookie", visitor["Cookie"].encode()), (b"origin", BASE.encode())]
        assert await call_raw(app, "POST", path, raw) == (413, 17)
        response = await http.post(path, content=large, headers=with_sandbox())
        assert response.status_code == 413
        assert response.headers["cache-control"] == "no-store"
        sample = await http.post("/beta/samples", content=large, headers={"Origin": BASE})
        assert sample.status_code == 413
        assert temporal.updates == []
        fits = await http.post(
            f"/review/{document}/dismiss",
            content=b"[" + b" " * (16 * 1024 - 2) + b"]",
            headers=with_sandbox(),
        )
        assert fits.status_code == 422


def held_document(engine, sandboxed: bool = True) -> uuid.UUID:
    """A held document, in the visitor's sandbox unless sandboxed is False (local mode)."""
    sandbox_id = open_sandbox(engine) if sandboxed else None
    with Session(engine) as session, session.begin():
        document = Document(
            sandbox_id=sandbox_id,
            mode="act",
            run_id="live",
            content_hash=uuid.uuid4().hex * 2,
            status="needs_review",
        )
        session.add(document)
        session.flush()
        session.add(
            Event(
                document_id=document.id,
                step="score",
                idem_key=f"{document.id}:score",
                detail={"candidates": [{"record_id": "UNIT-0001"}, {"record_id": "UNIT-0002"}]},
            )
        )
        return document.id


async def test_beta_caps_the_dismiss_reason_and_approves_only_candidates(postgres_engine):
    document = held_document(postgres_engine)
    async with beta_client(engine=postgres_engine) as (http, _, temporal, _):
        base = f"/review/{document}"
        for path, body in [
            ("/dismiss", {"reason": "x" * 301}),
            ("/approve", {"record_id": "UNIT-0003"}),
            ("/approve", {}),
        ]:
            response = await http.post(base + path, json=body, headers=with_sandbox())
            assert response.status_code == 422, (path, body, response.text)
        assert temporal.updates == []
        assert (
            await http.post(base + "/dismiss", json={"reason": "x" * 300}, headers=with_sandbox())
        ).status_code == 200
        assert (
            await http.post(
                base + "/approve", json={"record_id": "UNIT-0002"}, headers=with_sandbox()
            )
        ).status_code == 200
        assert [update[2] for update in temporal.updates] == [
            {"action": "dismiss", "value": "x" * 300},
            {"action": "approve", "value": "UNIT-0002"},
        ]


async def test_local_mode_keeps_its_review_rules(postgres_engine):
    document = held_document(postgres_engine, sandboxed=False)
    token = secrets.token_hex(16)
    local = Settings(_env_file=None, docket_review_token=SecretStr(token))
    async with beta_client(local, postgres_engine) as (http, _, temporal, _):
        headers = {"Authorization": "Bearer " + token}
        for body in [{"reason": "x" * 301}, {"record_id": "UNIT-0003"}]:
            action = "dismiss" if "reason" in body else "approve"
            response = await http.post(f"/review/{document}/{action}", json=body, headers=headers)
            assert response.status_code == 200
        assert len(temporal.updates) == 2
        assert (
            await http.post("/reviewer/session", json={"code": "a-code"}, headers=headers)
        ).status_code == 404
        assert (await http.post("/reviewer/session", json={"code": "a-code"})).status_code == 401


# A6


def refusal_cases():
    unset_cap = beta_settings(drop=("docket_daily_budget_usd",))
    return {
        "address key missing": (beta_settings(drop=("docket_address_key",)), {}),
        "short address key": (beta_settings(docket_address_key=SecretStr("k" * 31)), {}),
        "review token set": (beta_settings(docket_review_token=SecretStr("t")), {}),
        "proxy not trusted": (beta_settings(docket_trust_proxy=False), {}),
        "failpoint": (beta_settings(), {"DOCKET_FAILPOINT": "ingress.after_raw"}),
        "failpoint variant": (beta_settings(), {"DOCKET_FAILPOINT_EXTRA": "1"}),
        "cap missing": (unset_cap, {}),
        "cap above a dollar": (beta_settings(docket_daily_budget_usd=1.01), {}),
        "cap not a number": (beta_settings(docket_daily_budget_usd=math.nan), {}),
        "record mode": (beta_settings(docket_model_mode="record"), {}),
        "review timeout above an hour": (beta_settings(docket_review_timeout_seconds=3601), {}),
    }


CASES = [
    "address key missing",
    "short address key",
    "review token set",
    "proxy not trusted",
    "failpoint",
    "failpoint variant",
    "cap missing",
    "cap above a dollar",
    "cap not a number",
    "record mode",
    "review timeout above an hour",
]


@pytest.mark.parametrize("case", CASES)
async def test_each_unsafe_config_stops_the_api_the_worker_and_the_shared_check(case, monkeypatch):
    settings, environ = refusal_cases()[case]
    assert set(refusal_cases()) == set(CASES)
    for name, value in environ.items():
        monkeypatch.setenv(name, value)
    problems = startup_problems(settings, os.environ)
    assert len(problems) == 1, problems
    with pytest.raises(UnsafeConfig) as refused:
        refuse_unsafe_start(settings)
    message = str(refused.value)
    assert message == "Docket beta refuses to start:\n- " + problems[0]
    assert KEY not in message
    app = create_app(FakeRuntime(settings), FakeTemporal())
    with pytest.raises(UnsafeConfig):
        async with app.router.lifespan_context(app):
            pass
    with pytest.raises(UnsafeConfig):
        await run_worker(FakeRuntime(settings))


def test_a_safe_beta_config_and_any_local_config_pass():
    assert startup_problems(beta_settings(), {}) == []
    assert startup_problems(beta_settings(docket_model_mode="live"), {}) == []
    local = Settings(_env_file=None, docket_review_token=SecretStr("t"))
    assert startup_problems(local, {"DOCKET_FAILPOINT": "ingress.after_raw"}) == []


def test_the_cap_counts_as_set_from_the_environment(monkeypatch):
    monkeypatch.setenv("DOCKET_DAILY_BUDGET_USD", "1.00")
    settings = Settings(_env_file=None)
    assert "docket_daily_budget_usd" in settings.model_fields_set
    monkeypatch.delenv("DOCKET_DAILY_BUDGET_USD")
    assert "docket_daily_budget_usd" not in Settings(_env_file=None).model_fields_set


@pytest.mark.parametrize("command", [["-m", "docket.worker"], ["-m", "uvicorn", "docket.api:app"]])
def test_the_real_processes_exit_with_the_plain_message(command, tmp_path):
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DOCKET_", "ANTHROPIC_", "OPENROUTER_"))
    } | {"DOCKET_ENV": "beta", "DOCKET_REVIEW_TOKEN": "set"}
    result = subprocess.run(
        [sys.executable, *command, *(["--port", "0"] if "uvicorn" in command else [])],
        capture_output=True,
        text=True,
        timeout=60,
        env=environment,
        cwd=tmp_path,
    )
    assert result.returncode != 0
    assert (
        "Docket beta refuses to start:\n- DOCKET_ADDRESS_KEY needs at least 32 bytes."
        in result.stderr
    )
    assert "- DOCKET_REVIEW_TOKEN must be unset in beta." in result.stderr


@pytest.mark.parametrize("command", [["eval"], ["spike-models"]])
def test_commands_that_can_spend_refuse_an_unsafe_beta_config(command, monkeypatch):
    from typer.testing import CliRunner

    from docket import evaluation
    from docket.cli import app
    from docket.llm import spike

    def spent(*args, **kwargs):
        raise AssertionError("A refused command must stop before any model work")

    monkeypatch.setattr(evaluation, "evaluate", spent)
    monkeypatch.setattr(spike, "spike", spent)
    monkeypatch.setenv("DOCKET_ENV", "beta")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "set")
    result = CliRunner().invoke(app, command)
    assert result.exit_code == 1
    assert result.output.startswith("Docket beta refuses to start:\n- ")
    monkeypatch.setenv("DOCKET_ENV", "local")
    local = CliRunner().invoke(app, command)
    assert isinstance(local.exception, AssertionError), local.output


# Review fix round: s1 to s4


@pytest.mark.parametrize("mode", ["beta", "local"])
async def test_unknown_actions_and_nul_reasons_never_reach_temporal(mode, postgres_engine):
    document = held_document(postgres_engine, sandboxed=mode == "beta")
    token = secrets.token_hex(16)
    if mode == "beta":
        settings, headers = beta_settings(), with_sandbox()
    else:
        settings = Settings(_env_file=None, docket_review_token=SecretStr(token))
        headers = {"Authorization": "Bearer " + token}
    async with beta_client(settings, postgres_engine) as (http, _, temporal, _):
        base = f"/review/{document}"
        for action, body, status in [
            ("reject", {"reason": "x" * 5000}, 409),
            ("Approve", {"record_id": "UNIT-0001"}, 409),
            ("dismiss", {"reason": "spam\x00"}, 422),
        ]:
            response = await http.post(f"{base}/{action}", json=body, headers=headers)
            assert response.status_code == status, (action, response.text)
        assert temporal.updates == []


async def test_beta_refuses_a_blank_dismiss_reason_before_temporal(postgres_engine):
    document = held_document(postgres_engine)
    async with beta_client(engine=postgres_engine) as (http, _, temporal, _):
        for body in [{"reason": "   "}, {}]:
            response = await http.post(
                f"/review/{document}/dismiss", json=body, headers=with_sandbox()
            )
            assert response.status_code == 422, body
        assert temporal.updates == []


def beta_environment(monkeypatch, **extra):
    base = {
        "DOCKET_ENV": "beta",
        "DOCKET_ADDRESS_KEY": KEY,
        "DOCKET_TRUST_PROXY": "1",
        "DOCKET_BASE_URL": BASE,
        "DOCKET_DAILY_BUDGET_USD": "1.00",
        "DOCKET_MODEL_MODE": "replay",
        "ANTHROPIC_API_KEY": "set",  # pragma: allowlist secret
    }
    # A dict merge, so a value in extra always replaces the default.
    for key, value in {**base, **extra}.items():
        monkeypatch.setenv(key, value)


@pytest.mark.parametrize("mode", ["record", "export", "import"])
def test_eval_checks_the_mode_it_will_run_not_the_environment(mode, monkeypatch):
    from typer.testing import CliRunner

    from docket import evaluation
    from docket.cli import app

    runs = []
    monkeypatch.setattr(evaluation, "evaluate", lambda *args: runs.append(args) or 1 / 0)
    beta_environment(monkeypatch)
    # The environment allows replay, so only --mode can cause the refusal.
    assert os.environ["DOCKET_MODEL_MODE"] == "replay"
    result = CliRunner().invoke(app, ["eval", "--mode", mode])
    assert result.exit_code == 1
    assert "DOCKET_MODEL_MODE must be live or replay in beta." in result.output
    assert runs == []
    allowed = CliRunner().invoke(app, ["eval", "--mode", "replay"])
    assert len(runs) == 1 and isinstance(allowed.exception, ZeroDivisionError)


def test_spike_models_is_checked_as_the_live_run_it_is(monkeypatch):
    from typer.testing import CliRunner

    from docket.cli import app
    from docket.llm import spike

    runs = []
    monkeypatch.setattr(spike, "spike", lambda *args: runs.append(args) or "ran")
    beta_environment(monkeypatch, DOCKET_MODEL_MODE="record")
    # The environment's mode is refused in beta, so passing proves the live mode was checked.
    assert os.environ["DOCKET_MODEL_MODE"] == "record"
    result = CliRunner().invoke(app, ["spike-models"])
    assert result.exit_code == 0, result.output
    assert len(runs) == 1
    monkeypatch.setenv("DOCKET_DAILY_BUDGET_USD", "5")
    refused = CliRunner().invoke(app, ["spike-models"])
    assert refused.exit_code == 1 and len(runs) == 1


# Fix-check round: x1 and x4


@pytest.mark.parametrize("mode", ["beta", "local"])
async def test_a_dismiss_reason_that_is_not_utf8_never_reaches_temporal(mode, postgres_engine):
    document = held_document(postgres_engine, sandboxed=mode == "beta")
    token = secrets.token_hex(16)
    if mode == "beta":
        settings, headers = beta_settings(), with_sandbox()
    else:
        settings = Settings(_env_file=None, docket_review_token=SecretStr(token))
        headers = {"Authorization": "Bearer " + token}
    async with beta_client(settings, postgres_engine) as (http, _, temporal, _):
        response = await http.post(
            f"/review/{document}/dismiss",
            content=b'{"reason": "spam \\ud800"}',
            headers=headers | {"Content-Type": "application/json"},
        )
        assert response.status_code == 422, response.text
        assert temporal.updates == []


def test_every_compose_service_that_runs_docket_code_receives_the_beta_settings():
    import yaml

    services = yaml.safe_load(Path("compose.yaml").read_text())["services"]
    docket = [name for name, service in services.items() if service.get("build") == "."]
    assert set(docket) == {"migrate", "api", "worker"}
    for name in docket:
        environment = services[name]["environment"]
        assert environment["DOCKET_ENV"] == "${DOCKET_ENV:-local}", name
        for setting in ("DOCKET_ADDRESS_KEY", "DOCKET_BASE_URL"):
            assert environment[setting] == "${" + setting + ":-}", (name, setting)
        assert environment["DOCKET_TRUST_PROXY"] == "${DOCKET_TRUST_PROXY:-0}", name
        # Any default for the cap would count as set and hide the beta refusal.
        assert "DOCKET_DAILY_BUDGET_USD" not in environment, name


# Fresh review round: s1, f1 and f2


@pytest.mark.parametrize(
    "path",
    ["/documents\n", "/inbound\n", "/documents/", "//documents", "/inbound/", "//inbound//"],
)
async def test_closed_writes_cannot_be_reached_through_another_spelling(path):
    async with beta_client() as (_, _, _, app):
        session = [(b"cookie", cookie()["Cookie"].encode()), (b"origin", BASE.encode())]
        status, reads = await call_raw(app, "POST", path, session)
        assert reads == 0, path
        assert status in {400, 403}, (path, status)


@pytest.mark.parametrize("path", ["/review\n", "/healthz\x7f", "/reviewer\t", "/home/home.css\r"])
async def test_beta_refuses_any_path_with_a_control_character(path):
    async with beta_client() as (_, _, _, app):
        session = [(b"cookie", cookie()["Cookie"].encode())]
        status, reads = await call_raw(app, "GET", path, session)
        assert (status, reads) == (400, 0), path


async def test_local_body_limit_also_covers_the_line_feed_spelling():
    from docket.webhook import BodyLimit

    reached = []

    async def app(scope, receive, send):
        reached.append(scope["path"])

    status, reads = await call_raw(BodyLimit(app), "POST", "/inbound\n", [])
    assert status == 413 and reached == []
    assert reads == 10 * 1024 + 1


@pytest.mark.parametrize("mode", ["beta", "local"])
@pytest.mark.parametrize("body", [b'"\\ud800"', b'["\\ud800"]', b"1e999999"])
async def test_a_malformed_review_body_is_a_fixed_422_and_never_reaches_temporal(
    mode, body, postgres_engine
):
    document = held_document(postgres_engine, sandboxed=mode == "beta")
    token = secrets.token_hex(16)
    if mode == "beta":
        settings, headers = beta_settings(), with_sandbox()
    else:
        settings = Settings(_env_file=None, docket_review_token=SecretStr(token))
        headers = {"Authorization": "Bearer " + token}
    async with beta_client(settings, postgres_engine) as (http, _, temporal, _):
        response = await http.post(
            f"/review/{document}/dismiss",
            content=body,
            headers=headers | {"Content-Type": "application/json"},
        )
        assert response.status_code == 422, response.text
        assert response.json() == {"detail": "Send the review decision as JSON text."}
        assert temporal.updates == []
