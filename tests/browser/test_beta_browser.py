"""S12: the beta stack in a real browser. Two browsers each get a private sandbox, one sends a
sample and reviews it, the countdown shows, and Delete now empties the page; with cookies
blocked, the page explains and sends nothing; Send waits for Delete now, a sandbox that is
gone leaves nothing on the page, and a refusal clears by itself (web/tests/beta.spec.ts).

The sandbox cookie is Secure, and Chromium drops Secure cookies from a plain-http host other
than localhost. The browser therefore shares the beta API container's network and opens
http://localhost:8000, which Chromium treats as a secure context."""

import asyncio
import json
import os
import secrets
import shutil
import subprocess
import uuid

import httpx
from sqlalchemy import text
from sqlalchemy.engine import make_url

from docket.sweep import record_complete


def docker(*args, env=None):
    # Values travel in env and the arguments carry only their names ("-e NAME"), so a
    # failure or timeout that prints the command shows no password or key.
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=120, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


async def test_playwright_beta_sandboxes_review_and_delete_now(runtime):
    shutil.rmtree("web/test-results", ignore_errors=True)
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
        "SEAWEEDFS_MASTER_URL": "http://seaweedfs:9333",
        "DOCKET_MODEL_MODE": "replay",
        # The v0.1 simulated answers are keyed to the model they stood in for.
        "DOCKET_MODEL_PROVIDER": "openrouter",
        "DOCKET_MODEL": "openai/gpt-4o-mini",
        "DOCKET_RUN_BUDGET_USD": "0.01",
        "DOCKET_ENV": "beta",
        "DOCKET_ADDRESS_KEY": secrets.token_hex(32),
        "DOCKET_TRUST_PROXY": "1",
        "DOCKET_BASE_URL": "http://localhost:8000",
        "DOCKET_DAILY_BUDGET_USD": "1.00",
        "DOCKET_REVIEW_TOKEN": "",
    }
    # Samples wait for a recent sweep, and no sweep service runs here.
    record_complete(runtime.engine)
    try:
        for service in ("worker", "api"):
            name = "docket-browser-beta-" + uuid.uuid4().hex
            args = ["compose", "run", "-d", "--no-deps", "--name", name]
            if service == "api":
                args += ["-p", "127.0.0.1::8000"]
            for key in environment:
                args += ["-e", key]
            docker(*args, service, env=os.environ | environment)
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
            assert (await client.get("/healthz")).json() == {"status": "ok"}
            assert (await client.get("/review")).json() == {"items": []}

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
                "DOCKET_ACCESS=sandbox",
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
        assert "3 passed" in result.stdout and "skipped" not in result.stdout, result.stdout
        print(result.stdout)
    finally:
        for name in names:
            docker("rm", "-f", name)
