"""The sweep against real stores: Postgres, the S3 store and Temporal's dev server on
localhost:7233, which can delete histories (the time-skipping test server cannot). Each test
uses its own task queue, so the sweep sees only its own workflows (plan PR 1, S6a to S6d)."""

import asyncio
import itertools
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, insert, select, text
from sqlalchemy.orm import Session
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode
from typer.testing import CliRunner

from docket import sweep
from docket.api import create_app
from docket.cli import app as cli
from docket.corpus import pdf
from docket.db import BudgetLedger, Document, SampleCount, SampleSend, Sandbox, SandboxCreation
from docket.ingress import Ingress
from docket.sandbox import lock_key, spent_cookie
from docket.sweep import (
    CATCHING_UP,
    ERASE_DELAYED,
    Deadline,
    postgres_pass,
    record_complete,
    sweep_age,
    sweep_once,
    temporal_connector,
)
from tests.sandboxes import held_in, make_sandbox, sandbox_cookie
from tests.test_beta_gate import BASE, KEY

TEMPORAL = "localhost:7233"
CAPS = (Decimal("0.37"), 42)


def set_caps(engine) -> date:
    """Today's spend and sample count, and the UTC day they are keyed by. A cleanup that
    lowered them would lift the daily caps."""
    today = datetime.now(UTC).date()
    with Session(engine) as session, session.begin():
        session.execute(insert(BudgetLedger).values(day=today, spent_usd=CAPS[0]))
        session.execute(insert(SampleCount).values(day=today, count=CAPS[1]))
    return today


def caps(engine, day: date) -> tuple[Decimal | None, int | None]:
    """The spend and sample count, read back from the only rows there are, which must still
    be keyed by the day set_caps used, even if the UTC date has turned since."""
    with Session(engine) as session:
        spend = session.execute(select(BudgetLedger.day, BudgetLedger.spent_usd)).all()
        count = session.execute(select(SampleCount.day, SampleCount.count)).all()
    assert [row[0] for row in spend] == [day] and [row[0] for row in count] == [day]
    return spend[0][1], count[0][1]


@pytest.fixture(autouse=True)
def no_settle(monkeypatch):
    # Only the marker test reads SeaweedFS's disk, so only it waits for the bytes to go.
    monkeypatch.setattr(sweep, "FILER_SETTLE", 0)


@pytest.fixture()
def beta(runtime):
    """The workflow tests' runtime, as the beta runs it, on a task queue of its own."""
    runtime.settings = runtime.settings.model_copy(
        update={
            "docket_env": "beta",
            "docket_address_key": SecretStr(KEY),
            "docket_review_token": None,
            "docket_trust_proxy": True,
            "docket_base_url": BASE,
            "docket_daily_budget_usd": 1.00,
            "docket_task_queue": "sweep-" + uuid.uuid4().hex,
            "seaweedfs_master_url": "http://127.0.0.1:9333",
        }
    )
    return runtime


@pytest.fixture()
async def temporal():
    return await Client.connect(TEMPORAL)


async def connected(client):
    return client


def reachable(client):
    return lambda: connected(client)


async def send(runtime, temporal, sandbox_id, count=2) -> list[str]:
    """Documents in the sandbox, each with its row, its object and its workflow. No worker
    polls the queue, so each workflow waits at its first step."""
    ingress = Ingress(runtime, temporal)
    return [
        await ingress.submit(
            pdf([f"Sweep test {uuid.uuid4().hex}"]), "test.pdf", sandbox=sandbox_id
        )
        for _ in range(count)
    ]


def expire(engine, sandbox_id) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE sandboxes SET expires_at = now() - interval '1 minute' WHERE id = :id"),
            {"id": sandbox_id},
        )


def rows(engine, sandbox_id) -> tuple[int, int]:
    with Session(engine) as session:
        return (
            session.scalar(select(func.count()).where(Sandbox.id == sandbox_id)) or 0,
            session.scalar(select(func.count()).where(Document.sandbox_id == sandbox_id)) or 0,
        )


def objects(runtime, prefix="sbx/") -> list[str]:
    listed = runtime.s3.list_objects_v2(Bucket=runtime.settings.s3_bucket, Prefix=prefix)
    return sorted(item["Key"] for item in listed.get("Contents", []))


async def histories(temporal, document_ids) -> list[str]:
    kept = []
    for document_id in document_ids:
        try:
            await temporal.get_workflow_handle(f"doc-{document_id}").describe()
        except RPCError as exc:
            assert exc.status == RPCStatusCode.NOT_FOUND
        else:
            kept.append(document_id)
    return kept


def age_sweep(engine, minutes: float) -> None:
    record_complete(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE beta_flags SET updated_at = now() - make_interval(mins => :minutes) "
                "WHERE name = 'sweep_complete'"
            ),
            {"minutes": minutes},
        )


# S6a


async def test_a_backlog_goes_documents_first_and_a_cut_short_run_finishes_on_the_next(
    beta, temporal, monkeypatch
):
    engine = beta.engine
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO sandboxes (id, token_hash, created_at, expires_at) "
                "SELECT gen_random_uuid(), md5(n::text) || md5((n + 1)::text), "
                "now() - interval '3 hours', now() - interval '2 hours' "
                "FROM generate_series(1, 20000) AS n"
            )
        )
    documented = []
    for _ in range(50):
        # Younger than the empty ones, so only the documents-first rule puts them first.
        sandbox_id, _ = make_sandbox(engine, minutes_left=-1, age_minutes=51)
        held_in(engine, sandbox_id)
        documented.append(sandbox_id)
    age_sweep(engine, 20)
    before = sweep_age(engine)
    monkeypatch.setattr(sweep, "BATCH", 100)
    ticks = itertools.count()
    report = await sweep_once(beta, reachable(temporal), budget=5, clock=lambda: next(ticks) * 0.05)
    assert report["postgres"] == "cut short" and report["complete"] is False
    assert report["temporal"] == report["storage"] == "complete"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Document)) == 0
        assert session.scalar(select(func.count()).where(Sandbox.id.in_(documented))) == 0
        left = session.scalar(select(func.count()).select_from(Sandbox))
    assert 0 < left < 20000
    assert sweep_age(engine) >= before
    report = await sweep_once(beta, reachable(temporal))
    assert report["complete"] is True and report["postgres_deleted"] == left
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Sandbox)) == 0
    assert sweep_age(engine) < 60


# S6b


async def test_with_temporal_unreachable_rows_and_objects_go_and_histories_go_next_run(
    beta, temporal
):
    engine = beta.engine
    gone, _ = make_sandbox(engine)
    live, _ = make_sandbox(engine)
    sent = await send(beta, temporal, gone)
    kept = await send(beta, temporal, live, count=1)
    expire(engine, gone)
    seeded = set_caps(engine)
    report = await sweep_once(beta, temporal_connector("127.0.0.1:1"))
    assert report["temporal"] == "failed" and report["complete"] is False
    assert report["postgres"] == report["storage"] == "complete"
    assert rows(engine, gone) == (0, 0)
    assert objects(beta, f"sbx/{gone}/") == []
    assert await histories(temporal, sent) == sent
    assert sweep_age(engine) is None
    assert caps(engine, seeded) == CAPS
    report = await sweep_once(beta, reachable(temporal))
    assert report["complete"] is True and report["temporal_deleted"] == 2
    assert await histories(temporal, sent) == []
    # The live sandbox lost nothing.
    assert rows(engine, live) == (1, 1)
    assert len(objects(beta, f"sbx/{live}/")) == 1
    assert await histories(temporal, kept) == kept
    assert caps(engine, seeded) == CAPS


# S6c


class Killed(BaseException):
    pass


async def test_a_sweep_killed_between_passes_leaves_nothing_once_the_next_run_ends(
    beta, temporal, monkeypatch
):
    engine = beta.engine
    sandbox_id, _ = make_sandbox(engine)
    sent = await send(beta, temporal, sandbox_id)
    expire(engine, sandbox_id)

    async def killed(*args, **kwargs):
        raise Killed

    with monkeypatch.context() as patched:
        patched.setattr(sweep, "temporal_pass", killed)
        with pytest.raises(Killed):
            await sweep_once(beta, reachable(temporal))
    assert rows(engine, sandbox_id) == (0, 0)
    assert len(objects(beta, f"sbx/{sandbox_id}/")) == 2
    assert await histories(temporal, sent) == sent
    report = await sweep_once(beta, reachable(temporal))
    assert report["complete"] is True
    assert objects(beta) == []
    assert await histories(temporal, sent) == []


# S6d


@asynccontextmanager
async def api(runtime, temporal):
    app = create_app(runtime, temporal)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as http:
            yield http


async def test_a_stale_sweep_stops_new_sandboxes_samples_and_reviews_but_not_delete_now(
    beta, temporal
):
    engine = beta.engine
    sandbox_id, token = make_sandbox(engine)
    [document_id] = await send(beta, temporal, sandbox_id, count=1)
    visitor = {"Origin": BASE, "X-Forwarded-For": "192.0.2.40"}
    sample = {"case_type": "clean"}
    async with api(beta, temporal) as http:
        # No sweep has ever completed.
        assert (await http.get("/healthz")).json() == {"status": "stale"}
        refused = await http.post("/beta/samples", json=sample, headers=visitor)
        assert refused.status_code == 503 and refused.json() == {"detail": CATCHING_UP}
        assert "set-cookie" not in refused.headers
        options = await http.get("/beta/samples", headers=visitor)
        assert options.json()["state"] == "catching_up"
        owned = visitor | {"Cookie": sandbox_cookie(token)}
        assert (await http.post("/beta/samples", json=sample, headers=owned)).status_code == 503
        # A dismiss reason is new text too. Ownership is still checked first, so an id that
        # is not the visitor's gets its 404, not the 503.
        dismiss = {"reason": "Not ours."}
        # Bounded: with no worker, a dismiss that got through would wait on Temporal for good.
        review = await asyncio.wait_for(
            http.post(f"/review/{document_id}/dismiss", json=dismiss, headers=owned), 15
        )
        assert review.status_code == 503 and review.json() == {"detail": CATCHING_UP}
        unknown = await http.post(f"/review/{uuid.uuid4()}/dismiss", json=dismiss, headers=owned)
        assert unknown.status_code == 404
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(Sandbox)) == 1
        age_sweep(engine, 11)
        assert (await http.get("/healthz")).json() == {"status": "stale"}
        # Delete now still deletes, but nothing erases the last copies while the sweep is
        # behind, so its answer promises no time.
        deleted = await http.delete("/sandbox", headers=owned)
        assert deleted.status_code == 202
        assert deleted.json() == {"deleted": False, "detail": ERASE_DELAYED}
        assert deleted.headers["set-cookie"] == spent_cookie()
        assert rows(engine, sandbox_id) == (0, 0)
        assert objects(beta, f"sbx/{sandbox_id}/") == []
        age_sweep(engine, 9)
        assert (await http.get("/healthz")).json() == {"status": "ok"}
        age_sweep(engine, 11)
        report = await sweep_once(beta, reachable(temporal))
        assert report["complete"] is True
        assert (await http.get("/healthz")).json() == {"status": "ok"}
        accepted = await http.post("/beta/samples", json=sample, headers=visitor)
        assert accepted.status_code == 200, accepted.text
        assert accepted.headers["set-cookie"].startswith("__Host-docket_sandbox=")


# The passes' rules


async def test_documents_go_before_closed_empty_sandboxes_however_old(beta):
    engine = beta.engine
    closed = [make_sandbox(engine, closed=True, age_minutes=120)[0] for _ in range(10)]
    documented = []
    for _ in range(10):
        sandbox_id, _ = make_sandbox(engine, minutes_left=-1, age_minutes=51)
        held_in(engine, sandbox_id)
        documented.append(sandbox_id)
    ticks = itertools.count()
    # Room for twelve sandboxes before the budget runs out.
    finished, deleted = postgres_pass(engine, Deadline(0.6, lambda: next(ticks) * 0.05))
    assert (finished, deleted) == (False, 11)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).where(Sandbox.id.in_(documented))) == 0
        assert session.scalar(select(func.count()).where(Sandbox.id.in_(closed))) == 9


async def test_a_storage_pass_cut_short_still_vacuums_what_it_deleted(beta, monkeypatch):
    engine = beta.engine
    for _ in range(3):
        sandbox_id, _ = make_sandbox(engine, minutes_left=-1)
        beta.s3.put_object(Bucket=beta.settings.s3_bucket, Key=f"sbx/{sandbox_id}/raw/x", Body=b"x")
    vacuums = []
    monkeypatch.setattr(sweep, "vacuum", vacuums.append)
    ticks = itertools.count()
    finished, deleted = sweep.storage_pass(beta, Deadline(0.1, lambda: next(ticks) * 0.05))
    assert (finished, deleted) == (False, 1)
    assert vacuums == [beta.settings.seaweedfs_master_url]
    assert len(objects(beta)) == 2


async def test_a_closed_sandbox_still_being_written_waits_for_the_write(beta, monkeypatch):
    engine = beta.engine
    busy, _ = make_sandbox(engine, closed=True)
    idle, _ = make_sandbox(engine, closed=True)
    monkeypatch.setattr(sweep, "LOCK_TIMEOUT_MS", 200)
    with engine.connect() as writer:
        writer.execute(select(func.pg_advisory_lock_shared(lock_key(busy))))
        writer.commit()
        finished, deleted = postgres_pass(engine, Deadline(50))
        assert (finished, deleted) == (False, 1)
        assert rows(engine, busy) == (1, 0) and rows(engine, idle) == (0, 0)
        writer.execute(select(func.pg_advisory_unlock_shared(lock_key(busy))))
        writer.commit()
    assert postgres_pass(engine, Deadline(50)) == (True, 1)
    assert rows(engine, busy) == (0, 0)


async def test_live_sandboxes_stay_and_the_counters_lose_only_rows_past_the_hour(beta):
    engine = beta.engine
    live, _ = make_sandbox(engine, minutes_left=1)
    document = held_in(engine, live)
    with Session(engine) as session, session.begin():
        for minutes in (30, 59, 61, 120):
            at = func.now() - func.make_interval(0, 0, 0, 0, 0, minutes)
            session.execute(insert(SampleSend).values(address_hash="a" * 64, at=at))
            session.execute(insert(SandboxCreation).values(address_hash="a" * 64, at=at))
    assert postgres_pass(engine, Deadline(50)) == (True, 0)
    with Session(engine) as session:
        assert session.get(Document, document) is not None
        for table in (SampleSend, SandboxCreation):
            assert session.scalar(select(func.count()).select_from(table)) == 2


async def test_everything_deletes_live_sandboxes_loose_documents_and_every_object(beta, temporal):
    engine = beta.engine
    live, _ = make_sandbox(engine)
    sent = await send(beta, temporal, live)
    loose = await Ingress(beta, temporal).submit(pdf(["Before sandboxes"]), "old.pdf")
    assert objects(beta, "raw/")
    seeded = set_caps(engine)
    report = await sweep_once(beta, reachable(temporal), everything=True)
    assert report["complete"] is True
    assert caps(engine, seeded) == CAPS
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Sandbox)) == 0
        assert session.scalar(select(func.count()).select_from(Document)) == 0
    assert objects(beta, "") == []
    assert await histories(temporal, [*sent, loose]) == []


# The service


async def test_the_service_sweeps_every_interval_and_exits_when_a_run_hangs(
    beta, monkeypatch, tmp_path
):
    monkeypatch.setattr(sweep, "READY_FILE", tmp_path / "ready")
    runs, waits, left = [], [], []

    async def run(runtime, connect):
        runs.append(1)
        if len(runs) == 3:
            await asyncio.sleep(5)
        return {"complete": True}

    async def wait(seconds):
        waits.append(round(seconds))

    monkeypatch.setattr(sweep, "sweep_once", run)
    times = iter([0, 30, 200, 400, 500])
    await sweep.run_service(
        beta, reachable(None), limit=0.2, sleep=wait, clock=lambda: next(times), leave=left.append
    )
    assert (tmp_path / "ready").is_file()
    # Start to start every 120 seconds, and at once after a run that took longer.
    assert waits == [90, 0]
    assert left == [1] and len(runs) == 3


def test_the_sweep_and_status_commands_refuse_outside_beta(postgres_engine, monkeypatch):
    from tests.test_beta_samples import schema_url

    monkeypatch.setenv("DATABASE_URL", schema_url(postgres_engine))
    runner = CliRunner()
    for command in (["beta-sweep"], ["beta-status"]):
        result = runner.invoke(cli, command)
        assert result.exit_code == 1 and "only with DOCKET_ENV=beta" in result.output
    monkeypatch.setenv("DOCKET_ENV", "beta")
    monkeypatch.setenv("DOCKET_ADDRESS_KEY", KEY)
    monkeypatch.setenv("DOCKET_TRUST_PROXY", "1")
    monkeypatch.setenv("DOCKET_DAILY_BUDGET_USD", "1.00")
    monkeypatch.delenv("DOCKET_REVIEW_TOKEN", raising=False)
    both = runner.invoke(cli, ["beta-sweep", "--all", "--service"])
    assert both.exit_code == 2
    monkeypatch.setenv("DOCKET_ADDRESS_KEY", "short")
    unsafe = runner.invoke(cli, ["beta-sweep"])
    assert unsafe.exit_code == 1
    assert unsafe.output.startswith("Docket beta refuses to start:\n- ")
    assert "DOCKET_ADDRESS_KEY needs at least 32 bytes." in unsafe.output
    monkeypatch.setenv("DOCKET_ADDRESS_KEY", KEY)
    held_in(postgres_engine, make_sandbox(postgres_engine)[0])
    make_sandbox(postgres_engine, minutes_left=-5)
    status = runner.invoke(cli, ["beta-status"])
    assert status.exit_code == 0, status.output
    lines = status.output.splitlines()
    assert lines[0] == "Last complete sweep: never (stale: samples are refused)"
    assert lines[1].startswith("Sandboxes: 1 live, 1 waiting for the sweep; the oldest expired 0:0")
    assert lines[2] == "Documents: 1 in sandboxes, 0 outside any sandbox"
    assert lines[3] == "New sandboxes in the last hour: 0; addresses at the limit of 10: 0"
    assert lines[5] == "Live answers: on; spent today: $0.0000 of $1.00"
