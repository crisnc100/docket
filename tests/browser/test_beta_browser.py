"""A7: the beta stack in a real browser. A visitor signs in with an invite code and reviews one
held document.

The session cookie is Secure, and Chromium drops Secure cookies from a plain-http host other
than localhost. The browser therefore shares the beta API container's network and opens
http://localhost:8000, which Chromium treats as a secure context."""

import asyncio
import json
import secrets
import shutil
import subprocess
import uuid
from pathlib import Path

import httpx
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from temporalio.client import Client

from docket.db import Document
from docket.ingress import Ingress

DOCUMENT = "doc-0075-decoy_near_miss_address.pdf"


def docker(*args):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


async def test_playwright_beta_invite_sign_in_and_review(runtime):
    shutil.rmtree("web/test-results", ignore_errors=True)
    code = "invite-" + secrets.token_hex(16)
    queue = "browser-beta-" + uuid.uuid4().hex
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
        "DOCKET_MODEL_MODE": "replay",
        # The v0.1 simulated answers are keyed to the model they stood in for.
        "DOCKET_MODEL_PROVIDER": "openrouter",
        "DOCKET_MODEL": "openai/gpt-4o-mini",
        "DOCKET_RUN_BUDGET_USD": "0.01",
        "DOCKET_ENV": "beta",
        "DOCKET_INVITE_CODES": code,
        "DOCKET_SESSION_KEY": secrets.token_hex(32),
        "DOCKET_TRUST_PROXY": "1",
        "DOCKET_BASE_URL": "http://localhost:8000",
        "DOCKET_DAILY_BUDGET_USD": "1.00",
        "DOCKET_REVIEW_TOKEN": "",
    }
    try:
        for service in ("worker", "api"):
            name = "docket-browser-beta-" + uuid.uuid4().hex
            args = ["compose", "run", "-d", "--no-deps", "--name", name]
            if service == "api":
                args += ["-p", "127.0.0.1::8000"]
            for key, value in environment.items():
                args += ["-e", f"{key}={value}"]
            docker(*args, service)
            names.append(name)
        api = names[-1]
        api_port = json.loads(docker("inspect", api))[0]["NetworkSettings"]["Ports"]["8000/tcp"][0][
            "HostPort"
        ]
        async with httpx.AsyncClient(base_url="http://127.0.0.1:" + api_port) as client:
            for _ in range(150):
                try:
                    if (await client.get("/healthz")).status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                await asyncio.sleep(0.2)
            else:
                raise AssertionError("Beta API did not become healthy: " + docker("logs", api))
            assert (await client.get("/review")).status_code == 401

        # Visitors cannot send documents to the beta, so the held document goes in through
        # ingress directly, on the beta worker's queue and model.
        runtime.settings = runtime.settings.model_copy(
            update={
                "docket_task_queue": queue,
                "docket_model": environment["DOCKET_MODEL"],
                "docket_run_budget_usd": 0.01,
            }
        )
        temporal = await Client.connect(runtime.settings.temporal_address)
        content = Path("corpus/seed-11", DOCUMENT).read_bytes()
        document_id = await Ingress(runtime, temporal).submit(
            content, DOCUMENT, sender="reviewer@example.test"
        )
        # The worker container extracts and replays the model answer, so allow a minute.
        for _ in range(300):
            with Session(runtime.engine) as session:
                status = session.get(Document, uuid.UUID(document_id)).status
            if status == "needs_review":
                break
            await asyncio.sleep(0.2)
        else:
            raise AssertionError(f"Document stayed {status}: " + docker("logs", names[0]))

        browser = "docket-browser-beta-test-" + uuid.uuid4().hex
        names.append(browser)
        result = await asyncio.to_thread(
            subprocess.run,
            [
                "docker",
                "run",
                "--name",
                browser,
                "--init",
                "--shm-size",
                "256m",
                "--network",
                "container:" + api,
                "-e",
                "DOCKET_BROWSER_URL=http://localhost:8000",
                "-e",
                "DOCKET_ACCESS=invite",
                "-e",
                "DOCKET_INVITE_CODE=" + code,
                "-e",
                "DOCKET_DOCUMENT_NAME=" + DOCUMENT,
                "docket-v01-browser",
            ],
            capture_output=True,
            text=True,
            timeout=270,
        )
        copy_error = ""
        try:
            docker("cp", browser + ":/web/test-results", "web/")
        except (AssertionError, subprocess.SubprocessError, OSError) as exc:
            copy_error = str(exc)
        assert result.returncode == 0, result.stdout + result.stderr + copy_error
        assert not copy_error, copy_error
        assert "1 passed" in result.stdout and "skipped" not in result.stdout, result.stdout
        print(result.stdout)
    finally:
        for name in names:
            docker("rm", "-f", name)
