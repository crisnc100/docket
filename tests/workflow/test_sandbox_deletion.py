"""Delete now against real stores, with Temporal's dev server on localhost:7233: what is left
right after it answers, and samples racing it (plan PR 1, S4 and S5)."""

import asyncio
import threading

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from docket import sandbox as sandbox_module
from docket import sweep
from docket.corpus import pdf, sample_document
from docket.db import Document, Event, Sandbox
from docket.ingress import Ingress
from docket.sandbox import SANDBOX_ENDED, lock_key, writing_to
from docket.sweep import DELETED, DELETING, sweep_once
from tests.sandboxes import make_sandbox, sandbox_cookie
from tests.test_beta_gate import BASE
from tests.workflow.test_sweep import CAPS, api, caps, histories, objects, reachable, send, set_caps
from tests.workflow.test_sweep import beta as beta
from tests.workflow.test_sweep import no_settle as no_settle
from tests.workflow.test_sweep import temporal as temporal

SPENT = "__Host-docket_sandbox=; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=0"


def visitor(token: str | None = None) -> dict:
    return {"Origin": BASE, "X-Forwarded-For": "192.0.2.50"} | (
        {"Cookie": sandbox_cookie(token)} if token else {}
    )


def leftovers(engine, sandbox_id) -> int:
    """Every row anywhere that still names the sandbox or one of its documents."""
    with Session(engine) as session:
        return (
            (session.scalar(select(func.count()).where(Sandbox.id == sandbox_id)) or 0)
            + (session.scalar(select(func.count()).where(Document.sandbox_id == sandbox_id)) or 0)
            + (
                session.scalar(
                    select(func.count())
                    .select_from(Event)
                    .join(Document, Event.document_id == Document.id)
                    .where(Document.sandbox_id == sandbox_id)
                )
                or 0
            )
        )


async def test_delete_now_leaves_no_rows_objects_or_histories_and_clears_the_cookie(beta, temporal):
    engine = beta.engine
    sweep.record_complete(engine)
    sandbox_id, token = make_sandbox(engine)
    sent = await send(beta, temporal, sandbox_id, count=3)
    assert await histories(temporal, sent) == sent
    seeded = set_caps(engine)
    async with api(beta, temporal) as http:
        response = await http.delete("/sandbox", headers=visitor(token))
        assert response.status_code == 200
        assert response.json() == {"deleted": True, "detail": DELETED}
        assert caps(engine, seeded) == CAPS
        assert response.headers["set-cookie"] == SPENT
        assert leftovers(engine, sandbox_id) == 0
        assert objects(beta, f"sbx/{sandbox_id}/") == []
        assert await histories(temporal, sent) == []
        assert (await http.get("/sandbox", headers=visitor(token))).json() == {"expires_in": None}
        # No cookie, an unknown one, or the same one again: the same answer, which reveals
        # nothing about whether a sandbox was there.
        for headers in (visitor(), visitor("unknown"), visitor(token)):
            again = await http.delete("/sandbox", headers=headers)
            assert (again.status_code, again.content) == (200, response.content)
            assert again.headers["set-cookie"] == SPENT


# S4


async def test_deleting_one_of_two_sandboxes_with_the_same_bytes_leaves_the_others_preview(
    beta, temporal
):
    engine = beta.engine
    sweep.record_complete(engine)
    first, first_token = make_sandbox(engine)
    second, second_token = make_sandbox(engine)
    sample = sample_document("clean", 7)
    ingress = Ingress(beta, temporal)
    ids = [
        await ingress.submit(sample.content, sample.filename, source="sample", sandbox=sandbox)
        for sandbox in (first, second)
    ]
    async with api(beta, temporal) as http:
        assert (await http.delete("/sandbox", headers=visitor(first_token))).status_code == 200
        preview = await http.get(f"/documents/{ids[1]}/file", headers=visitor(second_token))
        assert preview.status_code == 200 and preview.content == sample.content
        gone = await http.get(f"/documents/{ids[0]}/file", headers=visitor(first_token))
        assert gone.status_code == 404
    assert len(objects(beta, f"sbx/{second}/")) == 1
    assert await histories(temporal, ids) == [ids[1]]


# S5


def held(target, reached: threading.Event, release: threading.Event):
    """target, stopped where it is called until the test releases it."""

    def wrapped(*args, **kwargs):
        reached.set()
        assert release.wait(30), "the test never released the write"
        return target(*args, **kwargs)

    return wrapped


async def lock_awaited(engine, sandbox_id) -> bool:
    """Whether some session is waiting for the sandbox's lock, within five seconds."""
    key = lock_key(sandbox_id) & 0xFFFFFFFFFFFFFFFF
    for _ in range(100):
        with engine.connect() as connection:
            waiting = connection.execute(
                text(
                    "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted"
                    " AND classid::bigint = :high AND objid::bigint = :low AND objsubid = 1"
                ),
                {"high": key >> 32, "low": key & 0xFFFFFFFF},
            ).scalar_one()
        if waiting:
            return True
        await asyncio.sleep(0.05)
    return False


async def test_a_sample_already_writing_is_deleted_with_the_sandbox(beta, temporal, monkeypatch):
    engine = beta.engine
    sweep.record_complete(engine)
    sandbox_id, token = make_sandbox(engine)
    reached, release = threading.Event(), threading.Event()
    # Held inside the write, with the sandbox's lock taken, until Delete now waits for it.
    monkeypatch.setattr(
        beta.storage, "put_if_absent", held(beta.storage.put_if_absent, reached, release)
    )
    try:
        async with api(beta, temporal) as http:
            sample = asyncio.create_task(
                http.post("/beta/samples", json={"case_type": "clean"}, headers=visitor(token))
            )
            assert await asyncio.to_thread(reached.wait, 10)
            delete = asyncio.create_task(http.delete("/sandbox", headers=visitor(token)))
            assert await lock_awaited(engine, sandbox_id)
            release.set()
            written = await sample
            deleted = await delete
    finally:
        release.set()
    assert written.status_code == 200, written.text
    document_id = written.json()["document_id"]
    assert deleted.status_code == 200 and deleted.json() == {"deleted": True, "detail": DELETED}
    assert leftovers(engine, sandbox_id) == 0
    assert objects(beta, f"sbx/{sandbox_id}/") == []
    assert await histories(temporal, [document_id]) == []


async def test_a_sample_that_reaches_the_lock_after_delete_now_writes_nothing(
    beta, temporal, monkeypatch
):
    engine = beta.engine
    sweep.record_complete(engine)
    sandbox_id, token = make_sandbox(engine)
    reached, release = threading.Event(), threading.Event()
    # Held after the sandbox was chosen and before its lock, until Delete now has finished.
    monkeypatch.setattr(
        sandbox_module, "_take_shared", held(sandbox_module._take_shared, reached, release)
    )
    try:
        async with api(beta, temporal) as http:
            sample = asyncio.create_task(
                http.post("/beta/samples", json={"case_type": "clean"}, headers=visitor(token))
            )
            assert await asyncio.to_thread(reached.wait, 10)
            deleted = await http.delete("/sandbox", headers=visitor(token), timeout=60)
            assert not sample.done()
            release.set()
            refused = await sample
    finally:
        release.set()
    assert deleted.status_code == 200
    assert refused.status_code == 409
    assert refused.json() == {"detail": SANDBOX_ENDED, "code": "sandbox_ended"}
    assert refused.headers["set-cookie"] == SPENT
    assert leftovers(engine, sandbox_id) == 0
    assert objects(beta, f"sbx/{sandbox_id}/") == []
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Document)) == 0
    listed = [
        item
        async for item in temporal.list_workflows(
            f"TaskQueue = '{beta.settings.docket_task_queue}'"
        )
    ]
    assert listed == []


# Recovery


async def test_reconcile_starts_a_sandbox_workflow_only_while_its_sandbox_is_open(
    beta, temporal, monkeypatch
):
    engine = beta.engine
    writer = Ingress(beta, temporal)

    async def never(request):
        pass

    # Written as a sample is, but its workflow never started.
    monkeypatch.setattr(writer, "start", never)
    gone, _ = make_sandbox(engine)
    open_, _ = make_sandbox(engine)
    stuck = await writer.submit(pdf(["Stuck while Delete now ran"]), "a.pdf", sandbox=gone)
    waiting = await writer.submit(pdf(["Stuck in an open sandbox"]), "b.pdf", sandbox=open_)
    assert await histories(temporal, [stuck, waiting]) == []
    recovery = Ingress(beta, temporal)
    # Delete now holds the lock: recovery waits for it, then finds the sandbox gone.
    with engine.connect() as holder:
        holder.execute(select(func.pg_advisory_lock(lock_key(gone))))
        holder.commit()
        task = asyncio.create_task(recovery.reconcile(older_than_minutes=-1))
        try:
            assert await lock_awaited(engine, gone)
            holder.execute(delete(Sandbox).where(Sandbox.id == gone))
            holder.commit()
        finally:
            holder.execute(select(func.pg_advisory_unlock(lock_key(gone))))
            holder.commit()
    counts = await task
    assert counts["started"] == 1
    assert await histories(temporal, [stuck, waiting]) == [waiting]


# When Delete now cannot finish


async def test_a_store_that_fails_leaves_the_rest_to_the_sweep(beta, temporal):
    engine = beta.engine
    sweep.record_complete(engine)
    sandbox_id, token = make_sandbox(engine)
    sent = await send(beta, temporal, sandbox_id, count=1)
    beta.settings = beta.settings.model_copy(update={"seaweedfs_master_url": "http://127.0.0.1:1"})
    async with api(beta, temporal) as http:
        response = await http.delete("/sandbox", headers=visitor(token))
    assert response.status_code == 202
    assert response.json() == {"deleted": False, "detail": DELETING}
    assert response.headers["set-cookie"] == SPENT
    # Everything but the vacuum was done.
    assert leftovers(engine, sandbox_id) == 0
    assert objects(beta, f"sbx/{sandbox_id}/") == []
    assert await histories(temporal, sent) == []


async def test_a_write_holding_the_lock_too_long_closes_the_sandbox_for_the_sweep(
    beta, temporal, monkeypatch
):
    engine = beta.engine
    sweep.record_complete(engine)
    sandbox_id, token = make_sandbox(engine)
    sent = await send(beta, temporal, sandbox_id, count=1)
    monkeypatch.setattr(sweep, "LOCK_TIMEOUT_MS", 200)
    async with api(beta, temporal) as http:
        async with writing_to(engine, sandbox_id):
            response = await http.delete("/sandbox", headers=visitor(token))
            assert response.status_code == 202
            assert response.json() == {"deleted": False, "detail": DELETING}
            with Session(engine) as session:
                assert session.get(Sandbox, sandbox_id).closed_at is not None
        # Closed, so no later write goes to it.
        with pytest.raises(sandbox_module.SandboxClosed):
            async with writing_to(engine, sandbox_id):
                pass
    report = await sweep_once(beta, reachable(temporal))
    assert report["complete"] is True
    assert leftovers(engine, sandbox_id) == 0
    assert objects(beta, f"sbx/{sandbox_id}/") == []
    assert await histories(temporal, sent) == []


async def test_a_stale_sweep_and_a_held_lock_together_answer_202_and_close_the_sandbox(
    beta, temporal, monkeypatch
):
    engine = beta.engine
    sandbox_id, token = make_sandbox(engine)
    await send(beta, temporal, sandbox_id, count=1)
    monkeypatch.setattr(sweep, "LOCK_TIMEOUT_MS", 200)
    assert sweep.sweep_stale(engine)
    async with api(beta, temporal) as http:
        async with writing_to(engine, sandbox_id):
            response = await http.delete("/sandbox", headers=visitor(token))
    assert response.status_code == 202
    assert response.json()["deleted"] is False
    assert response.headers["set-cookie"] == SPENT
    with Session(engine) as session:
        assert session.get(Sandbox, sandbox_id).closed_at is not None
    # Closed, not deleted: the write held the lock, so its rows wait for the sweep.
    assert leftovers(engine, sandbox_id) > 0
