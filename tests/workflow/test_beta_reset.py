"""The nightly beta reset (plan PR C, C1), through the real workflow and replayed answers."""

import json
import os
import threading
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

import httpx
import pytest
from sqlalchemy import create_engine, func, insert, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from typer.testing import CliRunner

from docket import beta_reset
from docket.activities import Activities
from docket.api import create_app
from docket.beta_reset import STARTING_QUEUE, due, next_slot, reset, run_service
from docket.beta_samples import (
    RESET_RUNNING,
    RESETTING,
    ResetRunning,
    SampleGate,
    flag,
    no_reset_running,
)
from docket.cli import app as cli
from docket.db import (
    BudgetLedger,
    Document,
    Event,
    FiledDocument,
    SampleCount,
    SampleSend,
    SandboxCreation,
)
from docket.runtime import Runtime
from docket.workflows import FileDocumentWorkflow
from tests.test_beta_gate import BASE, FakeTemporal, beta_settings, cookie

DAY = timedelta(days=1)


class EngineOnly:
    def __init__(self, engine):
        self.engine = engine


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("DOCKET_FAILPOINT", "DOCKET_DAILY_BUDGET_USD", "DOCKET_ENV")):
            monkeypatch.delenv(name)


@pytest.fixture()
def beta_runtime(postgres_engine, monkeypatch):
    def no_live_calls(*args, **kwargs):
        raise AssertionError("The reset must make no live model call")

    monkeypatch.setattr("docket.runtime.AnthropicTransport", no_live_calls)
    monkeypatch.setattr("docket.runtime.OpenRouterTransport", no_live_calls)
    runtime = Runtime(
        beta_settings(
            s3_bucket="beta-reset-" + uuid.uuid4().hex,
            # Visitors' samples are live; the reset must replay its own documents anyway.
            docket_model_mode="live",
            docket_review_timeout_seconds=86400,
        )
    )
    runtime.engine.dispose()
    runtime.engine = postgres_engine
    runtime.ensure_bucket()
    try:
        yield runtime
    finally:
        beta_reset.empty_bucket(runtime)
        runtime.s3.delete_bucket(Bucket=runtime.settings.s3_bucket)
        runtime.close()


def visitor() -> dict:
    return cookie() | {"Origin": BASE, "X-Forwarded-For": "203.0.113.9"}


def statuses(engine) -> list[str]:
    with Session(engine) as session:
        return sorted(session.scalars(select(Document.status)))


def counter_rows(engine, minutes_ago: list[int]) -> None:
    with Session(engine) as session, session.begin():
        for minutes in minutes_ago:
            at = func.now() - timedelta(minutes=minutes)
            session.execute(insert(SampleSend).values(address_hash="a" * 64, at=at))
            session.execute(insert(SandboxCreation).values(address_hash="a" * 64, at=at))


async def test_the_reset_keeps_the_caps_and_leaves_twelve_documents_waiting(beta_runtime):
    engine = beta_runtime.engine
    today = datetime.now(UTC).date()
    with Session(engine) as session, session.begin():
        session.execute(insert(BudgetLedger).values(day=today, spent_usd=Decimal("0.37")))
        session.execute(insert(SampleCount).values(day=today, count=42))
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=beta_runtime.settings.docket_task_queue,
            workflows=[FileDocumentWorkflow],
            activities=Activities(beta_runtime).registered(),
        ):
            app = create_app(beta_runtime, env.client)
            transport = httpx.ASGITransport(app)
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(transport=transport, base_url=BASE) as http,
            ):
                first = await reset(beta_runtime, env.client)
                assert first["seeded"] == {"needs_review": 12}
                # Every seeded call had a recorded answer.
                with Session(engine) as session:
                    details = list(session.scalars(select(Event.detail)))
                assert "replay_miss" not in json.dumps(details)
                # A day of use: one document approved and filed, the rest still held. The
                # seeded documents belong to no sandbox, so no visitor's queue lists them; the
                # approve goes to the workflow as the review route would send it.
                with Session(engine) as session:
                    held_id, score = session.execute(
                        select(Event.document_id, Event.detail)
                        .where(Event.step == "score")
                        .order_by(Event.id)
                    ).first()
                handle = env.client.get_workflow_handle(f"doc-{held_id}")
                await handle.execute_update(
                    "review_action",
                    {"action": "approve", "value": score["candidates"][0]["record_id"]},
                )
                await handle.result()
                with Session(engine) as session:
                    assert session.scalar(select(func.count()).select_from(FiledDocument)) == 1
                counter_rows(engine, [90, 61, 30, 5])

                during = {}
                close_workflows = beta_reset.close_workflows

                async def sample_during_reset(*args):
                    during["state"] = (await http.get("/beta/samples", headers=visitor())).json()
                    during["send"] = await http.post(
                        "/beta/samples", json={"case_type": "clean"}, headers=visitor()
                    )
                    await close_workflows(*args)

                beta_reset.close_workflows = sample_during_reset
                try:
                    second = await reset(beta_runtime, env.client)
                finally:
                    beta_reset.close_workflows = close_workflows
                assert during["state"]["state"] == "resetting"
                assert during["send"].status_code == 503
                assert during["send"].json() == {"detail": RESET_RUNNING}
                assert second["terminated"] == 11
                assert second["seeded"] == {"needs_review": 12}
                assert statuses(engine) == ["needs_review"] * 12
                with Session(engine) as session:
                    assert session.scalar(select(func.count()).select_from(FiledDocument)) == 0
                    assert session.scalar(select(BudgetLedger.spent_usd)) == Decimal("0.37")
                    assert session.scalar(select(SampleCount.count)) == 42
                    # Rows older than an hour count toward nothing, so only they go.
                    for table in (SampleSend, SandboxCreation):
                        assert session.scalar(select(func.count()).select_from(table)) == 2
                health = (await http.get("/healthz")).json()
                assert health["resetting"] is False and 0 <= health["last_reset_age_seconds"] < 60
                assert (await http.get("/beta/samples", headers=visitor())).json()[
                    "state"
                ] == "ready"

                # Two hours on, with the beta's 24-hour review timeout, all twelve still wait.
                await env.sleep(2 * 3600)
                assert statuses(engine) == ["needs_review"] * 12
                with Session(engine) as session:
                    ids = list(session.scalars(select(Document.id)))
                for document_id in ids:
                    handle = env.client.get_workflow_handle(f"doc-{document_id}")
                    assert (await handle.describe()).status == WorkflowExecutionStatus.RUNNING
                with Session(engine) as session:
                    received = session.scalars(
                        select(Event.detail).where(Event.step == "received")
                    ).all()
                filenames = [detail["workflow_input"]["filename"] for detail in received]
                assert sorted(filenames) == sorted(STARTING_QUEUE)
    # The worker never built a live transport, and nothing was charged.
    assert beta_runtime.transports == {}
    assert {key[2] for key in beta_runtime.gateways} == {"replay"}
    with Session(engine) as session:
        assert session.scalar(select(BudgetLedger.spent_usd)) == Decimal("0.37")


def test_the_reset_command_refuses_outside_beta_and_with_an_unsafe_config(monkeypatch):
    runner = CliRunner()
    local = runner.invoke(cli, ["beta-reset"])
    assert local.exit_code == 1 and "only with DOCKET_ENV=beta" in local.output
    monkeypatch.setenv("DOCKET_ENV", "beta")
    unsafe = runner.invoke(cli, ["beta-reset"])
    assert unsafe.exit_code == 1
    assert unsafe.output.startswith("Docket beta refuses to start:\n- ")


def at(text_time: str) -> datetime:
    return datetime.fromisoformat(text_time).replace(tzinfo=UTC)


def test_a_reset_is_due_once_per_day_after_eight_utc():
    now = at("2026-11-20T10:00:00")
    assert due(False, None, now)
    assert due(True, None, now)
    assert due(False, at("2026-11-20T07:59:59"), now)
    assert not due(False, at("2026-11-20T08:00:01"), now)
    early = at("2026-11-20T07:00:00")
    assert not due(False, at("2026-11-19T08:00:01"), early)
    assert next_slot(now) == at("2026-11-21T08:00:00")
    assert next_slot(early) == at("2026-11-20T08:00:00")


async def test_the_service_catches_up_a_missed_reset_at_startup(
    postgres_engine, monkeypatch, tmp_path
):
    # The host was down at 08:00: the last reset finished yesterday, and it is now 10:00.
    with Session(postgres_engine) as session, session.begin():
        session.execute(
            text(
                "INSERT INTO beta_flags (name, value, updated_at) VALUES ('resetting', false, :at)"
            ),
            {"at": at("2026-11-19T08:00:05")},
        )
    clock = [at("2026-11-20T10:00:00")]
    runs, waits = [], []

    async def fake_reset(runtime, temporal):
        runs.append(clock[0])
        beta_reset.set_flag(postgres_engine, beta_reset.RESETTING, False)
        with Session(postgres_engine) as session, session.begin():
            session.execute(
                text("UPDATE beta_flags SET updated_at = :at WHERE name = 'resetting'"),
                {"at": clock[0]},
            )
        return {}

    class Stop(Exception):
        pass

    async def fake_sleep(seconds):
        waits.append(seconds)
        if len(waits) == 2:
            raise Stop
        clock[0] += timedelta(seconds=seconds)

    monkeypatch.setattr(beta_reset, "reset", fake_reset)
    monkeypatch.setattr(beta_reset, "READY_FILE", tmp_path / "ready")
    with pytest.raises(Stop):
        await run_service(
            cast(Runtime, EngineOnly(postgres_engine)),
            cast(Client, None),
            now=lambda: clock[0],
            sleep=fake_sleep,
        )
    # Caught up at startup, then at the next 08:00, and not again in between.
    assert runs == [at("2026-11-20T10:00:00"), at("2026-11-21T08:00:00")]
    assert waits[0] == (at("2026-11-21T08:00:00") - at("2026-11-20T10:00:00")).total_seconds()
    assert (tmp_path / "ready").is_file()


# The reset lock, per the advisor's design: samples hold it shared only while they write
# their document; the reset takes it exclusive to set its flag.


def test_the_reset_waits_for_a_sample_writing_its_document_and_later_samples_see_the_flag(
    postgres_engine,
):
    sample = Session(postgres_engine)
    sample.begin()
    started = threading.Thread(target=beta_reset.begin, args=(postgres_engine, 5_000))
    try:
        no_reset_running(sample)
        started.start()
        started.join(0.5)
        # The reset is still waiting for the lock, so the flag is not set yet.
        assert started.is_alive() and flag(postgres_engine, RESETTING) is None
    finally:
        sample.rollback()
        sample.close()
    started.join(5)
    assert not started.is_alive() and flag(postgres_engine, RESETTING) is True
    with Session(postgres_engine) as later, later.begin(), pytest.raises(ResetRunning):
        no_reset_running(later)


async def test_a_lock_timeout_still_leaves_the_flag_set(postgres_engine):
    sample = Session(postgres_engine)
    sample.begin()
    no_reset_running(sample)
    try:
        with pytest.raises(OperationalError, match="lock timeout"):
            await reset(
                cast(Runtime, EngineOnly(postgres_engine)), cast(Client, None), lock_timeout_ms=200
            )
    finally:
        sample.rollback()
        sample.close()
    assert beta_reset.reset_record(postgres_engine) == (True, None)


async def test_a_failed_reset_leaves_the_flag_set_and_reruns_from_the_start(
    postgres_engine, monkeypatch, tmp_path
):
    steps = []

    async def failing(temporal, documents):
        steps.append("close")
        raise RuntimeError("Temporal unreachable")

    begin = beta_reset.begin
    monkeypatch.setattr(beta_reset, "begin", lambda *args: steps.append("begin") or begin(*args))
    monkeypatch.setattr(beta_reset, "close_workflows", failing)
    runtime = cast(Runtime, EngineOnly(postgres_engine))
    with pytest.raises(RuntimeError, match="unreachable"):
        await reset(runtime, cast(Client, None))
    running, finished = beta_reset.reset_record(postgres_engine)
    assert (running, finished) == (True, None)
    assert flag(postgres_engine, RESETTING) is True
    assert due(running, finished, at("2026-11-20T08:30:00"))

    # At the next start the service runs the whole reset again, from setting the flag.
    class Stop(Exception):
        pass

    async def stop(seconds):
        raise Stop

    monkeypatch.setattr(beta_reset, "READY_FILE", tmp_path / "ready")
    with pytest.raises(Stop):
        await run_service(runtime, cast(Client, None), sleep=stop)
    assert steps == ["begin", "close", "begin", "close"]


async def test_a_sample_past_the_state_check_is_refused_at_ingress_during_a_reset(
    beta_runtime, monkeypatch
):
    # The race: the sample read the state as ready just before the reset set its flag.
    monkeypatch.setattr(SampleGate, "state", lambda self: "ready")
    beta_reset.begin(beta_runtime.engine, 1000)
    app = create_app(beta_runtime, FakeTemporal())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=BASE) as http,
    ):
        response = await http.post("/beta/samples", json={"case_type": "clean"}, headers=visitor())
    assert response.status_code == 503 and response.json() == {"detail": RESET_RUNNING}
    assert statuses(beta_runtime.engine) == []


async def test_a_reset_whose_queue_does_not_all_end_held_fails_and_keeps_the_flag(
    postgres_engine, monkeypatch
):
    async def no_workflows(temporal, documents):
        pass

    async def short_queue(runtime, temporal):
        return []

    async def one_failed(engine, ids, timeout):
        return {"failed": 1, "needs_review": 11}

    monkeypatch.setattr(beta_reset, "close_workflows", no_workflows)
    monkeypatch.setattr(beta_reset, "empty_bucket", lambda runtime: 0)
    monkeypatch.setattr(beta_reset, "reseed", short_queue)
    monkeypatch.setattr(beta_reset, "settled", one_failed)
    with pytest.raises(beta_reset.IncompleteQueue, match="did not settle"):
        await reset(cast(Runtime, EngineOnly(postgres_engine)), cast(Client, None))
    assert beta_reset.reset_record(postgres_engine) == (True, None)


# f3: one reset at a time


def other_engine(engine):
    """A second engine, as a second process would have. Advisory locks span the database."""
    return create_engine(engine.url.render_as_string(hide_password=False))


async def test_a_reset_refuses_while_another_holds_the_runner_lock_and_leaves_the_flag(
    postgres_engine, monkeypatch
):
    began = []
    monkeypatch.setattr(beta_reset, "begin", lambda *args: began.append(args))
    second = other_engine(postgres_engine)
    try:
        with second.connect() as holder:
            assert holder.execute(
                select(func.pg_try_advisory_lock(beta_reset.RUNNER_LOCK))
            ).scalar_one()
            with pytest.raises(beta_reset.AlreadyRunning, match="already running"):
                await reset(cast(Runtime, EngineOnly(postgres_engine)), cast(Client, None))
            holder.execute(select(func.pg_advisory_unlock(beta_reset.RUNNER_LOCK)))
    finally:
        second.dispose()
    assert began == []
    assert beta_reset.reset_record(postgres_engine) == (False, None)


async def test_a_reset_that_fails_midway_lets_the_next_one_take_the_runner_lock(
    postgres_engine, monkeypatch
):
    async def failing(temporal, documents):
        raise RuntimeError("Temporal unreachable")

    monkeypatch.setattr(beta_reset, "close_workflows", failing)
    with pytest.raises(RuntimeError, match="unreachable"):
        await reset(cast(Runtime, EngineOnly(postgres_engine)), cast(Client, None))
    second = other_engine(postgres_engine)
    try:
        with second.connect() as next_run:
            assert next_run.execute(
                select(func.pg_try_advisory_lock(beta_reset.RUNNER_LOCK))
            ).scalar_one()
            next_run.execute(select(func.pg_advisory_unlock(beta_reset.RUNNER_LOCK)))
    finally:
        second.dispose()


async def test_the_service_logs_one_plain_line_when_another_reset_is_running(
    postgres_engine, monkeypatch, tmp_path, capsys, caplog
):
    async def refused(runtime, temporal):
        raise beta_reset.AlreadyRunning("A reset is already running.")

    class Stop(Exception):
        pass

    waits = []

    async def stop_second(seconds):
        waits.append(seconds)
        if len(waits) == 2:
            raise Stop

    monkeypatch.setattr(beta_reset, "reset", refused)
    monkeypatch.setattr(beta_reset, "READY_FILE", tmp_path / "ready")
    with pytest.raises(Stop):
        await run_service(
            cast(Runtime, EngineOnly(postgres_engine)), cast(Client, None), sleep=stop_second
        )
    assert waits[0] == beta_reset.RETRY_SECONDS
    assert capsys.readouterr().out.startswith(
        "Beta reset skipped: another reset is running. Checking again in 300 seconds.\n"
    )
    assert "Traceback" not in caplog.text
