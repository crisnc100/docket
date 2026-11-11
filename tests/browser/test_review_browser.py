import asyncio
import json
import shutil
import subprocess
import uuid

import httpx
from sqlalchemy import text
from sqlalchemy.engine import make_url


def docker(*args):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


async def test_playwright_compose_replay_review_and_xss(runtime):
    shutil.rmtree("web/test-results", ignore_errors=True)
    names = []
    with runtime.engine.connect() as connection:
        schema = connection.execute(text("select current_schema()")).scalar_one()
    url = make_url(runtime.settings.database_url).set(host="postgres", port=5432)
    environment = {
        "DATABASE_URL": url.update_query_dict(
            {"options": "-csearch_path=" + schema}
        ).render_as_string(hide_password=False),
        "DOCKET_TASK_QUEUE": "browser-" + uuid.uuid4().hex,
        "S3_BUCKET": runtime.settings.s3_bucket,
        "DOCKET_MODEL_MODE": "replay",
        "DOCKET_REVIEW_TOKEN": runtime.settings.docket_review_token.get_secret_value(),
        "DOCKET_RUN_BUDGET_USD": "0.01",
    }
    try:
        for service in ("worker", "api"):
            name = "docket-browser-" + uuid.uuid4().hex
            args = ["compose", "run", "-d", "--no-deps", "--name", name]
            if service == "api":
                args += ["-p", "127.0.0.1::8000"]
            for key, value in environment.items():
                args += ["-e", f"{key}={value}"]
            docker(*args, service)
            names.append(name)
        api_port = json.loads(docker("inspect", names[-1]))[0]["NetworkSettings"]["Ports"][
            "8000/tcp"
        ][0]["HostPort"]
        base_url = "http://127.0.0.1:" + api_port
        async with httpx.AsyncClient(base_url=base_url) as client:
            for _ in range(150):
                try:
                    if (await client.get("/healthz")).status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                await asyncio.sleep(0.2)
            else:
                raise AssertionError("Browser API did not become healthy")
        browser = "docket-browser-test-" + uuid.uuid4().hex
        names.append(browser)
        result = await asyncio.to_thread(
            subprocess.run,
            [
                "docker",
                "compose",
                "run",
                "--no-deps",
                "--name",
                browser,
                "-e",
                "DOCKET_BROWSER_URL=http://" + names[-2] + ":8000",
                "-e",
                "DOCKET_REVIEW_TOKEN=" + runtime.settings.docket_review_token.get_secret_value(),
                "browser",
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
        assert "11 passed" in result.stdout and "skipped" not in result.stdout, result.stdout
        print(result.stdout)
    finally:
        for name in names:
            docker("rm", "-f", name)
