import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from docket.evaluation import write_report
from tests.browser import test_review_browser as browser_test


def test_suggestion_report_states_the_actual_population(tmp_path):
    report = json.loads(Path("evals/history/simulated/reports/2026-11-11-seed-11.json").read_text())
    path = write_report(report, tmp_path)
    text = path.with_suffix(".md").read_text()
    assert "Documents that did not go to review are excluded." in text
    assert "Poison documents are excluded." not in text
    assert "Among 102 documents sent to review" in text


@pytest.mark.parametrize(
    "returncode,copy_fails,expected",
    [
        (1, True, "Playwright failedbrowser assertionartifact directory missing"),
        (1, False, "Playwright failedbrowser assertion"),
        (0, True, "artifact directory missing"),
        (0, False, None),
    ],
)
async def test_browser_failure_preserves_artifacts_and_primary_error(
    monkeypatch, returncode, copy_fails, expected
):
    commands = []

    def docker(*args):
        commands.append(args)
        if args[0] == "inspect":
            return json.dumps([{"NetworkSettings": {"Ports": {"8000/tcp": [{"HostPort": "1"}]}}}])
        if args[0] == "cp" and copy_fails:
            raise AssertionError("artifact directory missing")
        return "container"

    async def browser_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            [], returncode, "Playwright failed" if returncode else "11 passed", "browser assertion"
        )

    class HealthyClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, path):
            return SimpleNamespace(status_code=200)

    runtime = MagicMock()
    connection = runtime.engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.scalar_one.return_value = "test_browser"
    runtime.settings.database_url = (
        "postgresql://user:password@localhost/test"  # pragma: allowlist secret
    )
    monkeypatch.setattr(browser_test, "docker", docker)
    monkeypatch.setattr(browser_test.asyncio, "to_thread", browser_run)
    monkeypatch.setattr(browser_test.httpx, "AsyncClient", HealthyClient)
    monkeypatch.setattr(browser_test.shutil, "rmtree", lambda *args, **kwargs: None)
    if expected:
        with pytest.raises(AssertionError, match=expected):
            await browser_test.test_playwright_compose_replay_review_and_xss(runtime)
    else:
        await browser_test.test_playwright_compose_replay_review_and_xss(runtime)
    assert sum(args[0] == "cp" for args in commands) == 1
    assert sum(args[:2] == ("rm", "-f") for args in commands) == 3
