import asyncio
import uuid
from pathlib import Path

import pytest
import yaml
from temporalio.client import WorkflowHistory, WorkflowUpdateFailedError
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.service import RPCError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from docket.workflows import DocumentInput, FileDocumentWorkflow
from tests.workflow.scenarios import ScenarioActivities


async def wait_review(handle):
    for _ in range(200):
        if (await handle.query(FileDocumentWorkflow.progress))["review_open"]:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Workflow did not enter review")


@pytest.mark.parametrize(
    "case,mode,status,reason",
    [
        ("happy", "act", "filed", "threshold_met"),
        ("happy", "observe", "would_file", "threshold_met"),
        ("injection", "observe", "would_review", "uncertain"),
        ("empty", "observe", "would_review", "uncertain"),
        ("review", "act", "needs_review_expired", "review_timeout"),
        ("poison", "act", "rejected", "corrupt"),
        ("rejected", "act", "rejected", "encrypted"),
        ("failed", "act", "failed", "retries_exhausted"),
        ("replay_miss", "act", "needs_review_expired", "review_timeout"),
    ],
)
async def test_workflow_terminals_and_bounded_retries(case, mode, status, reason):
    activities = ScenarioActivities()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test",
            workflows=[FileDocumentWorkflow],
            activities=activities.registered(),
        ):
            request = DocumentInput(
                str(uuid.uuid4()),
                "raw/test-hash",
                case,
                mode=mode,
                run_id="eval-test" if mode == "observe" else "live",
                review_timeout_seconds=5,
            )
            result = await env.client.execute_workflow(
                FileDocumentWorkflow.run, request, id=str(uuid.uuid4()), task_queue="test"
            )
    assert result["status"] == status and result["reason"] == reason
    assert activities.finalized[-1] == (status, reason)
    if mode == "observe":
        assert "file" not in activities.calls
    if case == "empty":
        assert "choose" not in activities.calls and "classify" not in activities.calls
    if case == "failed":
        assert activities.attempts == 5
    if case == "replay_miss":
        assert activities.calls.count("choose") == activities.calls.count("score") == 1
        assert result["suggested_record_id"] is None
        assert activities.finalized == [
            ("needs_review", "uncertain"),
            ("needs_review_expired", "review_timeout"),
        ]


@pytest.mark.parametrize(
    "action,value,status",
    [
        ("approve", "UNIT-0001", "filed"),
        ("approve", "UNIT-0002", "filed"),
        ("dismiss", "not relevant", "dismissed"),
    ],
)
async def test_review_signals_and_duplicate_ignored(action, value, status):
    activities = ScenarioActivities()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test",
            workflows=[FileDocumentWorkflow],
            activities=activities.registered(),
        ):
            handle = await env.client.start_workflow(
                FileDocumentWorkflow.run,
                DocumentInput(str(uuid.uuid4()), "raw/test-hash", "review"),
                id=str(uuid.uuid4()),
                task_queue="test",
            )
            await wait_review(handle)
            await handle.signal(action, value)
            await handle.signal(action, value)
            result = await handle.result()
            assert result["status"] == status
            assert activities.calls.count("file") == (1 if status == "filed" else 0)


async def test_unknown_update_and_late_approval_are_rejected():
    activities = ScenarioActivities()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test",
            workflows=[FileDocumentWorkflow],
            activities=activities.registered(),
        ):
            handle = await env.client.start_workflow(
                FileDocumentWorkflow.run,
                DocumentInput(
                    str(uuid.uuid4()), "raw/test-hash", "review", review_timeout_seconds=5
                ),
                id=str(uuid.uuid4()),
                task_queue="test",
            )
            await wait_review(handle)
            for action in [
                {"action": "approve", "value": "UNIT-9999"},
                {"action": "dismiss", "value": ""},
                {"action": "invalid"},
            ]:
                with pytest.raises(WorkflowUpdateFailedError):
                    await handle.execute_update("review_action", action)
            await handle.signal("approve", "UNIT-9999")
            assert (await handle.query(FileDocumentWorkflow.progress))["review_open"]
            result = await handle.result()
            assert result["status"] == "needs_review_expired"
            with pytest.raises((WorkflowUpdateFailedError, RPCError)):
                await handle.execute_update(
                    "review_action", {"action": "approve", "value": "UNIT-0001"}
                )
            assert "file" not in activities.calls


async def test_duplicate_workflow_start_uses_existing_and_rejects_completed_reuse():
    from temporalio.exceptions import WorkflowAlreadyStartedError

    activities = ScenarioActivities()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test",
            workflows=[FileDocumentWorkflow],
            activities=activities.registered(),
        ):
            request = DocumentInput(str(uuid.uuid4()), "raw/test-hash", "review")
            workflow_id = "doc-" + request.document_id
            first = await env.client.start_workflow(
                FileDocumentWorkflow.run,
                request,
                id=workflow_id,
                task_queue="test",
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            )
            second = await env.client.start_workflow(
                FileDocumentWorkflow.run,
                request,
                id=workflow_id,
                task_queue="test",
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            )
            assert first.first_execution_run_id == second.first_execution_run_id
            await wait_review(first)
            await first.execute_update("review_action", {"action": "approve", "value": "UNIT-0001"})
            assert (await first.result())["status"] == "filed"
            with pytest.raises(WorkflowAlreadyStartedError):
                await env.client.start_workflow(
                    FileDocumentWorkflow.run,
                    request,
                    id=workflow_id,
                    task_queue="test",
                    id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                    id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
                )
            assert activities.calls.count("file") == 1


@pytest.mark.parametrize(
    "name,path", [("recorded-happy", "history.yaml"), ("recorded-review", "review-history.yaml")]
)
async def test_committed_workflow_history_replays(name, path):
    history = WorkflowHistory.from_json(
        name, yaml.safe_load((Path("tests/workflow") / path).read_text())
    )
    result = await Replayer(workflows=[FileDocumentWorkflow]).replay_workflow(history)
    assert result.replay_failure is None
