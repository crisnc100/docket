"""S6b with the real Temporal container stopped: the sweep still deletes rows and objects,
and the histories go on the first run after Temporal is back, with the client the service
already held."""

import subprocess
import uuid

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode

from docket.corpus import pdf
from docket.db import Document, Sandbox
from docket.ingress import Ingress
from docket.sweep import sweep_once, temporal_connector
from tests.sandboxes import make_sandbox


def docker(*args):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


async def test_the_sweep_carries_on_while_temporal_is_stopped_and_catches_up_after(runtime):
    runtime.settings = runtime.settings.model_copy(
        update={
            "docket_task_queue": "sweep-fault-" + uuid.uuid4().hex,
            "seaweedfs_master_url": "http://127.0.0.1:9333",
        }
    )
    address = runtime.settings.temporal_address
    temporal = await Client.connect(address)
    engine = runtime.engine
    sandbox_id, _ = make_sandbox(engine)
    ingress = Ingress(runtime, temporal)
    sent = [
        await ingress.submit(pdf([f"Fault test {uuid.uuid4().hex}"]), "t.pdf", sandbox=sandbox_id)
        for _ in range(2)
    ]
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE sandboxes SET expires_at = now() WHERE id = :id"), {"id": sandbox_id}
        )
    # Connected before Temporal stops, as the running service would be.
    connect = temporal_connector(address)
    await connect()
    try:
        # Inside the try, so a stop that fails partway still restarts Temporal for later tests.
        docker("compose", "stop", "temporal")
        report = await sweep_once(runtime, connect)
        assert report["temporal"] == "failed" and report["complete"] is False
        assert report["postgres"] == report["storage"] == "complete"
        with Session(engine) as session:
            assert session.scalar(select(func.count()).where(Sandbox.id == sandbox_id)) == 0
            assert session.scalar(select(func.count()).select_from(Document)) == 0
        listed = runtime.s3.list_objects_v2(
            Bucket=runtime.settings.s3_bucket, Prefix=f"sbx/{sandbox_id}/"
        )
        assert listed.get("Contents", []) == []
    finally:
        docker("compose", "up", "-d", "--wait", "temporal")
    report = await sweep_once(runtime, connect)
    assert report["complete"] is True and report["temporal_deleted"] == 2, report
    checker = await Client.connect(address)
    for document_id in sent:
        try:
            await checker.get_workflow_handle(f"doc-{document_id}").describe()
        except RPCError as exc:
            assert exc.status == RPCStatusCode.NOT_FOUND
        else:
            raise AssertionError(f"doc-{document_id} outlived the sweep")
