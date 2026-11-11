import uuid

import pytest
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from docket.workflows import DocumentInput, FileDocumentWorkflow
from tests.workflow.scenarios import ScenarioActivities


class NonRetryableActivities(ScenarioActivities):
    def __init__(self, error_type):
        super().__init__()
        self.error_type = error_type

    @activity.defn(name="choose")
    async def choose(self, stage: dict) -> dict:
        self.calls.append("choose")
        raise ApplicationError(
            "test non-retryable failure", type=self.error_type, non_retryable=True
        )

    @activity.defn(name="score")
    async def score(self, stage: dict) -> dict:
        assert stage["chosen"] is None
        self.suggestion_failure_reason = stage["suggestion_failure_reason"]
        return await super().score(stage)


@pytest.mark.parametrize(
    "error_type,reason",
    [
        ("BudgetExceeded", "budget_exceeded"),
        ("ParseError", "parse_error"),
        ("OtherNonRetryable", "non_retryable_error"),
    ],
)
async def test_choose_errors_score_and_finalize_once_without_exhaustion_reason(error_type, reason):
    activities = NonRetryableActivities(error_type)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="error-test",
            workflows=[FileDocumentWorkflow],
            activities=activities.registered(),
        ):
            result = await env.client.execute_workflow(
                FileDocumentWorkflow.run,
                DocumentInput(str(uuid.uuid4()), "raw/synthetic", "happy"),
                id="nonretryable-" + uuid.uuid4().hex,
                task_queue="error-test",
            )
    assert result["status"] == "filed" and result["reason"] == "threshold_met"
    assert activities.calls.count("choose") == 1
    assert activities.calls.count("score") == activities.calls.count("file") == 1
    assert activities.finalized == [("filed", "threshold_met")]
    assert activities.suggestion_failure_reason == reason
