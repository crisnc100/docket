import asyncio
import json
import subprocess
import time
import uuid

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from temporalio.client import Client

from docket.db import Document, DocumentSource, Event, FiledDocument
from docket.ingress import Ingress
from tests.workflow.test_integration import clean_pdf


def docker(*args):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


class Processes:
    def __init__(self, runtime):
        self.runtime = runtime
        self.names = []
        self.queue = "fault-" + uuid.uuid4().hex
        runtime.settings.docket_task_queue = self.queue
        with runtime.engine.connect() as conn:
            schema = conn.execute(text("select current_schema()")).scalar_one()
        url = make_url(runtime.settings.database_url).set(host="postgres", port=5432)
        self.env = {
            "DATABASE_URL": url.update_query_dict(
                {"options": "-csearch_path=" + schema}
            ).render_as_string(hide_password=False),
            "DOCKET_TASK_QUEUE": self.queue,
            "S3_BUCKET": runtime.settings.s3_bucket,
            "DOCKET_REVIEW_TOKEN": runtime.settings.docket_review_token.get_secret_value(),
        }

    def start(self, service, failpoint="", gate=False):
        name = "docket-fault-" + uuid.uuid4().hex
        args = ["compose", "run", "-d", "--no-deps", "--name", name]
        if service == "api":
            args += ["-p", "127.0.0.1::8000"]
        for key, value in {
            **self.env,
            "DOCKET_FAILPOINT": failpoint,
            "DOCKET_TEST_CHOOSE_GATE": "1" if gate else "",
        }.items():
            args += ["-e", key + "=" + value]
        args += [service]
        if service == "worker":
            args += ["python", "-m", "tests.fault.worker_fixture"]
        docker(*args)
        self.names.append(name)
        return name

    def logs(self, name):
        result = subprocess.run(
            ["docker", "logs", name], capture_output=True, text=True, timeout=15
        )
        assert result.returncode == 0
        return result.stdout + result.stderr

    async def wait_log(self, name, expected, timeout=60):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if expected in self.logs(name):
                return
            await asyncio.sleep(0.2)
        raise AssertionError(f"Missing {expected}: {self.logs(name)}")

    async def ready_worker(self, **kwargs):
        name = self.start("worker", **kwargs)
        await self.wait_log(name, "Docket worker ready")
        return name

    async def ready_api(self, **kwargs):
        name = self.start("api", **kwargs)
        await self.wait_log(name, "Application startup complete")
        port = json.loads(docker("inspect", name))[0]["NetworkSettings"]["Ports"]["8000/tcp"][0][
            "HostPort"
        ]
        return name, "http://127.0.0.1:" + port

    async def fired(self, name, point):
        await self.wait_log(name, "DOCKET_FAILPOINT fired " + point)
        for _ in range(100):
            state = json.loads(docker("inspect", name))[0]["State"]
            if not state["Running"]:
                assert state["ExitCode"] == 137, state
                print(f"{point}: exit=137, fired log confirmed", flush=True)
                return
            await asyncio.sleep(0.1)
        raise AssertionError("Failpoint logged but process did not exit")

    def cleanup(self):
        for name in self.names:
            docker("rm", "-f", name)


@pytest.fixture()
def processes(runtime):
    processes = Processes(runtime)
    try:
        yield processes
    finally:
        processes.cleanup()


async def post(http, message):
    response = await http.post(
        "/documents", files={"file": ("clean.pdf", clean_pdf())}, data={"message_id": message}
    )
    assert response.status_code == 200, response.text
    return response.json()["document_id"]


async def finished(runtime, document_id):
    client = await Client.connect(runtime.settings.temporal_address)
    result = await asyncio.wait_for(client.get_workflow_handle("doc-" + document_id).result(), 90)
    assert result["status"] == "filed", result
    with Session(runtime.engine) as session:
        assert session.query(Document).count() == session.query(FiledDocument).count() == 1
        assert session.query(Event).filter(Event.step == "filed").count() == 1
        assert session.query(Event).filter(Event.step == "file").count() == 1
        assert session.get(Document, uuid.UUID(document_id)).status == "filed"
    keys = [
        item["Key"]
        for item in runtime.s3.list_objects_v2(Bucket=runtime.settings.s3_bucket)["Contents"]
    ]
    assert len(keys) == 2 and len([key for key in keys if not key.startswith("raw/")]) == 1


@pytest.mark.parametrize("point", ["file.after_storage", "choose.during"])
async def test_worker_crash_restart_exactly_once_and_http_dedup(runtime, processes, point):
    worker = await processes.ready_worker(failpoint=point)
    _, url = await processes.ready_api()
    async with httpx.AsyncClient(
        base_url=url,
        timeout=20,
        headers={
            "Authorization": "Bearer " + runtime.settings.docket_review_token.get_secret_value(),
        },
    ) as http:
        ids = await asyncio.gather(*(post(http, str(i)) for i in range(3)))
        assert len(set(ids)) == 1
        await processes.fired(worker, point)
        with Session(runtime.engine) as session:
            assert session.query(FiledDocument).count() == 0
        if point == "file.after_storage":
            assert (
                len(runtime.s3.list_objects_v2(Bucket=runtime.settings.s3_bucket)["Contents"]) == 2
            )
        await processes.ready_worker()
        await finished(runtime, ids[0])
        assert await post(http, "after-terminal-different-message") == ids[0]
    with Session(runtime.engine) as session:
        assert session.query(DocumentSource).count() == 4
        assert {s.message_id for s in session.scalars(select(DocumentSource))} == {
            "0",
            "1",
            "2",
            "after-terminal-different-message",
        }


@pytest.mark.parametrize("point", ["ingress.after_raw", "ingress.after_insert"])
async def test_api_crash_ingress_order_resubmit_and_reconcile(runtime, processes, point):
    api, url = await processes.ready_api(failpoint=point)
    async with httpx.AsyncClient(
        base_url=url,
        timeout=20,
        headers={
            "Authorization": "Bearer " + runtime.settings.docket_review_token.get_secret_value(),
        },
    ) as http:
        with pytest.raises(httpx.HTTPError):
            await post(http, "crashed-submit")
    await processes.fired(api, point)
    with Session(runtime.engine) as session:
        assert session.query(Document).count() == (point == "ingress.after_insert")
        document = session.scalar(select(Document))
        document_id = str(document.id) if document else None
    assert len(runtime.s3.list_objects_v2(Bucket=runtime.settings.s3_bucket)["Contents"]) == 1
    client = await Client.connect(runtime.settings.temporal_address)
    await processes.ready_worker()
    if point == "ingress.after_insert":
        assert (await Ingress(runtime, client).reconcile(0))["started"] == 1
        await finished(runtime, document_id)
    _, recovered_url = await processes.ready_api()
    async with httpx.AsyncClient(
        base_url=recovered_url,
        timeout=20,
        headers={
            "Authorization": "Bearer " + runtime.settings.docket_review_token.get_secret_value(),
        },
    ) as http:
        recovered_id = await post(http, "resubmitted-after-api-crash")
    if document_id:
        assert recovered_id == document_id
    await finished(runtime, recovered_id)


async def test_real_s3_outage_retries_after_confirmed_worker_failpoint(runtime, processes):
    worker = await processes.ready_worker(failpoint="choose.during")
    _, url = await processes.ready_api()
    async with httpx.AsyncClient(
        base_url=url,
        timeout=20,
        headers={
            "Authorization": "Bearer " + runtime.settings.docket_review_token.get_secret_value(),
        },
    ) as http:
        document_id = await post(http, "outage")
    await processes.fired(worker, "choose.during")
    recovered = await processes.ready_worker(gate=True)
    await processes.wait_log(recovered, "TEST choose waiting for release")
    try:
        docker("compose", "stop", "seaweedfs")
        docker("exec", recovered, "touch", "/tmp/docket-release-choose")
        await processes.wait_log(recovered, "EndpointConnectionError")
        with Session(runtime.engine) as session:
            assert session.query(FiledDocument).count() == 0
    finally:
        docker("compose", "up", "-d", "--wait", "seaweedfs")
    await finished(runtime, document_id)
