import asyncio
import base64
import hashlib
import hmac
import json
import time
import uuid

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy.orm import Session
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from docket.activities import Activities
from docket.api import create_app
from docket.corpus import pdf
from docket.db import Document, DocumentSource
from docket.ingress import Ingress
from docket.workflows import FileDocumentWorkflow
from tests.workflow.conftest import ScriptedModel
from tests.workflow.test_integration import clean_pdf, terminal, wait_database

SECRET = str(uuid.uuid4())


def signed(body, timestamp=None):
    timestamp = str(int(time.time())) if timestamp is None else str(timestamp)
    digest = hmac.new(SECRET.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return {"X-Docket-Timestamp": timestamp, "X-Docket-Signature": "sha256=" + digest}


def payload():
    return json.dumps(
        {
            "message_id": "message-1",
            "from": "sender@example.test",
            "attachments": [
                {"filename": "first.pdf", "content": base64.b64encode(clean_pdf()).decode()},
                {"filename": "second.pdf", "content": base64.b64encode(pdf(["second"])).decode()},
            ],
        }
    ).encode()


async def test_webhook_authentication_rate_limit_and_concurrent_idempotence(runtime):
    runtime.settings.docket_webhook_secret = SecretStr(SECRET)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        app = create_app(runtime, env.client)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app),
                base_url="http://test",
            ) as http:
                body = payload()
                for headers in [
                    {},
                    signed(body) | {"X-Docket-Signature": "sha256=wrong"},
                    signed(body, int(time.time()) - 301),
                    signed(body, int(time.time()) + 400),
                    signed(body) | {"X-Docket-Timestamp": "invalid"},
                    signed(body + b"tampered"),
                ]:
                    assert (
                        await http.post("/inbound", content=body, headers=headers)
                    ).status_code == 401
                replies = await asyncio.gather(
                    *[http.post("/inbound", content=body, headers=signed(body)) for _ in range(3)]
                )
                assert [reply.status_code for reply in replies] == [200, 200, 200]
                ids = replies[0].json()["document_ids"]
                assert len(ids) == len(set(ids)) == 2
                assert all(reply.json()["document_ids"] == ids for reply in replies)
                with Session(runtime.engine) as session:
                    assert session.query(Document).count() == 2
                    assert session.query(DocumentSource).count() == 2
                app.state.rate_limit.limit = 1
                assert (
                    await http.post("/inbound", content=body, headers=signed(body))
                ).status_code == 429
                # A forwarded header cannot choose another rate-limit bucket.
                assert (
                    await http.post(
                        "/inbound",
                        content=body,
                        headers={
                            **signed(body),
                            "X-Forwarded-For": "another-client",
                        },
                    )
                ).status_code == 429
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(
                        app,
                        client=("192.0.2.2", 1234),
                    ),
                    base_url="http://test",
                ) as other:
                    assert (
                        await other.post("/inbound", content=body, headers=signed(body))
                    ).status_code == 200
                with Session(runtime.engine) as session:
                    assert session.query(DocumentSource).count() == 2


async def test_webhook_invalid_base64_is_atomic_and_empty_secret_fails_closed(runtime):
    runtime.settings.docket_webhook_secret = SecretStr(SECRET)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        app = create_app(runtime, env.client)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://test"
            ) as http:
                data = json.loads(payload())
                data["attachments"][1]["content"] = "invalid!"
                body = json.dumps(data).encode()
                assert (
                    await http.post("/inbound", content=body, headers=signed(body))
                ).status_code == 422
                with Session(runtime.engine) as session:
                    assert session.query(Document).count() == 0
                runtime.settings.docket_webhook_secret = SecretStr("")
                assert (
                    await http.post("/inbound", content=body, headers=signed(body))
                ).status_code == 401


@pytest.mark.parametrize("choice", ["UNIT-0001", "none"])
@pytest.mark.parametrize("strong", [True, False])
async def test_suggestion_never_changes_rules_tier_or_filing(runtime, monkeypatch, choice, strong):
    model = ScriptedModel(choice)
    monkeypatch.setattr(runtime, "gateway", lambda request: model)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=runtime.settings.docket_task_queue,
            workflows=[FileDocumentWorkflow],
            activities=Activities(runtime).registered(),
        ):
            document_id = await Ingress(runtime, env.client).submit(
                clean_pdf() if strong else pdf(["UNIT-0001"]),
                "document.pdf",
                mode="observe",
                run_id=uuid.uuid4().hex,
            )
            result = await terminal(env.client, document_id)
    assert result["suggested_record_id"] == (None if choice == "none" else choice)
    assert result["record_id"] == "UNIT-0001"
    assert result["tier"] == ("strong" if strong else "medium")
    assert result["score"] == ("0.9500000" if strong else "0.3000000")
    assert result["status"] == ("would_file" if strong else "would_review")


async def test_review_queue_preview_null_score_and_dismiss(runtime, monkeypatch):
    monkeypatch.setattr(runtime, "gateway", lambda request: ScriptedModel())
    async with await WorkflowEnvironment.start_time_skipping() as env:
        app = create_app(runtime, env.client)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app),
                base_url="http://test",
                headers={
                    "Authorization": "Bearer "
                    + runtime.settings.docket_review_token.get_secret_value()
                },
            ) as http:
                # No worker yet: this is a persisted, unscored upload.
                content = pdf(["UNIT-0001"])
                response = await http.post(
                    "/documents",
                    files={"file": ("single.pdf", content)},
                    data={"sender": "sender@example.test"},
                )
                document_id = response.json()["document_id"]
                assert (await http.get(f"/documents/{document_id}")).json()["score"] is None
                preview = await http.get(f"/documents/{document_id}/file")
                assert preview.status_code == 200 and preview.content == content
                assert preview.headers["content-type"] == "application/pdf"
                assert preview.headers["content-disposition"] == "inline"
                assert (await http.get(f"/documents/{uuid.uuid4()}/file")).status_code == 404
                async with Worker(
                    env.client,
                    task_queue=runtime.settings.docket_task_queue,
                    workflows=[FileDocumentWorkflow],
                    activities=Activities(runtime).registered(),
                ):
                    await wait_database(runtime, document_id, "needs_review")
                    queue = (await http.get("/review")).json()["items"]
                    assert len(queue) == 1
                    item = queue[0]
                    assert item["document_id"] == document_id
                    assert (
                        item["filename"] == "single.pdf" and item["sender"] == "sender@example.test"
                    )
                    assert item["suggested_record_id"] == item["rules_top_record_id"] == "UNIT-0001"
                    assert item["reason"] == "insufficient_corroboration"
                    assert item["candidates"][0]["score"] == item["score"] == "0.3000000"
                    assert item["candidates"][0]["signals"]["explicit_id"] == 1
                    assert (
                        await http.post(
                            f"/review/{document_id}/dismiss", json={"reason": "unrelated"}
                        )
                    ).status_code == 200
                    assert (await terminal(env.client, document_id))["status"] == "dismissed"
                    assert (await http.get("/review")).json()["items"] == []
                    assert (
                        await http.post(
                            f"/review/{document_id}/approve", json={"record_id": "UNIT-0001"}
                        )
                    ).status_code == 409
