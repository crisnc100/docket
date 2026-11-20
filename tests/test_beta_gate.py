"""The hosted beta's gate: invite-code sessions, the four public routes, closed write paths,
the wrong-code lockout and the startup refusals (plan PR A, A1 to A6)."""

import asyncio
import json
import math
import os
import secrets
import subprocess
import sys
import time
import uuid
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.routing import Mount

from docket import beta
from docket.api import create_app
from docket.beta import (
    SESSION_COOKIE,
    UnsafeConfig,
    client_address,
    refuse_unsafe_start,
    session_value,
    startup_problems,
    valid_session,
)
from docket.db import Document, Event, SigninFailure
from docket.settings import Settings
from docket.worker import run_worker

BASE = "https://docket.example.test"
CODE = "invite-" + secrets.token_hex(12)
OTHER_CODE = "invite-" + secrets.token_hex(12)
KEY = secrets.token_hex(32)
PUBLIC = {
    ("GET", "/"),
    ("GET", "/home/{path:path}"),
    ("GET", "/healthz"),
    ("POST", "/reviewer/session"),
}


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("DOCKET_FAILPOINT", "DOCKET_DAILY_BUDGET_USD")):
            monkeypatch.delenv(name)


def beta_settings(drop: tuple[str, ...] = (), **overrides) -> Settings:
    values = {
        "docket_env": "beta",
        "docket_invite_codes": SecretStr(f"{CODE},{OTHER_CODE}"),
        "docket_session_key": SecretStr(KEY),
        "docket_trust_proxy": True,
        "docket_base_url": BASE,
        "docket_daily_budget_usd": 1.00,
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
    runtime = StartedRuntime(settings or beta_settings(), engine)
    temporal = FakeTemporal()
    app = create_app(runtime, temporal)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as http:
            yield http, runtime, temporal, app


def cookie(settings: Settings | None = None, code: str = CODE, now: float | None = None):
    return {"Cookie": f"{SESSION_COOKIE}={session_value(settings or beta_settings(), code, now)}"}


def signed_in(**extra):
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


async def test_only_the_four_public_routes_answer_without_a_session_across_the_route_table():
    async with beta_client() as (http, _, _, app):
        table = route_table(app)
        assert len(table) > len(PUBLIC)
        open_routes = set()
        for method, template in table:
            response = await http.request(
                method, concrete(template), headers={"Origin": BASE}, content=b"{}"
            )
            if response.status_code != 401:
                open_routes.add((method, template))
            else:
                assert response.headers["cache-control"] == "no-store"
        assert open_routes == PUBLIC
        for method, path in [("GET", "/not-a-route"), ("HEAD", "/"), ("POST", "/healthz")]:
            response = await http.request(method, path, headers={"Origin": BASE})
            assert response.status_code == 401, (method, path)


async def test_a_valid_bearer_token_is_refused_in_beta():
    async with beta_client() as (http, runtime, _, _):
        token = secrets.token_hex(16)
        runtime.settings = runtime.settings.model_copy(
            update={"docket_review_token": SecretStr(token)}
        )
        bearer = {"Authorization": "Bearer " + token, "Origin": BASE}
        assert (await http.get("/review", headers=bearer)).status_code == 401
        page = await http.get("/reviewer", headers=bearer)
        assert page.status_code == 401
        assert 'for="review-token">Invite code<' in page.text
        assert token not in page.text


async def test_the_missing_token_503_never_answers_in_beta():
    async with beta_client() as (http, _, _, _):
        response = await http.get("/review")
        assert response.status_code == 401
        assert "DOCKET_REVIEW_TOKEN" not in response.text


async def test_api_refuses_to_start_with_a_review_token():
    runtime = FakeRuntime(beta_settings(docket_review_token=SecretStr("set")))
    app = create_app(runtime, FakeTemporal())
    with pytest.raises(UnsafeConfig, match="DOCKET_REVIEW_TOKEN must be unset"):
        async with app.router.lifespan_context(app):
            pass


# A2


async def test_a_right_code_sets_the_session_cookie_with_exactly_its_flags(postgres_engine):
    async with beta_client(engine=postgres_engine) as (http, _, _, _):
        response = await http.post(
            "/reviewer/session", json={"code": CODE}, headers={"Origin": BASE}
        )
        assert response.status_code == 204, response.text
        header = response.headers["set-cookie"]
        name, _, rest = header.partition("=")
        value, *flags = rest.split("; ")
        assert name == SESSION_COOKIE
        assert flags == ["HttpOnly", "Secure", "SameSite=Strict", "Path=/"]
        assert CODE not in header and CODE.split("-")[1] not in header
        assert valid_session(beta_settings(), value)
        assert response.headers["cache-control"] == "no-store"
        queue = await http.get("/review", headers={"Cookie": f"{SESSION_COOKIE}={value}"})
        assert queue.status_code == 200
        assert queue.json() == {"items": []}
        page = await http.get("/reviewer", headers={"Cookie": f"{SESSION_COOKIE}={value}"})
        assert page.status_code == 200
        assert "const signedIn = true;" in page.text


async def test_old_removed_and_foreign_cookies_are_refused():
    eight_days_ago = time.time() - 8 * 24 * 3600
    six_days_ago = time.time() - 6 * 24 * 3600
    foreign = beta_settings(docket_session_key=SecretStr(secrets.token_hex(32)))
    async with beta_client() as (http, runtime, _, _):
        cases = {
            "eight days old": cookie(now=eight_days_ago),
            "another key": cookie(foreign),
            "not a cookie value": {"Cookie": f"{SESSION_COOKIE}=1.2"},
            "future dated": cookie(now=time.time() + 3600),
        }
        for label, headers in cases.items():
            assert (await http.get("/review", headers=headers)).status_code == 401, label
        assert (await http.get("/reviewer", headers=cookie(now=six_days_ago))).status_code == 200
        removed = cookie(code=OTHER_CODE)
        assert (await http.get("/reviewer", headers=removed)).status_code == 200
        runtime.settings = runtime.settings.model_copy(
            update={"docket_invite_codes": SecretStr(CODE)}
        )
        assert (await http.get("/reviewer", headers=removed)).status_code == 401
        assert (await http.get("/reviewer", headers=cookie())).status_code == 200


def test_session_checks():
    settings = beta_settings()
    now = int(time.time())
    value = session_value(settings, CODE, now)
    assert valid_session(settings, value, now + 7 * 24 * 3600)
    assert not valid_session(settings, value, now + 7 * 24 * 3600 + 1)
    assert not valid_session(beta_settings(docket_invite_codes=SecretStr(OTHER_CODE)), value, now)
    assert not valid_session(settings, value.replace(".", ".0", 1), now)
    assert not valid_session(settings, "", now)


# A3


def wrong_code(address: str, prefix: str = "") -> dict:
    hops = f"{prefix}, {address}" if prefix else address
    return {"json": {"code": "wrong-" + secrets.token_hex(10)}, "headers": forwarded(hops)}


def forwarded(hops: str) -> dict:
    return {"Origin": BASE, "X-Forwarded-For": hops}


async def test_the_eleventh_code_in_an_hour_is_refused_even_when_right(postgres_engine):
    async with beta_client(engine=postgres_engine) as (http, _, _, _):
        for attempt in range(10):
            # A client-written prefix changes nothing: the last hop is the address.
            reply = await http.post(
                "/reviewer/session", **wrong_code("203.0.113.7", f"10.0.0.{attempt}")
            )
            assert reply.status_code == 401, (attempt, reply.text)
        right = await http.post(
            "/reviewer/session", json={"code": CODE}, headers=forwarded("198.51.100.1, 203.0.113.7")
        )
        assert right.status_code == 429
        assert 0 < int(right.headers["retry-after"]) <= 3600
        assert "set-cookie" not in right.headers
        other = await http.post(
            "/reviewer/session", json={"code": CODE}, headers=forwarded("203.0.113.8")
        )
        assert other.status_code == 204
        spoofed = await http.post(
            "/reviewer/session", json={"code": CODE}, headers=forwarded("203.0.113.8, 203.0.113.7")
        )
        assert spoofed.status_code == 429
        locked_with_cookie = cookie() | forwarded("203.0.113.7")
        assert (await http.get("/review", headers=locked_with_cookie)).status_code == 200

    async with beta_client(engine=postgres_engine) as (http, _, _, _):
        restarted = await http.post(
            "/reviewer/session", json={"code": CODE}, headers=forwarded("203.0.113.7")
        )
        assert restarted.status_code == 429


async def test_right_codes_do_not_count_and_parallel_guesses_stay_inside_the_limit(
    postgres_engine,
):
    async with beta_client(engine=postgres_engine) as (http, _, _, _):
        for _ in range(12):
            reply = await http.post(
                "/reviewer/session", json={"code": CODE}, headers=forwarded("192.0.2.1")
            )
            assert reply.status_code == 204
        replies = await asyncio.gather(
            *[http.post("/reviewer/session", **wrong_code("192.0.2.2")) for _ in range(30)]
        )
        statuses = sorted(reply.status_code for reply in replies)
        assert statuses == [401] * 10 + [429] * 20
    with Session(postgres_engine) as session:
        counts = Counter(row.address for row in session.query(SigninFailure))
    assert counts == {"192.0.2.2": 10}


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


# A4


@pytest.mark.parametrize("origin", [None, "https://evil.example", "null", BASE + ".evil.example"])
async def test_posts_without_the_beta_origin_are_refused(origin):
    document = uuid.uuid4()
    async with beta_client() as (http, _, temporal, _):
        headers = cookie() | ({"Origin": origin} if origin else {})
        for path, body in [
            ("/reviewer/session", {"code": CODE}),
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
        response = await http.post("/reviewer/session", json={"code": CODE}, headers=headers)
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
        assert await call_raw(app, "POST", path, chunked) == (401, 0)


async def test_bodies_over_16_kb_are_refused_before_the_handler():
    document = uuid.uuid4()
    async with beta_client() as (http, _, temporal, app):
        path = f"/review/{document}/dismiss"
        large = json.dumps({"reason": "x" * 17 * 1024}).encode()
        response = await http.post(path, content=large, headers=signed_in())
        assert response.status_code == 413
        session = [(b"cookie", cookie()["Cookie"].encode()), (b"origin", BASE.encode())]
        status, reads = await call_raw(app, "POST", path, session)
        assert status == 413 and reads == 17
        sign_in = await http.post("/reviewer/session", content=large, headers={"Origin": BASE})
        assert sign_in.status_code == 413
        assert temporal.updates == []
        fits = await http.post(
            path, content=b"[" + b" " * (16 * 1024 - 2) + b"]", headers=signed_in()
        )
        assert fits.status_code == 422


def held_document(engine) -> uuid.UUID:
    with Session(engine) as session, session.begin():
        document = Document(
            mode="act", run_id="live", content_hash=uuid.uuid4().hex * 2, status="needs_review"
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
            response = await http.post(base + path, json=body, headers=signed_in())
            assert response.status_code == 422, (path, body, response.text)
        assert temporal.updates == []
        assert (
            await http.post(base + "/dismiss", json={"reason": "x" * 300}, headers=signed_in())
        ).status_code == 200
        assert (
            await http.post(base + "/approve", json={"record_id": "UNIT-0002"}, headers=signed_in())
        ).status_code == 200
        assert [update[2] for update in temporal.updates] == [
            {"action": "dismiss", "value": "x" * 300},
            {"action": "approve", "value": "UNIT-0002"},
        ]


async def test_local_mode_keeps_its_review_rules(postgres_engine):
    document = held_document(postgres_engine)
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
            await http.post("/reviewer/session", json={"code": CODE}, headers=headers)
        ).status_code == 404
        assert (await http.post("/reviewer/session", json={"code": CODE})).status_code == 401


# A6


def refusal_cases(tmp_path: Path):
    example = tmp_path / ".env.beta.example"
    example.write_text(f"DOCKET_INVITE_CODES={CODE}\n")
    unset_cap = beta_settings(drop=("docket_daily_budget_usd",))
    return {
        "codes missing": (beta_settings(docket_invite_codes=SecretStr("")), {}, None),
        "blank code entry": (beta_settings(docket_invite_codes=SecretStr(CODE + ",")), {}, None),
        "short code": (beta_settings(docket_invite_codes=SecretStr("short-code")), {}, None),
        "placeholder code": (beta_settings(), {}, example),
        "short session key": (beta_settings(docket_session_key=SecretStr("k" * 31)), {}, None),
        "review token set": (beta_settings(docket_review_token=SecretStr("t")), {}, None),
        "proxy not trusted": (beta_settings(docket_trust_proxy=False), {}, None),
        "failpoint": (beta_settings(), {"DOCKET_FAILPOINT": "ingress.after_raw"}, None),
        "failpoint variant": (beta_settings(), {"DOCKET_FAILPOINT_EXTRA": "1"}, None),
        "cap missing": (unset_cap, {}, None),
        "cap above a dollar": (beta_settings(docket_daily_budget_usd=1.01), {}, None),
        "cap not a number": (beta_settings(docket_daily_budget_usd=math.nan), {}, None),
        "record mode": (beta_settings(docket_model_mode="record"), {}, None),
    }


CASES = [
    "codes missing",
    "blank code entry",
    "short code",
    "placeholder code",
    "short session key",
    "review token set",
    "proxy not trusted",
    "failpoint",
    "failpoint variant",
    "cap missing",
    "cap above a dollar",
    "cap not a number",
    "record mode",
]


@pytest.mark.parametrize("case", CASES)
async def test_each_unsafe_config_stops_the_api_the_worker_and_the_shared_check(
    case, tmp_path, monkeypatch
):
    settings, environ, example = refusal_cases(tmp_path)[case]
    assert set(refusal_cases(tmp_path)) == set(CASES)
    for name, value in environ.items():
        monkeypatch.setenv(name, value)
    if example:
        monkeypatch.setattr(beta, "EXAMPLE_ENV", example)
    problems = startup_problems(settings, os.environ)
    assert len(problems) == 1, problems
    with pytest.raises(UnsafeConfig) as refused:
        refuse_unsafe_start(settings)
    message = str(refused.value)
    assert message == "Docket beta refuses to start:\n- " + problems[0]
    for secret in (CODE, OTHER_CODE, KEY):
        assert secret not in message
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
    assert "Docket beta refuses to start:\n- DOCKET_INVITE_CODES is not set." in result.stderr
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
    document = held_document(postgres_engine)
    token = secrets.token_hex(16)
    if mode == "beta":
        settings, headers = beta_settings(), signed_in()
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
                f"/review/{document}/dismiss", json=body, headers=signed_in()
            )
            assert response.status_code == 422, body
        assert temporal.updates == []


def beta_environment(monkeypatch, **extra):
    base = {
        "DOCKET_ENV": "beta",
        "DOCKET_INVITE_CODES": CODE,
        "DOCKET_SESSION_KEY": KEY,
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


def counted_attempts(engine, address: str, minutes_ago: list[int]) -> None:
    with Session(engine) as session, session.begin():
        for minutes in minutes_ago:
            session.execute(
                text(
                    "insert into signin_failures (address, at) "
                    "values (:address, now() - make_interval(mins => :minutes))"
                ),
                {"address": address, "minutes": minutes},
            )


async def test_the_lockout_window_rolls_across_the_clock_hour(postgres_engine):
    # Ten wrong codes spread over the last 55 minutes, so a new clock hour has likely begun.
    counted_attempts(postgres_engine, "203.0.113.20", [55, 50, 45, 40, 35, 30, 25, 20, 15, 5])
    counted_attempts(postgres_engine, "203.0.113.21", [61] * 10)
    async with beta_client(engine=postgres_engine) as (http, _, _, _):
        locked = await http.post(
            "/reviewer/session", json={"code": CODE}, headers=forwarded("203.0.113.20")
        )
        assert locked.status_code == 429
        assert 4 * 60 <= int(locked.headers["retry-after"]) <= 5 * 60 + 5
        expired = await http.post(
            "/reviewer/session", json={"code": CODE}, headers=forwarded("203.0.113.21")
        )
        assert expired.status_code == 204
        for _ in range(9):
            reply = await http.post("/reviewer/session", **wrong_code("203.0.113.21"))
            assert reply.status_code == 401
        replies = [
            (await http.post("/reviewer/session", **wrong_code("203.0.113.21"))).status_code
            for _ in range(2)
        ]
        assert replies == [401, 429]
    with Session(postgres_engine) as session:
        rows = session.query(SigninFailure).filter_by(address="203.0.113.21").count()
    assert rows == 10


# Fix-check round: x1 and x4


@pytest.mark.parametrize("mode", ["beta", "local"])
async def test_a_dismiss_reason_that_is_not_utf8_never_reaches_temporal(mode, postgres_engine):
    document = held_document(postgres_engine)
    token = secrets.token_hex(16)
    if mode == "beta":
        settings, headers = beta_settings(), signed_in()
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
        for setting in ("DOCKET_INVITE_CODES", "DOCKET_SESSION_KEY", "DOCKET_BASE_URL"):
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


async def test_a_code_that_is_not_utf8_is_refused_before_it_counts(postgres_engine):
    async with beta_client(engine=postgres_engine) as (http, _, _, _):
        response = await http.post(
            "/reviewer/session",
            content=b'{"code": "\\ud800"}',
            headers=forwarded("192.0.2.50") | {"Content-Type": "application/json"},
        )
        assert response.status_code == 422, response.text
    with Session(postgres_engine) as session:
        assert session.query(SigninFailure).count() == 0


async def test_a_pasted_code_with_spaces_signs_in_and_counts_nothing(postgres_engine):
    async with beta_client(engine=postgres_engine) as (http, _, _, _):
        response = await http.post(
            "/reviewer/session", json={"code": f"  {CODE}\n"}, headers=forwarded("192.0.2.51")
        )
        assert response.status_code == 204, response.text
        blank = await http.post(
            "/reviewer/session", json={"code": "   "}, headers=forwarded("192.0.2.51")
        )
        assert blank.status_code == 422
    with Session(postgres_engine) as session:
        assert session.query(SigninFailure).count() == 0
    assert "signIn(input.value.trim())" in Path("web/login.html").read_text()


@pytest.mark.parametrize(
    "body",
    [
        b'"\\ud800"',
        b'["\\ud800"]',
        b"1e999999",
        b"[",
        b"\xff\xfe",
        b"null",
        b'{"\\ud800": "x"}',
        b"[" * 5000 + b"]" * 5000,
    ],
)
async def test_a_malformed_signin_body_is_a_fixed_422_and_counts_nothing(postgres_engine, body):
    async with beta_client(engine=postgres_engine) as (http, _, _, _):
        response = await http.post(
            "/reviewer/session",
            content=body,
            headers=forwarded("192.0.2.52") | {"Content-Type": "application/json"},
        )
        assert response.status_code == 422, response.text
        assert response.json() == {"detail": "Send one invite code as text."}
    with Session(postgres_engine) as session:
        assert session.query(SigninFailure).count() == 0


@pytest.mark.parametrize("mode", ["beta", "local"])
@pytest.mark.parametrize("body", [b'"\\ud800"', b'["\\ud800"]', b"1e999999"])
async def test_a_malformed_review_body_is_a_fixed_422_and_never_reaches_temporal(
    mode, body, postgres_engine
):
    document = held_document(postgres_engine)
    token = secrets.token_hex(16)
    if mode == "beta":
        settings, headers = beta_settings(), signed_in()
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
