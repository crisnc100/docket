import uuid

import pytest
from sqlalchemy.orm import Session
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from docket.activities import Activities
from docket.corpus import pdf
from docket.db import Document, Event, FiledDocument
from docket.ingress import Ingress
from docket.workflows import DocumentInput, FileDocumentWorkflow
from tests.workflow.conftest import ScriptedModel
from tests.workflow.scenarios import ScenarioActivities
from tests.workflow.test_integration import clean_pdf, terminal, wait_database


class UnavailableSuggestion(ScriptedModel):
    def __init__(self, error_type):
        super().__init__()
        self.error_type = error_type

    async def call(self, *, activity, **kwargs):
        if activity != "choose":
            return await super().call(activity=activity, **kwargs)
        self.calls.append(activity)
        if self.error_type == "retryable":
            raise RuntimeError("temporary model failure")
        raise ApplicationError("no suggestion", type=self.error_type, non_retryable=True)


@pytest.mark.parametrize(
    "error_type,reason,attempts",
    [
        ("ParseError", "parse_error", 1),
        ("BudgetExceeded", "budget_exceeded", 1),
        ("ReplayMiss", "replay_miss", 1),
        ("retryable", "retries_exhausted", 5),
    ],
)
@pytest.mark.parametrize("route", ["file", "review"])
async def test_choose_failure_records_reason_and_preserves_rules_route(
    runtime, monkeypatch, error_type, reason, attempts, route
):
    model = UnavailableSuggestion(error_type)
    monkeypatch.setattr(runtime, "gateway", lambda request: model)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=runtime.settings.docket_task_queue,
            workflows=[FileDocumentWorkflow],
            activities=Activities(runtime).registered(),
        ):
            document_id = await Ingress(runtime, env.client).submit(
                clean_pdf() if route == "file" else pdf(["UNIT-0001"]),
                "optional-suggestion.pdf",
                mode="act" if route == "file" else "observe",
                run_id="live" if route == "file" else uuid.uuid4().hex,
            )
            result = await terminal(env.client, document_id)
    assert result["status"] == ("filed" if route == "file" else "would_review")
    assert result["record_id"] == "UNIT-0001"
    assert result["suggested_record_id"] is None
    assert result["tier"] == ("strong" if route == "file" else "medium")
    assert model.calls == ["classify"] + ["choose"] * attempts
    with Session(runtime.engine) as session:
        score = session.query(Event).filter(Event.step == "score").one().detail
        assert score["suggested_record_id"] is None
        assert score["suggestion_failure_reason"] == reason
        assert score["rules_top_record_id"] == "UNIT-0001"
        assert session.query(Event).filter(Event.step == "failed").count() == 0
        assert session.query(FiledDocument).count() == (route == "file")
        assert session.get(Document, uuid.UUID(document_id)).status == result["status"]


async def test_choose_failure_still_allows_human_approval(runtime, monkeypatch):
    monkeypatch.setattr(runtime, "gateway", lambda request: UnavailableSuggestion("ParseError"))
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=runtime.settings.docket_task_queue,
            workflows=[FileDocumentWorkflow],
            activities=Activities(runtime).registered(),
        ):
            document_id = await Ingress(runtime, env.client).submit(
                pdf(["UNIT-0001"]), "review.pdf"
            )
            await wait_database(runtime, document_id, "needs_review")
            handle = env.client.get_workflow_handle("doc-" + document_id)
            await handle.execute_update(
                "review_action", {"action": "approve", "value": "UNIT-0002"}
            )
            result = await handle.result()
    assert result["status"] == "filed" and result["reason"] == "review_approved"
    assert result["record_id"] == "UNIT-0002" and result["suggested_record_id"] is None


class ExtractReplayMiss(ScenarioActivities):
    @activity.defn(name="extract")
    async def extract(self, stage: dict) -> dict:
        self.calls.append("extract")
        raise ApplicationError("missing extraction", type="ReplayMiss", non_retryable=True)


async def test_extract_replay_miss_remains_failed():
    activities = ExtractReplayMiss()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="extract-miss",
            workflows=[FileDocumentWorkflow],
            activities=activities.registered(),
        ):
            result = await env.client.execute_workflow(
                FileDocumentWorkflow.run,
                DocumentInput(str(uuid.uuid4()), "raw/test", "happy"),
                id=uuid.uuid4().hex,
                task_queue="extract-miss",
            )
    assert result["status"] == "failed" and result["reason"] == "replay_miss"
    assert activities.calls == ["extract"]
    assert activities.finalized == [("failed", "replay_miss")]


class GenuineFailure(ScenarioActivities):
    def __init__(self, failed_step, error_type):
        super().__init__()
        self.failed_step, self.error_type = failed_step, error_type

    def fail(self, name):
        if self.failed_step == name:
            self.attempts += 1
            raise ApplicationError("required step failed", type=self.error_type, non_retryable=True)

    @activity.defn(name="classify")
    async def classify(self, stage: dict) -> dict:
        self.fail("classify")
        return await super().classify(stage)

    @activity.defn(name="score")
    async def score(self, stage: dict) -> dict:
        self.fail("score")
        return await super().score(stage)

    @activity.defn(name="file")
    async def file(self, stage: dict) -> dict:
        self.fail("file")
        return await super().file(stage)


@pytest.mark.parametrize(
    "failed_step,error_type,reason",
    [
        ("classify", "ReplayMiss", "replay_miss"),
        ("score", "ParseError", "parse_error"),
        ("file", "StorageConflict", "storage_conflict"),
    ],
)
async def test_required_step_failure_still_finalizes_failed(failed_step, error_type, reason):
    activities = GenuineFailure(failed_step, error_type)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="required-error",
            workflows=[FileDocumentWorkflow],
            activities=activities.registered(),
        ):
            result = await env.client.execute_workflow(
                FileDocumentWorkflow.run,
                DocumentInput(str(uuid.uuid4()), "raw/test", "happy"),
                id=uuid.uuid4().hex,
                task_queue="required-error",
            )
    assert result["status"] == "failed" and result["reason"] == reason
    assert activities.attempts == 1
    assert activities.finalized == [("failed", reason)]
