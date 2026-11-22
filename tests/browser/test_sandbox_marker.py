"""S7: a marker in three sandboxes on the compose stack, with the beta API and worker in
their own containers. One sandbox goes by Delete now and one by the sweep. Before, each
marker is in every store; after, the two deleted ones are in none, while the kept one's
still is, which shows the search would have found them. No container's logs hold any.

It sits with the browser tests because their CI job is the one that builds the images."""

import asyncio
import io
import json
import os
import secrets
import subprocess
import uuid

import httpx
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from temporalio.client import Client

from docket.db import Document
from docket.ingress import Ingress
from docket.sandbox import raw_key
from docket.sweep import record_complete, sweep_once, temporal_connector
from tests.sandboxes import make_sandbox, sandbox_cookie
from tests.test_sandbox_gate import pg_dump

BASE = "http://localhost:8000"


def docker(*args, env=None) -> str:
    # Values travel in env and the arguments carry only their names ("-e NAME").
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=120, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


def uncompressed_pdf(lines: list[str]) -> bytes:
    """Written without compression, so the marker's bytes are the stored object's bytes."""
    stream = io.BytesIO()
    document = canvas.Canvas(stream, pagesize=letter, invariant=1, pageCompression=0)
    y = 750
    for line in lines:
        document.drawString(40, y, line)
        y -= 14
    document.showPage()
    document.save()
    return stream.getvalue()


def folder(service: str) -> bytes:
    """Every byte of the service's data folder, as a tar stream."""
    container = docker("compose", "ps", "-q", service)
    return subprocess.run(
        ["docker", "cp", f"{container}:/data", "-"], capture_output=True, timeout=120, check=True
    ).stdout


def container_logs(name: str) -> str:
    """Both streams of one read, which must succeed: a failed read is no evidence."""
    result = subprocess.run(["docker", "logs", name], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    return result.stdout + result.stderr


def found(markers: dict[str, str], haystack: bytes | str) -> set[str]:
    data = haystack.encode() if isinstance(haystack, str) else haystack
    return {name for name, marker in markers.items() if marker.encode() in data}


async def wait_for(engine, document_id: str, status: str, logs: str) -> None:
    for _ in range(300):
        with Session(engine) as session:
            current = session.get(Document, uuid.UUID(document_id)).status
        if current == status:
            return
        await asyncio.sleep(0.2)
    raise AssertionError(f"Document stayed {current}: " + docker("logs", logs))


async def test_deleted_sandboxes_leave_their_marker_in_no_store_and_no_log(runtime):
    queue = "marker-" + uuid.uuid4().hex
    names = []
    with runtime.engine.connect() as connection:
        schema = connection.execute(text("select current_schema()")).scalar_one()
    url = make_url(runtime.settings.database_url).set(host="postgres", port=5432)
    environment = {
        "DATABASE_URL": url.update_query_dict(
            {"options": "-csearch_path=" + schema}
        ).render_as_string(hide_password=False),
        "DOCKET_TASK_QUEUE": queue,
        "S3_BUCKET": runtime.settings.s3_bucket,
        "SEAWEEDFS_MASTER_URL": "http://seaweedfs:9333",
        "DOCKET_MODEL_MODE": "replay",
        "DOCKET_ENV": "beta",
        "DOCKET_ADDRESS_KEY": secrets.token_hex(32),
        "DOCKET_TRUST_PROXY": "1",
        "DOCKET_BASE_URL": BASE,
        "DOCKET_DAILY_BUDGET_USD": "1.00",
        "DOCKET_REVIEW_TOKEN": "",
    }
    markers = {name: f"DKTMARK{name}{secrets.token_hex(12)}" for name in ("now", "swept", "kept")}
    # Review actions wait for a recent sweep, and none has run here yet.
    record_complete(runtime.engine)
    engine = runtime.engine
    try:
        for service in ("worker", "api"):
            name = "docket-marker-" + uuid.uuid4().hex
            args = ["compose", "run", "-d", "--no-deps", "--name", name]
            if service == "api":
                args += ["-p", "127.0.0.1::8000"]
            for key in environment:
                args += ["-e", key]
            docker(*args, service, env=os.environ | environment)
            names.append(name)
        worker, api = names
        port = json.loads(docker("inspect", api))[0]["NetworkSettings"]["Ports"]["8000/tcp"][0][
            "HostPort"
        ]
        async with httpx.AsyncClient(base_url="http://127.0.0.1:" + port) as http:
            for _ in range(150):
                try:
                    if (await http.get("/healthz")).status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                await asyncio.sleep(0.2)
            else:
                raise AssertionError("Beta API did not become healthy: " + docker("logs", api))

            runtime.settings = runtime.settings.model_copy(
                update={"docket_task_queue": queue, "seaweedfs_master_url": "http://127.0.0.1:9333"}
            )
            temporal = await Client.connect(runtime.settings.temporal_address)
            sandboxes, documents = {}, {}
            for name, marker in markers.items():
                sandboxes[name] = make_sandbox(engine)
                # Nothing in it names a unit, so it is held for review without a model call.
                content = uncompressed_pdf(["A note about nothing on file.", marker])
                documents[name] = await Ingress(runtime, temporal).submit(
                    content, "note.pdf", sandbox=sandboxes[name][0]
                )
            for name, document_id in documents.items():
                await wait_for(engine, document_id, "needs_review", worker)
                headers = {"Origin": BASE, "Cookie": sandbox_cookie(sandboxes[name][1])}
                dismissed = await http.post(
                    f"/review/{document_id}/dismiss",
                    json={"reason": f"Dismissed: {markers[name]}"},
                    headers=headers,
                )
                assert dismissed.status_code == 200, dismissed.text
                await wait_for(engine, document_id, "dismissed", worker)

            everywhere = set(markers)
            assert found(markers, pg_dump(engine)) == everywhere
            for name, (sandbox_id, _) in sandboxes.items():
                with Session(engine) as session:
                    digest = session.get(Document, uuid.UUID(documents[name])).content_hash
                assert markers[name].encode() in runtime.read(raw_key(sandbox_id, digest))
            assert found(markers, folder("seaweedfs")) == everywhere
            assert found(markers, folder("temporal")) == everywhere

            now = {"Origin": BASE, "Cookie": sandbox_cookie(sandboxes["now"][1])}
            # Delete now waits 3 seconds before the vacuum, which may take up to 30.
            deleted = await http.delete("/sandbox", headers=now, timeout=60)
            assert deleted.status_code == 200 and deleted.json() == {"deleted": True}
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE sandboxes SET expires_at = now() WHERE id = :id"),
                {"id": sandboxes["swept"][0]},
            )
        report = await sweep_once(runtime, temporal_connector(runtime.settings.temporal_address))
        assert report["complete"] is True, report

        assert found(markers, pg_dump(engine)) == {"kept"}
        assert found(markers, folder("seaweedfs")) == {"kept"}
        # Temporal deletes a history's rows in a task of its own, moments after the call.
        for _ in range(60):
            if found(markers, folder("temporal")) == {"kept"}:
                break
            await asyncio.sleep(0.5)
        assert found(markers, folder("temporal")) == {"kept"}
        kept_id, _ = sandboxes["kept"]
        with Session(engine) as session:
            digest = session.get(Document, uuid.UUID(documents["kept"])).content_hash
        assert markers["kept"].encode() in runtime.read(raw_key(kept_id, digest))

        logs = docker("compose", "logs", "--no-color") + "".join(
            container_logs(name) for name in names
        )
        assert found(markers, logs) == set()
    finally:
        for name in names:
            docker("rm", "-f", name)
