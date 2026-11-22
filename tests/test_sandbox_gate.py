"""A sandbox made on the first write: its cookie, the hourly cap on new ones, and what the
database keeps of a visitor (plan PR 1, S1, S3 and S11)."""

import base64
import hashlib
import hmac
import math
import os
import subprocess
import uuid
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from docket.beta import address_hash
from docket.db import Base, SampleSend, Sandbox, SandboxCreation
from docket.sandbox import (
    SANDBOX_COOKIE,
    SANDBOX_ENDED,
    SANDBOX_ENDING,
    TOO_MANY_SANDBOXES,
    SandboxClosed,
    close_sandbox,
    spent_cookie,
)
from tests.sandboxes import held_in, make_sandbox, sandbox_cookie
from tests.test_beta_gate import BASE, beta_client, beta_settings, concrete, route_table

LIFE_SECONDS = 50 * 60


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("DOCKET_FAILPOINT", "DOCKET_DAILY_BUDGET_USD")):
            monkeypatch.delenv(name)


class FakeIngress:
    def __init__(self, error: Exception | None = None):
        self.sandboxes = []
        self.error = error

    async def submit(self, content, filename, **kwargs):
        if self.error is not None:
            raise self.error
        self.sandboxes.append(kwargs["sandbox"])
        return str(uuid.uuid4())


def sender(address: str, token: str | None = None) -> dict:
    headers = {"Origin": BASE, "X-Forwarded-For": address}
    return headers | ({"Cookie": sandbox_cookie(token)} if token else {})


def created_token(response) -> str:
    name, _, rest = response.headers["set-cookie"].partition("=")
    assert name == SANDBOX_COOKIE
    return rest.split("; ")[0]


def rows(engine) -> dict[str, int]:
    with Session(engine) as session:
        return {
            table.name: session.scalar(select(func.count()).select_from(table)) or 0
            for table in Base.metadata.sorted_tables
        }


def pg_dump(engine) -> str:
    with engine.connect() as connection:
        schema = connection.execute(text("SELECT current_schema()")).scalar_one()
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres"]
        + ["pg_dump", "-U", "docket", "-d", "docket", "-n", schema],
        capture_output=True,
        text=True,
        timeout=120,
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 0, result.stderr
    assert "CREATE TABLE" in result.stdout
    return result.stdout


async def sample(http, headers):
    # Each call is its own browser: only the cookie a test passes is sent.
    http.cookies.clear()
    return await http.post("/beta/samples", json={"case_type": "clean"}, headers=headers)


# S1


async def test_the_first_write_sets_the_cookie_with_exactly_its_flags_and_stores_its_hash(
    postgres_engine,
):
    async with beta_client(engine=postgres_engine) as (http, _, _, app):
        app.state.ingress = ingress = FakeIngress()
        first = await sample(http, sender("192.0.2.10"))
        assert first.status_code == 200, first.text
        assert first.headers["cache-control"] == "no-store"
        name, _, rest = first.headers["set-cookie"].partition("=")
        value, *flags = rest.split("; ")
        assert name == SANDBOX_COOKIE == "__Host-docket_sandbox"
        assert flags[:4] == ["HttpOnly", "Secure", "SameSite=Strict", "Path=/"]
        assert len(flags) == 5 and flags[4].startswith("Max-Age=")
        # Max-Age is the whole seconds from the database's clock, read after the transaction
        # that made the sandbox, to expires_at. created_at is that transaction's clock, so
        # Max-Age is below expires_at - created_at; and it is at least the whole seconds left
        # by the same clock read again after the answer.
        max_age = int(flags[4].removeprefix("Max-Age="))
        with Session(postgres_engine) as session:
            made, expires = session.execute(
                select(Sandbox.created_at, Sandbox.expires_at).where(
                    Sandbox.token_hash == hashlib.sha256(value.encode()).hexdigest()
                )
            ).one()
            after = session.scalar(select(func.now()))
        assert expires - made == timedelta(seconds=LIFE_SECONDS)
        assert math.floor((expires - after).total_seconds()) <= max_age
        assert max_age <= math.ceil((expires - made).total_seconds()) - 1
        assert len(base64.urlsafe_b64decode(value + "==")) == 16
        # The next write with the cookie goes to the same sandbox and sets nothing.
        again = await sample(http, sender("192.0.2.10", value))
        assert again.status_code == 200 and "set-cookie" not in again.headers
    with Session(postgres_engine) as session:
        [stored] = session.scalars(select(Sandbox)).all()
    assert stored.token_hash == hashlib.sha256(value.encode()).hexdigest()
    assert ingress.sandboxes == [stored.id, stored.id]
    dump = pg_dump(postgres_engine)
    assert stored.token_hash in dump
    assert value not in dump


async def test_in_its_last_five_minutes_a_sandbox_refuses_the_next_write_and_starts_no_other(
    postgres_engine,
):
    ending, ending_token = make_sandbox(postgres_engine, minutes_left=4.5)
    lasting, lasting_token = make_sandbox(postgres_engine, minutes_left=6)
    async with beta_client(engine=postgres_engine) as (http, _, _, app):
        app.state.ingress = ingress = FakeIngress()
        kept = await sample(http, sender("192.0.2.11", lasting_token))
        refused = await sample(http, sender("192.0.2.11", ending_token))
    assert kept.status_code == 200 and "set-cookie" not in kept.headers
    assert refused.status_code == 409
    assert refused.json() == {"detail": SANDBOX_ENDING, "code": "sandbox_ending"}
    # The cookie still opens the sandbox's documents and Delete now, so it is kept.
    assert "set-cookie" not in refused.headers
    assert ingress.sandboxes == [lasting]
    assert rows(postgres_engine)["sandboxes"] == 2
    assert rows(postgres_engine)["sample_sends"] == 1


async def test_a_sample_whose_sandbox_was_deleted_first_is_a_409_that_spends_its_cookie(
    postgres_engine,
):
    async with beta_client(engine=postgres_engine) as (http, _, _, app):
        app.state.ingress = FakeIngress(SandboxClosed("gone"))
        response = await sample(http, sender("192.0.2.12"))
    assert response.status_code == 409
    assert response.json() == {"detail": SANDBOX_ENDED, "code": "sandbox_ended"}
    assert response.headers["set-cookie"] == spent_cookie()


async def test_a_request_with_a_cookie_never_starts_a_sandbox(postgres_engine):
    expired, expired_token = make_sandbox(postgres_engine, minutes_left=-1)
    closed, closed_token = make_sandbox(postgres_engine)
    close_sandbox(postgres_engine, closed)
    async with beta_client(engine=postgres_engine) as (http, _, _, app):
        app.state.ingress = ingress = FakeIngress()
        before = rows(postgres_engine)
        for token in ("not-a-sandbox", expired_token, closed_token):
            refused = await sample(http, sender("192.0.2.16", token))
            assert refused.status_code == 409, (token, refused.text)
            assert refused.json() == {"detail": SANDBOX_ENDED, "code": "sandbox_ended"}
            assert refused.headers["set-cookie"] == spent_cookie()
        # Nothing was written for them: no sandbox, no count against the address, no slot.
        assert rows(postgres_engine) == before
        assert ingress.sandboxes == []
        # Only the same browser without its cookie starts one.
        started = await sample(http, sender("192.0.2.16"))
    assert started.status_code == 200
    assert ingress.sandboxes[0] not in {expired, closed}
    assert created_token(started) not in {expired_token, closed_token}
    assert rows(postgres_engine)["sandboxes"] == before["sandboxes"] + 1


async def test_the_cookie_lasts_only_as_long_as_its_sandbox(postgres_engine):
    class SlowIngress(FakeIngress):
        async def submit(self, content, filename, **kwargs):
            # Stands for a write slow enough that the sandbox's clock has run on.
            with Session(postgres_engine) as session, session.begin():
                session.execute(
                    text("UPDATE sandboxes SET expires_at = now() + interval '100 seconds'")
                )
            return await super().submit(content, filename, **kwargs)

    async with beta_client(engine=postgres_engine) as (http, _, _, app):
        app.state.ingress = SlowIngress()
        response = await sample(http, sender("192.0.2.14"))
    assert response.status_code == 200
    max_age = int(response.headers["set-cookie"].rpartition("Max-Age=")[2])
    with Session(postgres_engine) as session:
        expires = session.scalar(select(Sandbox.expires_at))
        after = session.scalar(select(func.now()))
    # expires_at is 100 seconds past the update's clock, and Max-Age is read by a later one.
    assert math.floor((expires - after).total_seconds()) <= max_age <= 99


async def test_a_first_sample_that_fails_closes_its_sandbox_and_keeps_its_slots(
    postgres_engine,
):
    live, live_token = make_sandbox(postgres_engine)
    async with beta_client(engine=postgres_engine) as (http, _, _, app):
        app.state.ingress = FakeIngress(RuntimeError("Temporal is down"))
        failed = await sample(http, sender("192.0.2.15"))
        # A failure in a sandbox the browser already has leaves it open: its cookie still
        # reaches it.
        again = await sample(http, sender("192.0.2.15", live_token))
    assert failed.status_code == again.status_code == 500
    assert "set-cookie" not in failed.headers
    with Session(postgres_engine) as session:
        made = session.scalars(select(Sandbox).where(Sandbox.id != live)).all()
        assert len(made) == 1 and made[0].closed_at is not None
        assert session.get(Sandbox, live).closed_at is None
    assert rows(postgres_engine)["sandbox_creations"] == 1
    assert rows(postgres_engine)["sample_sends"] == 2


# S3


async def test_no_get_writes_anything(postgres_engine):
    live, live_token = make_sandbox(postgres_engine)
    _, expired_token = make_sandbox(postgres_engine, minutes_left=-1)
    document = held_in(postgres_engine, live)
    async with beta_client(engine=postgres_engine) as (http, _, _, app):
        before = rows(postgres_engine)
        gets = [template for method, template in route_table(app) if method == "GET"]
        assert ("GET", "/beta/samples") in route_table(app)
        for template in gets:
            path = concrete(template.replace("{document_id}", str(document)))
            for token in (None, "not-a-sandbox", expired_token, live_token):
                await http.get(path, headers=sender("192.0.2.13", token))
    assert rows(postgres_engine) == before


async def test_the_eleventh_new_sandbox_in_an_hour_from_one_address_is_refused(postgres_engine):
    async with beta_client(engine=postgres_engine) as (http, _, _, app):
        app.state.ingress = FakeIngress()
        tokens = []
        for _ in range(10):
            response = await sample(http, sender("192.0.2.20"))
            assert response.status_code == 200, response.text
            tokens.append(created_token(response))
        refused = await sample(http, sender("192.0.2.20"))
        assert refused.status_code == 429
        assert refused.json() == {"detail": TOO_MANY_SANDBOXES}
        assert "set-cookie" not in refused.headers
        # The refusal took no sample slot, and a sandbox already made still takes samples.
        assert (await sample(http, sender("192.0.2.20", tokens[0]))).status_code == 200
        assert (await sample(http, sender("192.0.2.21"))).status_code == 200
    hashed = address_hash(beta_settings(), "192.0.2.20")
    with Session(postgres_engine) as session:
        assert session.scalar(select(func.count()).select_from(Sandbox)) == 11
        assert (
            session.scalar(select(func.count()).where(SandboxCreation.address_hash == hashed)) == 10
        )
        assert session.scalar(select(func.count()).where(SampleSend.address_hash == hashed)) == 11


async def test_two_addresses_in_one_ipv6_64_share_the_cap(postgres_engine):
    async with beta_client(engine=postgres_engine) as (http, _, _, app):
        app.state.ingress = FakeIngress()
        for index in range(10):
            response = await sample(http, sender(f"2001:db8:5:6::{index + 1:x}"))
            assert response.status_code == 200, response.text
        neighbour = await sample(http, sender("2001:db8:5:6:ffff::1"))
        assert neighbour.status_code == 429
        elsewhere = await sample(http, sender("2001:db8:5:7::1"))
        assert elsewhere.status_code == 200


async def test_a_refused_sample_makes_no_sandbox(postgres_engine):
    hashed = address_hash(beta_settings(), "192.0.2.30")
    with Session(postgres_engine) as session, session.begin():
        for _ in range(20):
            session.add(SampleSend(address_hash=hashed))
    async with beta_client(engine=postgres_engine) as (http, _, _, app):
        app.state.ingress = ingress = FakeIngress()
        response = await sample(http, sender("192.0.2.30"))
    assert response.status_code == 429 and "set-cookie" not in response.headers
    assert ingress.sandboxes == []
    assert rows(postgres_engine)["sandboxes"] == rows(postgres_engine)["sandbox_creations"] == 0


# S11


async def test_the_database_holds_no_client_address(postgres_engine):
    addresses = ["203.0.113.77", "2001:db8:77:1::5"]
    async with beta_client(engine=postgres_engine) as (http, _, _, app):
        app.state.ingress = FakeIngress()
        for address in addresses:
            assert (await sample(http, sender(address))).status_code == 200
    dump = pg_dump(postgres_engine)
    for value in addresses + ["2001:db8:77:1::/64", "2001:db8:77:1"]:
        assert value not in dump, value
    # The counters did store something for each: the keyed hash.
    for value in ["203.0.113.77", "2001:db8:77:1::/64"]:
        assert address_hash(beta_settings(), value) in dump


KNOWN_KEY = "0123456789abcdef" * 4


def keyed(value: str) -> str:
    """HMAC-SHA256 under KNOWN_KEY, worked out here rather than by docket's helper."""
    return hmac.new(KNOWN_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()


async def test_the_counters_store_the_keyed_digest_and_never_the_address(postgres_engine):
    from pydantic import SecretStr

    settings = beta_settings(docket_address_key=SecretStr(KNOWN_KEY))
    async with beta_client(settings, engine=postgres_engine) as (http, _, _, app):
        app.state.ingress = FakeIngress()
        for address in ("203.0.113.77", "2001:db8:77:1::5"):
            assert (await sample(http, sender(address))).status_code == 200
    # An IPv6 address counts by its /64.
    expected = sorted([keyed("203.0.113.77"), keyed("2001:db8:77:1::/64")])
    with Session(postgres_engine) as session:
        for table in (SampleSend, SandboxCreation):
            stored = list(session.scalars(select(table.address_hash)))
            assert sorted(stored) == expected, table.__tablename__
            for value in stored:
                assert "203.0.113" not in value and "2001:db8" not in value, value


async def test_the_sandbox_routes_answer_404_locally():
    from pydantic import SecretStr

    from docket.api import create_app
    from tests.test_beta_gate import FakeTemporal, StartedRuntime

    runtime = StartedRuntime(
        beta_settings(docket_env="local", docket_review_token=SecretStr("t" * 32))
    )
    app = create_app(runtime, FakeTemporal())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as http:
            auth = {"Authorization": "Bearer " + "t" * 32, "Origin": BASE}
            for method in ("GET", "DELETE"):
                response = await http.request(method, "/sandbox", headers=auth)
                assert response.status_code == 404, method
