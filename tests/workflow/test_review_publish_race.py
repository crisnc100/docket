import asyncio
import uuid

import pytest
from sqlalchemy.orm import Session
from temporalio import activity
from temporalio.client import WorkflowUpdateFailedError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from docket.activities import Activities, heartbeats
from docket.corpus import pdf
from docket.db import Document, FiledDocument
from docket.ingress import Ingress
from docket.workflows import FileDocumentWorkflow
from tests.workflow.test_integration import wait_database
from tests.workflow.test_optional_suggestion import UnavailableSuggestion


class PausedReviewFinalize(Activities):
    def __init__(self, runtime):
        super().__init__(runtime)
        self.published = asyncio.Event()
        self.release = asyncio.Event()
        self.returned = False

    @activity.defn(name="finalize")
    async def finalize(self, stage: dict) -> dict:
        stage = await super().finalize(stage)
        if stage["terminal"] == "needs_review":
            self.published.set()
            async with heartbeats():
                await self.release.wait()
            self.returned = True
        return stage


@pytest.mark.parametrize(
    "action,value,status,reason",
    [
        ("approve", "UNIT-0002", "filed", "review_approved"),
        ("dismiss", "review complete", "dismissed", "review complete"),
    ],
)
async def test_action_after_database_publish_precedes_finalize_return(
    runtime, monkeypatch, action, value, status, reason
):
    monkeypatch.setattr(runtime, "gateway", lambda request: UnavailableSuggestion("ParseError"))
    activities = PausedReviewFinalize(runtime)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=runtime.settings.docket_task_queue,
            workflows=[FileDocumentWorkflow],
            activities=activities.registered(),
        ):
            document_id = await Ingress(runtime, env.client).submit(
                pdf(["UNIT-0001"]), "review-publish.pdf"
            )
            handle = env.client.get_workflow_handle("doc-" + document_id)
            try:
                await asyncio.wait_for(activities.published.wait(), 10)
                await wait_database(runtime, document_id, "needs_review")
                assert not activities.returned
                assert await asyncio.wait_for(
                    handle.execute_update("review_action", {"action": action, "value": value}),
                    10,
                )
                assert not activities.returned
                with pytest.raises(WorkflowUpdateFailedError):
                    await handle.execute_update("review_action", {"action": action, "value": value})
            finally:
                activities.release.set()
            result = await asyncio.wait_for(handle.result(), 10)
            history = await handle.fetch_history()
        replay = await Replayer(workflows=[FileDocumentWorkflow]).replay_workflow(history)
        assert replay.replay_failure is None
    assert result["status"] == status and result["reason"] == reason
    assert result["suggestion_failure_reason"] == "parse_error"
    with Session(runtime.engine) as session:
        assert session.get(Document, uuid.UUID(document_id)).status == status
        assert session.query(FiledDocument).count() == (action == "approve")
    if action == "approve":
        assert result["record_id"] == "UNIT-0002"
