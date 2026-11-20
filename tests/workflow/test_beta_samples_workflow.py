"""Beta samples through the real workflow (plan PR B, B1, B4 and B5).

Storage is an in-memory stand-in, so these run with Postgres and Temporal's test server only.
"""

import asyncio
import hashlib
import os
import uuid

import httpx
import httpx2
import pytest
from pydantic import SecretStr
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from docket.activities import Activities
from docket.api import create_app
from docket.beta_samples import SAMPLE_SENDER, set_live_answers
from docket.core.storage import StorageConflict
from docket.corpus import SAMPLE_CASE_TYPES
from docket.db import Document, DocumentSource, Event, FiledDocument
from docket.ingress import Ingress
from docket.llm.anthropic import AnthropicTransport
from docket.llm.errors import BudgetExceeded
from docket.runtime import Runtime
from docket.workflows import FileDocumentWorkflow
from tests.test_anthropic_transport import FakeApi
from tests.test_beta_gate import BASE, beta_settings, cookie

SETTLED = {"filed", "needs_review"}


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("DOCKET_FAILPOINT", "DOCKET_DAILY_BUDGET_USD")):
            monkeypatch.delenv(name)


class MemoryStorage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_if_absent(self, key, content, sha256, *, original_filename=None):
        assert hashlib.sha256(content).hexdigest() == sha256
        if key in self.objects:
            if self.objects[key] != content:
                raise StorageConflict(key)
            return False
        self.objects[key] = content
        return True


class FirstCandidateModel:
    """Answers like a model that always picks the first candidate."""

    def __init__(self, error: Exception | None = None):
        self.calls: list[str] = []
        self.error = error

    async def call(self, *, activity, schema, inputs, **kwargs):
        self.calls.append(activity)
        if self.error is not None:
            raise self.error
        if activity == "classify":
            return schema.model_validate({"category": "lease"})
        return schema.model_validate({"record_id": inputs["ids"][0]})


@pytest.fixture()
def beta_runtime(postgres_engine, monkeypatch):
    runtime = Runtime(beta_settings(s3_bucket="unused"))
    runtime.engine.dispose()
    runtime.engine = postgres_engine
    storage = MemoryStorage()
    monkeypatch.setattr(runtime, "storage", storage)
    monkeypatch.setattr(runtime, "read", lambda key: storage.objects[key])
    monkeypatch.setattr(runtime, "ensure_bucket", lambda: None)
    try:
        yield runtime
    finally:
        runtime.s3.close()


def visitor(index: int) -> dict:
    return cookie() | {"Origin": BASE, "X-Forwarded-For": f"203.0.113.{index}"}


async def settled(engine, document_id: str, statuses=SETTLED) -> str:
    for _ in range(1500):
        with Session(engine) as session:
            status = session.get(Document, uuid.UUID(document_id)).status
        if status in statuses:
            return status
        await asyncio.sleep(0.02)
    raise AssertionError(f"Document {document_id} stayed {status}")


def steps(engine, document_id: str) -> list[str]:
    with Session(engine) as session:
        return list(
            session.scalars(
                select(Event.step)
                .where(Event.document_id == uuid.UUID(document_id))
                .order_by(Event.id)
            )
        )


def forget(engine, document_id: str) -> None:
    """Remove a document's rows, so the same bytes can come in again as a new document."""
    key = uuid.UUID(document_id)
    with Session(engine) as session, session.begin():
        for table in (Event, DocumentSource, FiledDocument):
            session.execute(delete(table).where(table.document_id == key))
        session.execute(delete(Document).where(Document.id == key))


async def beta_stack(runtime, env):
    app = create_app(runtime, env.client)
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=BASE)
    return app, http


# B1


async def test_every_sample_type_runs_the_normal_workflow_and_ends_filed_or_held(
    beta_runtime, monkeypatch
):
    model = FirstCandidateModel()
    monkeypatch.setattr(beta_runtime, "gateway", lambda request: model)
    engine = beta_runtime.engine
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=beta_runtime.settings.docket_task_queue,
            workflows=[FileDocumentWorkflow],
            activities=Activities(beta_runtime).registered(),
        ):
            app, http = await beta_stack(beta_runtime, env)
            async with app.router.lifespan_context(app), http:
                outcomes = {}
                for index, case_type in enumerate(SAMPLE_CASE_TYPES):
                    hashes = []
                    for attempt in range(2):
                        response = await http.post(
                            "/beta/samples",
                            json={"case_type": case_type},
                            headers=visitor(2 * index + attempt),
                        )
                        assert response.status_code == 200, response.text
                        document_id = response.json()["document_id"]
                        status = await settled(engine, document_id)
                        with Session(engine) as session:
                            document = session.get(Document, uuid.UUID(document_id))
                            content_hash = document.content_hash
                            source = session.scalar(
                                select(DocumentSource.source).where(
                                    DocumentSource.document_id == document.id
                                )
                            )
                            received = session.scalar(
                                select(Event.detail).where(
                                    Event.document_id == document.id, Event.step == "received"
                                )
                            )
                        filename = received["workflow_input"]["filename"]
                        assert filename.startswith("sample-")
                        assert source == "sample"
                        hashes.append(content_hash)
                        sample_steps = steps(engine, document_id)
                        assert sample_steps[0] == "received" and sample_steps[-1] == status
                        # The same bytes sent the way an upload is sent take the same steps.
                        content = beta_runtime.read(f"raw/{content_hash}")
                        forget(engine, document_id)
                        uploaded = await Ingress(beta_runtime, env.client).submit(content, filename)
                        assert await settled(engine, uploaded) == status
                        assert steps(engine, uploaded) == sample_steps
                        outcomes[(case_type, attempt)] = status
                    assert hashes[0] != hashes[1], case_type
    assert set(outcomes.values()) == SETTLED
    assert outcomes[("clean", 0)] == "filed"
    assert outcomes[("no_record", 0)] == "needs_review"


# B4


async def test_a_budget_refusal_mid_way_ends_failed_with_the_budget_reason(
    beta_runtime, monkeypatch
):
    model = FirstCandidateModel(BudgetExceeded("Global daily budget exceeded before model call"))
    monkeypatch.setattr(beta_runtime, "gateway", lambda request: model)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=beta_runtime.settings.docket_task_queue,
            workflows=[FileDocumentWorkflow],
            activities=Activities(beta_runtime).registered(),
        ):
            app, http = await beta_stack(beta_runtime, env)
            async with app.router.lifespan_context(app), http:
                response = await http.post(
                    "/beta/samples", json={"case_type": "clean"}, headers=visitor(1)
                )
                document_id = response.json()["document_id"]
                result = await env.client.get_workflow_handle("doc-" + document_id).result()
                assert (result["status"], result["reason"]) == ("failed", "budget_exceeded")
                trace = (await http.get("/documents/" + document_id, headers=visitor(1))).json()
                assert (trace["status"], trace["reason"]) == ("failed", "budget_exceeded")
                # A failed sample is not in the queue, so the pane takes these from here.
                assert (trace["filename"], trace["sender"]) == ("sample-clean.pdf", SAMPLE_SENDER)
    assert model.calls == ["classify"]


# B5


async def test_switching_off_mid_workflow_refuses_the_next_live_call_at_the_transport(
    postgres_engine, monkeypatch, beta_runtime
):
    beta_runtime.settings = beta_settings(
        s3_bucket="unused",
        anthropic_api_key=SecretStr("test-key"),
        docket_model_mode="live",
    )
    api = FakeApi()

    def transport(settings, max_output_tokens):
        client = httpx2.AsyncClient(transport=httpx2.MockTransport(api))
        return AnthropicTransport(settings, max_output_tokens, client)

    monkeypatch.setattr("docket.runtime.AnthropicTransport", transport)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        app, http = await beta_stack(beta_runtime, env)
        async with app.router.lifespan_context(app), http:
            response = await http.post(
                "/beta/samples", json={"case_type": "clean"}, headers=visitor(1)
            )
            assert response.status_code == 200
            document_id = response.json()["document_id"]
            # The workflow has started but no worker has run it yet, so the switch flips
            # between the sample being accepted and its first live call.
            set_live_answers(postgres_engine, False)
            async with Worker(
                env.client,
                task_queue=beta_runtime.settings.docket_task_queue,
                workflows=[FileDocumentWorkflow],
                activities=Activities(beta_runtime).registered(),
            ):
                result = await env.client.get_workflow_handle("doc-" + document_id).result()
    assert (result["status"], result["reason"]) == ("failed", "budget_exceeded")
    assert api.requests == []


async def test_a_held_document_stays_reviewable_with_the_switch_off(beta_runtime, monkeypatch):
    model = FirstCandidateModel()
    monkeypatch.setattr(beta_runtime, "gateway", lambda request: model)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=beta_runtime.settings.docket_task_queue,
            workflows=[FileDocumentWorkflow],
            activities=Activities(beta_runtime).registered(),
        ):
            app, http = await beta_stack(beta_runtime, env)
            async with app.router.lifespan_context(app), http:
                response = await http.post(
                    "/beta/samples", json={"case_type": "address_only"}, headers=visitor(1)
                )
                document_id = response.json()["document_id"]
                assert await settled(beta_runtime.engine, document_id) == "needs_review"
                set_live_answers(beta_runtime.engine, False)
                [item] = (await http.get("/review", headers=visitor(1))).json()["items"]
                record_id = item["candidates"][0]["record_id"]
                approved = await http.post(
                    f"/review/{document_id}/approve",
                    json={"record_id": record_id},
                    headers=visitor(1),
                )
                assert approved.status_code == 200, approved.text
                result = await env.client.get_workflow_handle("doc-" + document_id).result()
                assert (result["status"], result["record_id"]) == ("filed", record_id)
