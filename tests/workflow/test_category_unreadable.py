"""An unreadable category answer sends the document to a person instead of failing it."""

import uuid

import pytest
from sqlalchemy.orm import Session
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from docket.activities import Activities
from docket.db import Document, Event, FiledDocument
from docket.ingress import Ingress
from docket.llm.errors import UnreadableAnswer
from docket.workflows import FileDocumentWorkflow
from tests.workflow.conftest import ScriptedModel
from tests.workflow.test_integration import clean_pdf, terminal, wait_database


class UnreadableCategory(ScriptedModel):
    def __init__(self, reason, refusal_category=None):
        super().__init__()
        self.reason, self.refusal_category = reason, refusal_category

    async def call(self, *, activity, **kwargs):
        if activity != "classify":
            return await super().call(activity=activity, **kwargs)
        self.calls.append(activity)
        raise UnreadableAnswer(self.reason, self.refusal_category)


@pytest.mark.parametrize(
    ("reason", "refusal_category"),
    [("refusal", "bio"), ("refusal", None), ("cut", None), ("not_json", None), ("schema", None)],
)
@pytest.mark.parametrize("mode", ["act", "observe"])
async def test_unreadable_category_goes_to_review_not_failed(
    runtime, monkeypatch, reason, refusal_category, mode
):
    model = UnreadableCategory(reason, refusal_category)
    monkeypatch.setattr(runtime, "gateway", lambda request: model)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=runtime.settings.docket_task_queue,
            workflows=[FileDocumentWorkflow],
            activities=Activities(runtime).registered(),
        ):
            # A clean document the rules would file: two signals above the threshold.
            document_id = await Ingress(runtime, env.client).submit(
                clean_pdf(),
                "unreadable-category.pdf",
                mode=mode,
                run_id="live" if mode == "act" else uuid.uuid4().hex,
            )
            if mode == "act":
                await wait_database(runtime, document_id, "needs_review")
                handle = env.client.get_workflow_handle("doc-" + document_id)
                await handle.execute_update(
                    "review_action", {"action": "dismiss", "value": "checked by hand"}
                )
            result = await terminal(env.client, document_id)
    assert result["status"] == ("dismissed" if mode == "act" else "would_review")
    if mode == "observe":
        assert result["reason"] == "category_unreadable"
    assert result["category"] == "misc"
    assert result["category_failure_reason"] == reason
    assert result["refusal_category"] == refusal_category
    # The rules still ran: their top candidate and tier are unchanged by the failure.
    assert result["record_id"] == "UNIT-0001" and result["tier"] == "strong"
    assert model.calls == ["classify", "choose"]
    with Session(runtime.engine) as session:
        classify = session.query(Event).filter(Event.step == "classify").one().detail
        assert classify == {
            "category": "misc",
            "category_failure_reason": reason,
            "refusal_category": refusal_category,
        }
        score = session.query(Event).filter(Event.step == "score").one().detail
        assert score["decision"] == "review" and score["reason"] == "category_unreadable"
        assert score["rules_decision"] == "file"
        assert score["rules_reason"] == "threshold_met"
        assert score["category_failure_reason"] == reason
        assert session.query(Event).filter(Event.step == "failed").count() == 0
        assert session.query(FiledDocument).count() == 0
        assert session.get(Document, uuid.UUID(document_id)).status == result["status"]


async def test_readable_category_keeps_the_rules_route(runtime, monkeypatch):
    model = ScriptedModel()
    monkeypatch.setattr(runtime, "gateway", lambda request: model)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=runtime.settings.docket_task_queue,
            workflows=[FileDocumentWorkflow],
            activities=Activities(runtime).registered(),
        ):
            document_id = await Ingress(runtime, env.client).submit(clean_pdf(), "clean.pdf")
            result = await terminal(env.client, document_id)
    assert result["status"] == "filed" and result["reason"] == "threshold_met"
    assert result["category_failure_reason"] is None and result["refusal_category"] is None
    with Session(runtime.engine) as session:
        score = session.query(Event).filter(Event.step == "score").one().detail
        assert "rules_decision" not in score and "category_failure_reason" not in score
