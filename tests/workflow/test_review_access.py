import asyncio
import base64
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

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
from tests.workflow.test_integration import wait_database
from tests.workflow.test_review_api import SECRET, signed
from tests.workflow.test_workflow import wait_review


@asynccontextmanager
async def access_client(runtime, monkeypatch, *, configured=True):
    token = uuid.uuid4().hex
    runtime.settings = runtime.settings.model_copy(
        update={
            "docket_review_token": SecretStr(token) if configured else None,
            "docket_webhook_secret": SecretStr(SECRET),
        }
    )
    monkeypatch.setattr(runtime, "gateway", lambda request: ScriptedModel())
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=runtime.settings.docket_task_queue,
            workflows=[FileDocumentWorkflow],
            activities=Activities(runtime).registered(),
        ):
            content = pdf(["UNIT-0001"])
            document_id = await Ingress(runtime, env.client).submit(content, "review.pdf")
            await wait_database(runtime, document_id, "needs_review")
            await wait_review(env.client.get_workflow_handle("doc-" + document_id))
            app = create_app(runtime, env.client)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(
                        app,
                        raise_app_exceptions=False,
                    ),
                    base_url="http://test",
                ) as http:
                    try:
                        yield http, token, document_id, content
                    finally:
                        handle = env.client.get_workflow_handle("doc-" + document_id)
                        initial = await handle.query(FileDocumentWorkflow.progress)
                        if initial["review_open"]:
                            await handle.execute_update(
                                "review_action",
                                {
                                    "action": "dismiss",
                                    "value": "Access test complete",
                                },
                            )
                        for _ in range(300):
                            state = await handle.query(FileDocumentWorkflow.progress)
                            if state["status"] not in {"processing", "needs_review"}:
                                break
                            await asyncio.sleep(0.01)
                        else:
                            raise AssertionError("Access-test workflow did not finish cleanup")


def protected_requests(document_id, content):
    asset = next(Path("web/dist/assets").glob("*.js")).name
    return [
        ("POST", "/documents", {"files": {"file": ("new.pdf", content)}}),
        ("GET", f"/documents/{document_id}", {}),
        ("GET", f"/documents/{document_id}/file", {}),
        ("GET", "/review", {}),
        ("POST", f"/review/{document_id}/approve", {"json": {"record_id": "UNIT-0001"}}),
        ("POST", f"/review/{document_id}/dismiss", {"json": {"reason": "not relevant"}}),
        ("GET", "/", {}),
        ("GET", "/assets/" + asset, {}),
        ("GET", "/docs", {}),
        ("GET", "/redoc", {}),
        ("GET", "/openapi.json", {}),
    ]


@pytest.mark.parametrize("route", range(11))
async def test_every_protected_route_requires_bearer_and_accepts_correct_token(
    runtime,
    monkeypatch,
    route,
):
    async with access_client(runtime, monkeypatch) as (http, token, document_id, content):
        method, path, arguments = protected_requests(document_id, content)[route]
        for headers in [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic " + token}]:
            response = await http.request(method, path, headers=headers, **arguments)
            assert response.status_code == 401, (method, path, response.text)
        with Session(runtime.engine) as session:
            assert session.get(Document, uuid.UUID(document_id)).status == "needs_review"
            assert session.query(DocumentSource).count() == 1
        response = await http.request(
            method, path, headers={"Authorization": "Bearer " + token}, **arguments
        )
        assert response.status_code == 200, (method, path, response.text)
        if path.endswith("/file"):
            assert response.content == content
        if path == "/":
            assert token not in response.text


@pytest.mark.parametrize("unset", [None, ""])
async def test_unset_reviewer_token_disables_protected_routes_but_keeps_public_routes(
    runtime,
    monkeypatch,
    unset,
):
    async with access_client(runtime, monkeypatch, configured=False) as (
        http,
        _,
        document_id,
        content,
    ):
        runtime.settings.docket_review_token = SecretStr(unset) if unset is not None else None
        for method, path, arguments in protected_requests(document_id, content):
            response = await http.request(method, path, **arguments)
            assert response.status_code == 503, (method, path, response.text)
            assert response.headers["content-type"].startswith("text/plain")
            assert "DOCKET_REVIEW_TOKEN" in response.text
        assert (await http.get("/healthz")).status_code == 200
        assert (await http.post("/inbound", content=b"{}")).status_code == 401


@pytest.mark.parametrize("field", ["message_id", "from", "filename"])
async def test_webhook_rejects_nul_metadata_before_any_effect(runtime, monkeypatch, field):
    async with access_client(runtime, monkeypatch) as (http, _, _, content):
        body = {
            "message_id": "message",
            "from": "sender",
            "attachments": [
                {"filename": "test.pdf", "content": base64.b64encode(content).decode()},
            ],
        }
        if field == "filename":
            body["attachments"][0][field] += "\x00"
        else:
            body[field] += "\x00"
        raw = json.dumps(body).encode()
        response = await http.post("/inbound", content=raw, headers=signed(raw))
        assert response.status_code == 422, response.text
        with Session(runtime.engine) as session:
            assert session.query(Document).count() == session.query(DocumentSource).count() == 1


@pytest.mark.parametrize("field", ["message_id", "sender", "filename"])
async def test_upload_rejects_nul_metadata(runtime, monkeypatch, field):
    async with access_client(runtime, monkeypatch) as (http, token, _, content):
        data = {field: "nul\x00value"} if field != "filename" else {}
        name = "nul\x00value.pdf" if field == "filename" else "test.pdf"
        headers = {"Authorization": "Bearer " + token}
        if field == "filename":
            headers["Content-Type"] = "multipart/form-data; boundary=metadata-test"
            body = (
                b'--metadata-test\r\nContent-Disposition: form-data; name="file"; '
                b'filename="nul\x00value.pdf"\r\nContent-Type: application/pdf\r\n\r\n'
                + content
                + b"\r\n--metadata-test--\r\n"
            )
            response = await http.post("/documents", content=body, headers=headers)
        else:
            response = await http.post(
                "/documents", data=data, files={"file": (name, content)}, headers=headers
            )
        assert response.status_code == 422, response.text
        with Session(runtime.engine) as session:
            assert session.query(DocumentSource).count() == 1


@pytest.mark.parametrize("digits", [310, 4300])
async def test_webhook_numeric_timestamp_overflow_is_401(runtime, monkeypatch, digits):
    async with access_client(runtime, monkeypatch) as (http, _, _, _):
        response = await http.post("/inbound", content=b"{}", headers=signed(b"{}", "9" * digits))
        assert response.status_code == 401
