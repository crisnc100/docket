import asyncio
import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import is_cancelled_exception
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from docket import evaluation
from docket.corpus import pdf
from docket.workflows import DocumentInput, FileDocumentWorkflow
from tests.test_evaluation import label
from tests.workflow.conftest import ScriptedModel
from tests.workflow.scenarios import ScenarioActivities
from tests.workflow.test_optional_suggestion import UnavailableSuggestion


class WaitingChoose(ScenarioActivities):
    def __init__(self):
        super().__init__()
        self.started = asyncio.Event()

    @activity.defn(name="choose")
    async def choose(self, stage: dict) -> dict:
        self.calls.append("choose")
        self.started.set()
        while True:
            activity.heartbeat()
            await asyncio.sleep(0.01)


async def test_workflow_cancel_during_choose_never_scores_or_files():
    activities = WaitingChoose()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="cancel-choose",
            workflows=[FileDocumentWorkflow],
            activities=activities.registered(),
        ):
            handle = await env.client.start_workflow(
                FileDocumentWorkflow.run,
                DocumentInput(str(uuid.uuid4()), "raw/test", "happy"),
                id=uuid.uuid4().hex,
                task_queue="cancel-choose",
            )
            await asyncio.wait_for(activities.started.wait(), 10)
            await handle.cancel()
            with pytest.raises(WorkflowFailureError) as failure:
                await asyncio.wait_for(handle.result(), 10)
            assert is_cancelled_exception(failure.value.cause)
    assert activities.calls == ["extract", "shortlist", "classify", "choose"]
    assert activities.finalized == [("failed", "cancelled")]


@pytest.mark.parametrize("failed", [False, True])
async def test_model_outcomes_carries_suggestion_failure_only_when_present(
    runtime, monkeypatch, tmp_path, failed
):
    model = UnavailableSuggestion("ParseError") if failed else ScriptedModel()
    monkeypatch.setattr(runtime, "gateway", lambda request: model)
    (tmp_path / "doc.pdf").write_bytes(pdf(["UNIT-0001"]))

    class BorrowedRuntime:
        def __init__(self, settings):
            self.settings = settings

        def __getattr__(self, name):
            return getattr(runtime, name)

        def close(self):
            pass

    async with await WorkflowEnvironment.start_time_skipping() as env:
        monkeypatch.setattr(evaluation, "Runtime", BorrowedRuntime)
        monkeypatch.setattr(
            evaluation, "Client", SimpleNamespace(connect=AsyncMock(return_value=env.client))
        )
        results, recordings, cost = await evaluation.model_outcomes(
            [label(expected="review")], tmp_path, runtime.settings, "replay", Decimal(0)
        )
    assert len(results) == 1 and results[0].status == "would_review"
    assert results[0].suggestion == (
        {"record_id": None, "suggestion_failure_reason": "parse_error"}
        if failed
        else {"record_id": "UNIT-0001"}
    )
    assert recordings == [] and cost == 0
