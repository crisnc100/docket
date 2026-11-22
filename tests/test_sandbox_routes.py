"""Every route classified, and no sandbox-scoped route shows one visitor another's
documents (plan PR 1, S2 and S4)."""

import hashlib
import os
import uuid
from contextlib import asynccontextmanager

import httpx
import pytest

from docket.api import create_app
from docket.corpus import sample_document
from docket.ingress import Ingress
from docket.review_access import BETA_ROUTES, Access
from docket.runtime import Runtime
from docket.sandbox import raw_key
from docket.sweep import record_complete
from tests.sandboxes import held_in, make_sandbox, sandbox_cookie
from tests.test_beta_gate import BASE, FakeTemporal, beta_settings, route_table

PDF = b"%PDF-1.4\n% sandbox route test\n"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("DOCKET_FAILPOINT", "DOCKET_DAILY_BUDGET_USD")):
            monkeypatch.delenv(name)


@pytest.fixture()
def beta_runtime(postgres_engine):
    runtime = Runtime(beta_settings(s3_bucket="sandbox-routes-" + uuid.uuid4().hex))
    runtime.engine.dispose()
    runtime.engine = postgres_engine
    runtime.ensure_bucket()
    # Samples wait for a recent sweep.
    record_complete(postgres_engine)
    try:
        yield runtime
    finally:
        bucket = runtime.settings.s3_bucket
        for item in runtime.s3.list_objects_v2(Bucket=bucket).get("Contents", []):
            runtime.s3.delete_object(Bucket=bucket, Key=item["Key"])
        runtime.s3.delete_bucket(Bucket=bucket)
        runtime.close()


class RecordingTemporal(FakeTemporal):
    def __init__(self):
        super().__init__()
        self.started = []

    async def start_workflow(self, workflow, request, **kwargs):
        self.started.append(request)


class FakeIngress:
    def __init__(self):
        self.sandboxes = []

    async def submit(self, content, filename, **kwargs):
        self.sandboxes.append(kwargs.get("sandbox"))
        return str(uuid.uuid4())


@asynccontextmanager
async def beta_app(runtime, temporal):
    app = create_app(runtime, temporal)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as http:
            yield app, http


def visitor(token: str | None) -> dict:
    return ({"Cookie": sandbox_cookie(token)} if token else {}) | {"Origin": BASE}


def store(runtime, sandbox_id, content: bytes) -> str:
    digest = hashlib.sha256(content).hexdigest()
    runtime.s3.put_object(
        Bucket=runtime.settings.s3_bucket, Key=raw_key(sandbox_id, digest), Body=content
    )
    return digest


async def test_every_app_route_is_in_the_table_and_the_table_lists_no_other():
    async with beta_app(Runtime(beta_settings()), FakeTemporal()) as (app, _):
        assert set(route_table(app)) == set(BETA_ROUTES)
    # The docs pages are app routes too; the beta serves none of them.
    assert BETA_ROUTES[("GET", "/docs")] == BETA_ROUTES[("GET", "/openapi.json")] == Access.REFUSED


async def test_no_sandbox_scoped_route_answers_for_another_visitors_document(beta_runtime):
    engine = beta_runtime.engine
    owner, owner_token = make_sandbox(engine, minutes_left=40)
    other, other_token = make_sandbox(engine)
    _, expired_token = make_sandbox(engine, minutes_left=-1)
    document = held_in(engine, owner)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE documents SET content_hash = %s WHERE id = %s",
            (store(beta_runtime, owner, PDF), document),
        )
    temporal = RecordingTemporal()
    called = set()
    async with beta_app(beta_runtime, temporal) as (app, http):
        app.state.ingress = ingress = FakeIngress()

        async def call(method, template, token, document_id=document):
            # Each call is its own browser: only the cookie a test passes is sent.
            http.cookies.clear()
            called.add((method, template))
            path = template.replace("{document_id}", str(document_id))
            if template == "/review/{document_id}/{action}":
                bodies = [("approve", {"record_id": "UNIT-0001"}), ("dismiss", {"reason": "No."})]
                return [
                    await http.post(
                        path.replace("{action}", action), json=body, headers=visitor(token)
                    )
                    for action, body in bodies
                ]
            body = {"case_type": "clean"} if method == "POST" else None
            return [await http.request(method, path, json=body, headers=visitor(token))]

        def seen(responses):
            return [(response.status_code, response.content) for response in responses]

        with_id = [key for key in BETA_ROUTES if "{document_id}" in key[1]]
        for method, template in with_id:
            assert BETA_ROUTES[method, template] == Access.SANDBOX
            unknown = seen(await call(method, template, owner_token, uuid.uuid4()))
            assert {status for status, _ in unknown} == {404}
            assert {body for _, body in unknown} == {b'{"detail":"Unknown document"}'}
            for token in (other_token, None, expired_token):
                assert seen(await call(method, template, token)) == unknown, (template, token)
            owned = await call(method, template, owner_token)
            assert all(response.status_code == 200 for response in owned), template
        # Only the owner's two review actions reached Temporal.
        assert [update[0] for update in temporal.updates] == [f"doc-{document}"] * 2

        queues = {}
        for token in (other_token, None, expired_token, owner_token):
            [response] = await call("GET", "/review", token)
            queues[token] = response.json()["items"]
        assert queues[other_token] == queues[None] == queues[expired_token] == []
        assert [item["document_id"] for item in queues[owner_token]] == [str(document)]

        options = {
            token: (await call("GET", "/beta/samples", token))[0].json()
            for token in (other_token, None, expired_token, owner_token)
        }
        assert len({str(value) for value in options.values()}) == 1

        for token in (other_token, None, expired_token, owner_token):
            [response] = await call("POST", "/beta/samples", token)
            assert response.status_code == 200, response.text
        # A sample lands in the sender's own sandbox, or a new one, never in another's.
        sent_to = ingress.sandboxes
        assert sent_to[0] == other and sent_to[3] == owner
        assert len(set(sent_to)) == 4 and None not in sent_to

        # The countdown is each visitor's own; the owner's sandbox lives 40 minutes.
        left = {
            token: (await call("GET", "/sandbox", token))[0].json()["expires_in"]
            for token in (other_token, None, expired_token, owner_token)
        }
        assert left[None] is None and left[expired_token] is None
        assert 2990 <= left[other_token] <= 3000 and 2390 <= left[owner_token] <= 2400
        # Delete now deletes only the caller's own sandbox.
        for token in (other_token, None, expired_token):
            [response] = await call("DELETE", "/sandbox", token)
            assert response.json() == {"deleted": True}
        [response] = await call("GET", "/documents/{document_id}", owner_token)
        assert response.status_code == 200
        assert (await call("GET", "/sandbox", owner_token))[0].json()["expires_in"] > 2300

    scoped = {key for key, access in BETA_ROUTES.items() if access == Access.SANDBOX}
    assert called == scoped


async def test_two_sandboxes_with_the_same_bytes_get_two_documents_files_and_previews(
    beta_runtime,
):
    engine = beta_runtime.engine
    first, first_token = make_sandbox(engine)
    second, second_token = make_sandbox(engine)
    sample = sample_document("clean", 7)
    temporal = RecordingTemporal()
    ingress = Ingress(beta_runtime, temporal)
    ids = [
        await ingress.submit(sample.content, sample.filename, source="sample", sandbox=sandbox)
        for sandbox in (first, second)
    ]
    assert len(set(ids)) == 2
    # The same bytes again, in the first sandbox, are the same document.
    again = await ingress.submit(sample.content, sample.filename, source="sample", sandbox=first)
    assert again == ids[0]
    digest = hashlib.sha256(sample.content).hexdigest()
    listed = beta_runtime.s3.list_objects_v2(Bucket=beta_runtime.settings.s3_bucket)
    assert sorted(item["Key"] for item in listed["Contents"]) == sorted(
        [f"sbx/{first}/raw/{digest}", f"sbx/{second}/raw/{digest}"]
    )
    assert [request.raw_key for request in temporal.started[:2]] == [
        f"sbx/{first}/raw/{digest}",
        f"sbx/{second}/raw/{digest}",
    ]
    async with beta_app(beta_runtime, temporal) as (_, http):
        for document_id, token in zip(ids, (first_token, second_token), strict=True):
            preview = await http.get(f"/documents/{document_id}/file", headers=visitor(token))
            assert preview.status_code == 200 and preview.content == sample.content
        crossed = await http.get(f"/documents/{ids[0]}/file", headers=visitor(second_token))
        assert crossed.status_code == 404
