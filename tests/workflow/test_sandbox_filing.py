"""A sandbox document that files, by the rules or by a reviewer, writes no second object:
only the key it would have used is recorded (plan PR 1, S8)."""

import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from docket.activities import Activities
from docket.corpus import sample_document
from docket.db import Document, Event, FiledDocument
from docket.ingress import Ingress
from docket.workflows import FileDocumentWorkflow
from tests.sandboxes import make_sandbox


class FirstCandidateModel:
    async def call(self, *, activity, schema, inputs, **kwargs):
        if activity == "classify":
            return schema.model_validate({"category": "lease"})
        return schema.model_validate({"record_id": inputs["ids"][0]})


async def settled(engine, document_id: str) -> str:
    for _ in range(1500):
        with Session(engine) as session:
            status = session.get(Document, uuid.UUID(document_id)).status
        if status in {"filed", "needs_review"}:
            return status
        await asyncio.sleep(0.02)
    raise AssertionError(f"Document {document_id} stayed {status}")


def keys(runtime) -> list[str]:
    listed = runtime.s3.list_objects_v2(Bucket=runtime.settings.s3_bucket)
    return sorted(item["Key"] for item in listed.get("Contents", []))


async def test_filing_a_sandbox_document_records_its_key_and_writes_no_second_object(
    runtime, monkeypatch
):
    monkeypatch.setattr(runtime, "gateway", lambda request: FirstCandidateModel())
    sandbox_id, _ = make_sandbox(runtime.engine)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=runtime.settings.docket_task_queue,
            workflows=[FileDocumentWorkflow],
            activities=Activities(runtime).registered(),
        ):
            ingress = Ingress(runtime, env.client)
            clean = sample_document("clean", 11)
            held = sample_document("address_only", 11)
            filed_id = await ingress.submit(
                clean.content, clean.filename, source="sample", sandbox=sandbox_id
            )
            held_id = await ingress.submit(
                held.content, held.filename, source="sample", sandbox=sandbox_id
            )
            assert await settled(runtime.engine, filed_id) == "filed"
            assert await settled(runtime.engine, held_id) == "needs_review"
            with Session(runtime.engine) as session:
                score = session.scalar(
                    select(Event.detail).where(
                        Event.document_id == uuid.UUID(held_id), Event.step == "score"
                    )
                )
            handle = env.client.get_workflow_handle(f"doc-{held_id}")
            await handle.execute_update(
                "review_action",
                {"action": "approve", "value": score["candidates"][0]["record_id"]},
            )
            result = await handle.result()
            assert result["status"] == "filed"
    # Only the two raw files are stored, both under the sandbox.
    assert all(key.startswith(f"sbx/{sandbox_id}/raw/") for key in keys(runtime))
    assert len(keys(runtime)) == 2
    with Session(runtime.engine) as session:
        filings = session.scalars(select(FiledDocument).order_by(FiledDocument.id)).all()
        file_events = session.scalars(select(Event.detail).where(Event.step == "file")).all()
    assert len(filings) == 2
    for filing in filings:
        assert filing.sandbox_id == sandbox_id
        assert filing.storage_key.startswith(f"sbx/{sandbox_id}/")
        assert filing.storage_key not in keys(runtime)
    assert sorted(event["key"] for event in file_events) == sorted(
        filing.storage_key for filing in filings
    )
