"""The nightly beta reset (plan PR C, C1), through the real workflow and replayed answers."""

import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

import httpx
import pytest
from sqlalchemy import func, insert, select, text
from sqlalchemy.orm import Session
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from typer.testing import CliRunner

from docket import beta_reset
from docket.activities import Activities
from docket.api import create_app
from docket.beta_reset import STARTING_QUEUE, due, next_slot, reset, run_service
from docket.beta_samples import RESET_RUNNING
from docket.cli import app as cli
from docket.db import (
    BudgetLedger,
    Document,
    Event,
    FiledDocument,
    SampleCount,
    SampleSend,
    SigninFailure,
)
from docket.runtime import Runtime
from docket.workflows import FileDocumentWorkflow
from tests.test_beta_gate import BASE, beta_settings, cookie

DAY = timedelta(days=1)


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
            session.execute(insert(SampleSend).values(address="198.51.100.1", at=at))
            session.execute(insert(SigninFailure).values(address="198.51.100.1", at=at))


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
                # A day of use: one document approved and filed, the rest still held.
                [item, *_] = (await http.get("/review", headers=visitor())).json()["items"]
                approved = await http.post(
                    f"/review/{item['document_id']}/approve",
                    json={"record_id": item["candidates"][0]["record_id"]},
                    headers=visitor(),
                )
                assert approved.status_code == 200, approved.text
                await env.client.get_workflow_handle("doc-" + item["document_id"]).result()
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
                    for table in (SampleSend, SigninFailure):
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
                listed = (await http.get("/review", headers=visitor())).json()["items"]
                assert sorted(item["filename"] for item in listed) == sorted(STARTING_QUEUE)
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

    class EngineOnly:
        engine = postgres_engine

    monkeypatch.setattr(beta_reset, "reset", fake_reset)
    monkeypatch.setattr(beta_reset, "READY_FILE", tmp_path / "ready")
    with pytest.raises(Stop):
        await run_service(
            cast(Runtime, EngineOnly()), cast(Client, None), now=lambda: clock[0], sleep=fake_sleep
        )
    # Caught up at startup, then at the next 08:00, and not again in between.
    assert runs == [at("2026-11-20T10:00:00"), at("2026-11-21T08:00:00")]
    assert waits[0] == (at("2026-11-21T08:00:00") - at("2026-11-20T10:00:00")).total_seconds()
    assert (tmp_path / "ready").is_file()
