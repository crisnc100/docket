"""The beta's sample endpoint, its caps and the kill switch (plan PR B, B2 to B6)."""

import asyncio
import os
import uuid
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

import httpx2
import pytest
from pydantic import SecretStr
from sqlalchemy import func, insert, select, text
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from docket.beta import address_hash
from docket.beta_samples import (
    DAY_CAPPED,
    PAUSED,
    SAMPLE_LABELS,
    SAMPLE_SENDER,
    TURNED_OFF,
    address_capped,
    document_ceiling,
    live_answers_on,
    no_reset_running,
    set_live_answers,
)
from docket.cli import app as cli
from docket.config import load_records
from docket.corpus import NOT_SAMPLES, SAMPLE_CASE_TYPES
from docket.db import BudgetLedger, SampleCount, SampleSend
from docket.llm.anthropic import AnthropicTransport, OutputMode
from docket.llm.budget import load_prices
from docket.llm.errors import BudgetExceeded
from docket.prompts import classify_request
from docket.runtime import Runtime
from tests.test_anthropic_transport import FakeApi, message
from tests.test_beta_gate import BASE, beta_client, beta_settings, cookie, open_sandbox, route_table


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("DOCKET_FAILPOINT", "DOCKET_DAILY_BUDGET_USD", "DOCKET_ENV")):
            monkeypatch.delenv(name)


class FakeIngress:
    def __init__(self):
        self.submitted = []

    async def submit(self, content, filename, **kwargs):
        self.submitted.append((content, filename, kwargs))
        return str(uuid.uuid4())


def visitor(address: str = "198.51.100.7") -> dict:
    return cookie() | {"Origin": BASE, "X-Forwarded-For": address}


def hashed(address: str) -> str:
    return address_hash(beta_settings(), address)


# Every address these tests send from, so a stored hash can be named by its address.
ADDRESSES = {hashed(f"198.51.100.{index}"): f"198.51.100.{index}" for index in range(256)}


def counts(engine) -> dict[str, int]:
    """Samples counted so far: per address in the rolling window, and for the day."""
    with Session(engine) as session:
        sends = session.execute(
            select(SampleSend.address_hash, func.count()).group_by(SampleSend.address_hash)
        ).all()
        days = session.scalars(select(SampleCount.count)).all()
    return {"address:" + ADDRESSES[key]: total for key, total in sends} | (
        {"day": sum(days)} if days else {}
    )


def spend(engine, amount: Decimal) -> None:
    from datetime import UTC, datetime

    with Session(engine) as session, session.begin():
        session.execute(insert(BudgetLedger).values(day=datetime.now(UTC).date(), spent_usd=amount))


def ceiling() -> Decimal:
    return document_ceiling(
        load_prices().ceiling("claude-haiku-5-5"), [item.record_id for item in load_records()]
    )


# The page builds its picker from the server's list.


async def test_the_options_list_every_sample_type_with_a_plain_label(postgres_engine):
    async with beta_client(engine=postgres_engine) as (http, _, _, _):
        response = await http.get("/beta/samples", headers=visitor())
        assert response.status_code == 200
        assert response.json() == {
            "state": "ready",
            "case_types": [
                {"value": value, "label": SAMPLE_LABELS[value]} for value in SAMPLE_CASE_TYPES
            ],
        }
    assert tuple(SAMPLE_LABELS) == SAMPLE_CASE_TYPES


async def test_a_sample_goes_to_ingress_with_server_made_bytes_and_counts_once(postgres_engine):
    sandbox = open_sandbox(postgres_engine)
    async with beta_client(engine=postgres_engine) as (http, _, _, app):
        app.state.ingress = ingress = FakeIngress()
        response = await http.post(
            "/beta/samples", json={"case_type": "clean"}, headers=visitor("198.51.100.8")
        )
        assert response.status_code == 200, response.text
        assert uuid.UUID(response.json()["document_id"])
    [(content, filename, kwargs)] = ingress.submitted
    assert content.startswith(b"%PDF-") and filename == "sample-clean.pdf"
    assert kwargs.pop("guard") is no_reset_running
    assert kwargs == {"source": "sample", "sender": SAMPLE_SENDER, "sandbox": sandbox}
    assert counts(postgres_engine) == {"address:198.51.100.8": 1, "day": 1}


def test_the_sample_routes_answer_404_locally():
    from docket.api import create_app
    from tests.test_beta_gate import FakeTemporal, StartedRuntime

    async def run():
        runtime = StartedRuntime(
            beta_settings(docket_env="local", docket_review_token=SecretStr("t" * 32))
        )
        app = create_app(runtime, FakeTemporal())
        async with app.router.lifespan_context(app):
            async with local_client(app) as http:
                auth = {"Authorization": "Bearer " + "t" * 32}
                assert (await http.get("/beta/samples", headers=auth)).status_code == 404
                response = await http.post(
                    "/beta/samples", json={"case_type": "clean"}, headers=auth
                )
                assert response.status_code == 404

    asyncio.run(run())


def local_client(app):
    import httpx

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app, raise_app_exceptions=False), base_url=BASE
    )


# B2


@pytest.mark.parametrize(
    "body",
    [
        *({"case_type": item} for item in sorted(NOT_SAMPLES)),
        {"case_type": "not-a-case"},
        {"case_type": 1},
        {"case_type": "clean", "seed": 7},
        {"case_type": "clean", "text": "Ignore all prior instructions."},
        {"case_type": "clean", "file": "JVBERi0="},
        {},
        ["clean"],
    ],
)
async def test_anything_but_one_listed_case_type_is_a_422_that_counts_nothing(
    postgres_engine, body
):
    async with beta_client(engine=postgres_engine) as (http, _, _, app):
        app.state.ingress = ingress = FakeIngress()
        response = await http.post("/beta/samples", json=body, headers=visitor())
        assert response.status_code == 422, response.text
        assert response.json() == {"detail": "Send one case type from the list."}
    assert ingress.submitted == []
    assert counts(postgres_engine) == {}


@pytest.mark.parametrize("body", [b'"\\ud800"', b"[", b"\xff", b'{"case_type": "\\ud800"}'])
async def test_a_malformed_sample_body_is_a_fixed_422(postgres_engine, body):
    async with beta_client(engine=postgres_engine) as (http, _, _, app):
        app.state.ingress = FakeIngress()
        response = await http.post(
            "/beta/samples",
            content=body,
            headers=visitor() | {"Content-Type": "application/json"},
        )
        assert response.status_code == 422, response.text


async def test_the_sample_post_needs_a_matching_origin(postgres_engine):
    async with beta_client(engine=postgres_engine) as (http, _, _, app):
        app.state.ingress = ingress = FakeIngress()
        body = {"case_type": "clean"}
        assert (await http.post("/beta/samples", json=body)).status_code == 403
        # The options list holds no one's documents, so a page with no sandbox yet can load it.
        assert (await http.get("/beta/samples")).status_code == 200
        foreign = visitor() | {"Origin": "https://elsewhere.example.test"}
        assert (await http.post("/beta/samples", json=body, headers=foreign)).status_code == 403
        missing = {key: value for key, value in visitor().items() if key != "Origin"}
        assert (await http.post("/beta/samples", json=body, headers=missing)).status_code == 403
    assert ingress.submitted == []


# B3


async def test_fifty_parallel_samples_from_one_address_let_exactly_twenty_through(
    postgres_engine,
):
    # One browser: every sample goes to its sandbox, so only the sample cap is in play.
    open_sandbox(postgres_engine)
    async with beta_client(engine=postgres_engine) as (http, _, _, app):
        app.state.ingress = FakeIngress()
        responses = await asyncio.gather(
            *(
                http.post("/beta/samples", json={"case_type": "clean"}, headers=visitor())
                for _ in range(50)
            )
        )
        statuses = [response.status_code for response in responses]
        assert statuses.count(200) == 20 and statuses.count(429) == 30
        refused = next(response for response in responses if response.status_code == 429)
        wait = int(refused.headers["retry-after"])
        assert 1 <= wait <= 3600
        assert refused.json() == {"detail": address_capped(wait)}
        other = await http.post(
            "/beta/samples", json={"case_type": "clean"}, headers=visitor("198.51.100.99")
        )
        assert other.status_code == 200
    assert counts(postgres_engine) == {
        "address:198.51.100.7": 20,
        "address:198.51.100.99": 1,
        "day": 21,
    }
    # A new process reads the same counts.
    async with beta_client(engine=postgres_engine) as (http, _, _, app):
        app.state.ingress = FakeIngress()
        again = await http.post("/beta/samples", json={"case_type": "clean"}, headers=visitor())
        assert again.status_code == 429


async def test_the_days_301st_sample_is_refused_and_takes_no_address_slot(postgres_engine):
    async with beta_client(engine=postgres_engine) as (http, _, _, app):
        app.state.ingress = FakeIngress()
        assert (
            await http.post("/beta/samples", json={"case_type": "clean"}, headers=visitor())
        ).status_code == 200
    with Session(postgres_engine) as session, session.begin():
        session.execute(text("UPDATE sample_counts SET count = 299"))
    async with beta_client(engine=postgres_engine) as (http, _, _, app):
        app.state.ingress = ingress = FakeIngress()
        last = await http.post(
            "/beta/samples", json={"case_type": "clean"}, headers=visitor("198.51.100.20")
        )
        assert last.status_code == 200
        over = await http.post(
            "/beta/samples", json={"case_type": "clean"}, headers=visitor("198.51.100.21")
        )
        assert over.status_code == 429
        assert over.json() == {"detail": DAY_CAPPED}
        assert 1 <= int(over.headers["retry-after"]) <= 24 * 3600
    assert len(ingress.submitted) == 1
    assert counts(postgres_engine)["day"] == 300
    assert "address:198.51.100.21" not in counts(postgres_engine)


def sent_earlier(engine, address: str, when: str, times: int = 20) -> None:
    with Session(engine) as session, session.begin():
        for _ in range(times):
            session.execute(
                text(f"INSERT INTO sample_sends (address_hash, at) VALUES (:hash, {when})"),
                {"hash": hashed(address)},
            )


async def test_the_address_cap_rolls_across_the_clock_hour(postgres_engine):
    address = "198.51.100.30"
    # Twenty samples in the last moment of the previous clock hour: the 21st, sent in this
    # clock hour and within 60 minutes of them, is refused.
    sent_earlier(postgres_engine, address, "date_trunc('hour', now()) - interval '1 millisecond'")
    async with beta_client(engine=postgres_engine) as (http, _, _, app):
        app.state.ingress = ingress = FakeIngress()
        response = await http.post(
            "/beta/samples", json={"case_type": "clean"}, headers=visitor(address)
        )
        assert response.status_code == 429
        wait = int(response.headers["retry-after"])
        assert 1 <= wait <= 3600
        assert response.json() == {"detail": address_capped(wait)}
    assert ingress.submitted == []
    assert counts(postgres_engine) == {"address:" + address: 20}


async def test_samples_older_than_sixty_minutes_stop_counting(postgres_engine):
    address = "198.51.100.31"
    sent_earlier(postgres_engine, address, "now() - interval '61 minutes'")
    sent_earlier(postgres_engine, address, "now() - interval '59 minutes'", times=19)
    async with beta_client(engine=postgres_engine) as (http, _, _, app):
        app.state.ingress = FakeIngress()
        body = {"case_type": "clean"}
        assert (
            await http.post("/beta/samples", json=body, headers=visitor(address))
        ).status_code == 200
        refused = await http.post("/beta/samples", json=body, headers=visitor(address))
        assert refused.status_code == 429
        # The oldest counted sample leaves the window in about one minute.
        assert 1 <= int(refused.headers["retry-after"]) <= 61
    assert counts(postgres_engine) == {"address:" + address: 20, "day": 1}


# B4


async def test_with_less_than_one_document_left_the_button_gets_paused_for_today(
    postgres_engine,
):
    limit = Decimal("1.00")
    spend(postgres_engine, limit - ceiling() + Decimal("0.000000001"))
    async with beta_client(engine=postgres_engine) as (http, _, _, app):
        app.state.ingress = ingress = FakeIngress()
        assert (await http.get("/beta/samples", headers=visitor())).json()["state"] == "paused"
        response = await http.post("/beta/samples", json={"case_type": "clean"}, headers=visitor())
        assert response.status_code == 503
        assert response.json() == {"detail": PAUSED}
    assert ingress.submitted == [] and counts(postgres_engine) == {}


async def test_exactly_one_document_left_still_sends(postgres_engine):
    spend(postgres_engine, Decimal("1.00") - ceiling())
    async with beta_client(engine=postgres_engine) as (http, _, _, app):
        app.state.ingress = FakeIngress()
        response = await http.post("/beta/samples", json={"case_type": "clean"}, headers=visitor())
        assert response.status_code == 200


def test_one_documents_ceiling_covers_both_calls_on_the_longest_text():
    value = ceiling()
    prices = load_prices().ceiling("claude-haiku-5-5")
    # The text is at most 8,000 characters, so two calls of at least 8,000 bytes each.
    assert value > 2 * prices.worst_case("x" * 8000, 256)
    assert Decimal("0.005") < value < Decimal("0.05")


def test_the_page_explains_a_budget_failure_in_plain_words():
    page = Path("web/src/main.ts").read_text()
    assert "budget_exceeded:" in page
    assert (
        "Live answers stopped before this document finished. Today's budget ran out, or live "
        "answers were turned off."
    ) in page


@pytest.mark.parametrize(
    "seconds,text",
    [
        (1, "Try again in 1 minute."),
        (60, "Try again in 1 minute."),
        (61, "Try again in 2 minutes."),
        (3600, "Try again in 60 minutes."),
    ],
)
def test_the_capped_message_names_the_wait_in_whole_minutes_rounded_up(seconds, text):
    assert address_capped(seconds) == f"You can send 20 samples an hour. {text}"


async def test_the_pause_lifts_when_the_utc_day_changes(postgres_engine):
    from datetime import UTC, datetime, timedelta

    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    with Session(postgres_engine) as session, session.begin():
        session.execute(insert(BudgetLedger).values(day=yesterday, spent_usd=Decimal("1.00")))
    async with beta_client(engine=postgres_engine) as (http, _, _, _):
        assert (await http.get("/beta/samples", headers=visitor())).json()["state"] == "ready"
    spend(postgres_engine, Decimal("1.00"))
    async with beta_client(engine=postgres_engine) as (http, _, _, _):
        assert (await http.get("/beta/samples", headers=visitor())).json()["state"] == "paused"


# B5


async def test_switching_off_refuses_samples_with_no_restart_and_on_restores_them(
    postgres_engine,
):
    async with beta_client(engine=postgres_engine) as (http, _, _, app):
        app.state.ingress = ingress = FakeIngress()
        body = {"case_type": "clean"}
        set_live_answers(postgres_engine, False)
        assert (await http.get("/beta/samples", headers=visitor())).json()["state"] == "off"
        off = await http.post("/beta/samples", json=body, headers=visitor())
        assert off.status_code == 503 and off.json() == {"detail": TURNED_OFF}
        assert ingress.submitted == [] and counts(postgres_engine) == {}
        set_live_answers(postgres_engine, True)
        assert (await http.get("/beta/samples", headers=visitor())).json()["state"] == "ready"
        assert (await http.post("/beta/samples", json=body, headers=visitor())).status_code == 200


def live_runtime(engine, monkeypatch, api, **settings) -> Runtime:
    runtime = Runtime(
        beta_settings(
            anthropic_api_key=SecretStr("test-key"),
            docket_model_mode="live",
            **settings,
        )
    )
    runtime.engine.dispose()
    runtime.engine = engine

    def transport(settings, max_output_tokens):
        client = httpx2.AsyncClient(transport=httpx2.MockTransport(api))
        # A mode of its own, so a schema refusal here never changes other tests' transports.
        return AnthropicTransport(settings, max_output_tokens, client, mode=OutputMode())

    monkeypatch.setattr("docket.runtime.AnthropicTransport", transport)
    return runtime


def live_request(document_id: str | None = None) -> dict:
    return {
        "document_id": document_id or str(uuid.uuid4()),
        "run_id": "live",
        "model_mode": "live",
        "model": "claude-haiku-5-5",
        "budget": "1.00",
    }


async def test_the_switch_stops_every_live_call_at_the_transport_and_on_restores_it(
    postgres_engine, monkeypatch
):
    api = FakeApi(message('{"category":"lease"}'), message('{"category":"lease"}'))
    runtime = live_runtime(postgres_engine, monkeypatch, api)
    gateway = runtime.gateway(live_request())
    call = {**classify_request("Lease for unit 1"), "source": "sample"}
    set_live_answers(postgres_engine, False)
    for _ in range(3):
        with pytest.raises(BudgetExceeded, match="turned off"):
            await gateway.call(**call)
    assert api.requests == [] and gateway.call_count == 0
    with Session(postgres_engine) as session:
        assert session.scalar(select(BudgetLedger.spent_usd)) is None
    # The same gateway, in the same process, calls again once the switch is back on.
    set_live_answers(postgres_engine, True)
    assert (await gateway.call(**call)).category.value == "lease"
    assert len(api.requests) == 1


async def test_the_switch_also_stops_the_resend_after_a_schema_refusal(
    postgres_engine, monkeypatch
):
    requests = []

    async def api(request):
        requests.append(request)
        # The switch goes off while the first request is answered with a schema refusal,
        # so the transport's resend in the other output mode must not go out.
        set_live_answers(postgres_engine, False)
        return httpx2.Response(
            400,
            json={
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "output_config.format: the compiled grammar is too large",
                },
            },
        )

    runtime = live_runtime(postgres_engine, monkeypatch, api)
    gateway = runtime.gateway(live_request())
    with pytest.raises(BudgetExceeded, match="turned off"):
        await gateway.call(**classify_request("Lease for unit 1"), source="sample")
    assert len(requests) == 1
    # Nothing was billed, so the reservation is given back.
    with Session(postgres_engine) as session:
        assert session.scalar(select(BudgetLedger.spent_usd)) == 0


async def test_a_failed_switch_read_at_the_transport_gives_the_reservation_back(
    postgres_engine, monkeypatch
):
    api = FakeApi(message('{"category":"lease"}'))
    runtime = live_runtime(postgres_engine, monkeypatch, api)
    gateway = runtime.gateway(live_request())
    reads = []

    def reader():
        # The gateway's own read passes; the transport's read, and every read after it, fail.
        reads.append(len(reads))
        if len(reads) > 1:
            raise OSError("database unreachable")
        return True

    async def no_wait(seconds):
        pass

    gateway.live_allowed = gateway.transport.live_allowed = reader
    gateway.sleep = no_wait
    with pytest.raises(OSError, match="unreachable"):
        await gateway.call(**classify_request("Lease for unit 1"), source="sample")
    assert api.requests == [] and len(reads) == 3
    with Session(postgres_engine) as session:
        assert session.scalar(select(BudgetLedger.spent_usd)) == 0


async def test_a_refund_that_cannot_be_written_stops_the_call_and_is_logged(
    postgres_engine, monkeypatch, caplog
):
    api = FakeApi(message('{"category":"lease"}'))
    runtime = live_runtime(postgres_engine, monkeypatch, api)
    gateway = runtime.gateway(live_request())
    reads = []

    def reader():
        reads.append(1)
        if len(reads) > 1:
            raise OSError("database unreachable")
        return True

    def settle(*args):
        raise OSError("database unreachable during the refund")

    gateway.live_allowed = gateway.transport.live_allowed = reader
    assert gateway.budget is not None
    monkeypatch.setattr(gateway.budget.daily, "settle", settle)
    with pytest.raises(OSError, match="during the refund"):
        await gateway.call(**classify_request("Lease for unit 1"), source="sample")
    # Fails closed: no request went out, and the stuck reservation is named in the log.
    assert api.requests == [] and len(reads) == 2
    assert "Could not give back a $" in caplog.text


async def test_local_gateways_never_read_the_switch(postgres_engine, monkeypatch):
    api = FakeApi(message('{"category":"lease"}'))
    runtime = live_runtime(postgres_engine, monkeypatch, api, docket_env="local")
    gateway = runtime.gateway(live_request())
    assert gateway.live_allowed is None
    set_live_answers(postgres_engine, False)
    await gateway.call(**classify_request("Lease for unit 1"), source="http")
    assert len(api.requests) == 1


def test_no_row_means_live_answers_are_on(postgres_engine):
    assert live_answers_on(postgres_engine)
    set_live_answers(postgres_engine, False)
    set_live_answers(postgres_engine, False)
    assert not live_answers_on(postgres_engine)


def schema_url(engine) -> str:
    with engine.connect() as connection:
        schema = connection.execute(text("SELECT current_schema()")).scalar_one()
    base = engine.url.render_as_string(hide_password=False)
    return f"{base}?options={quote('-csearch_path=' + schema)}"


def test_the_beta_switch_command_flips_the_switch_and_refuses_outside_beta(
    postgres_engine, monkeypatch
):
    monkeypatch.setenv("DATABASE_URL", schema_url(postgres_engine))
    runner = CliRunner()
    local = runner.invoke(cli, ["beta-switch", "off"])
    assert local.exit_code == 1 and "only with DOCKET_ENV=beta" in local.output
    assert live_answers_on(postgres_engine)
    monkeypatch.setenv("DOCKET_ENV", "beta")
    assert runner.invoke(cli, ["beta-switch", "maybe"]).exit_code == 2
    off = runner.invoke(cli, ["beta-switch", "off"])
    assert off.exit_code == 0, off.output
    assert not live_answers_on(postgres_engine)
    assert runner.invoke(cli, ["beta-switch", "on"]).exit_code == 0
    assert live_answers_on(postgres_engine)


# B6


async def test_no_beta_route_lets_visitor_bytes_reach_ingress_or_storage(postgres_engine):
    async with beta_client(engine=postgres_engine) as (http, _, _, app):
        writes = {
            (method, path) for method, path in route_table(app) if method not in {"GET", "HEAD"}
        }
        # Each write route and what it stores: uploads are refused before their body is read
        # (tests/test_beta_gate.py), review stores a capped dismiss reason or a candidate id,
        # and a sample stores server-made bytes.
        assert writes == {
            ("POST", "/documents"),
            ("POST", "/inbound"),
            ("POST", "/review/{document_id}/{action}"),
            ("POST", "/beta/samples"),
        }
        for path in ["/documents", "/inbound"]:
            response = await http.post(path, headers=visitor(), content=b"%PDF-1.7 visitor")
            assert response.status_code == 403
        app.state.ingress = ingress = FakeIngress()
        response = await http.post(
            "/beta/samples",
            json={"case_type": "injection"},
            headers=visitor() | {"X-Docket-Filename": "visitor.pdf"},
        )
        assert response.status_code == 200
    [(content, filename, kwargs)] = ingress.submitted
    assert filename == "sample-injection.pdf"
    assert kwargs["sender"] == SAMPLE_SENDER
    assert b"visitor" not in content


async def test_the_spike_command_reads_the_switch_in_beta(postgres_engine, tmp_path, monkeypatch):
    from docket.llm import spike as spike_module

    api = FakeApi()

    def transport(settings, max_output_tokens):
        client = httpx2.AsyncClient(transport=httpx2.MockTransport(api))
        return AnthropicTransport(settings, max_output_tokens, client)

    monkeypatch.setattr(spike_module, "create_engine", lambda url: postgres_engine)
    monkeypatch.setattr(spike_module, "AnthropicTransport", transport)
    set_live_answers(postgres_engine, False)
    settings = beta_settings(anthropic_api_key=SecretStr("test-key"))
    with pytest.raises(BudgetExceeded, match="turned off"):
        await spike_module.run_spike(settings, tmp_path / "spike.json", tmp_path / "adr.md")
    assert api.requests == []
