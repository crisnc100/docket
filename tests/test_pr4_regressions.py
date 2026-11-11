import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from docket.evaluation import write_report
from tests.browser import test_review_browser as browser_test


def test_suggestion_report_states_the_actual_population(tmp_path):
    report = json.loads(Path("evals/reports/2026-11-11-seed-11.json").read_text())
    path = write_report(report, tmp_path)
    text = path.with_suffix(".md").read_text()
    assert "Documents that did not go to review are excluded." in text
    assert "Poison documents are excluded." not in text
    assert "Among 102 documents sent to review" in text


async def test_browser_failure_precedes_artifact_copy(monkeypatch):
    commands = []

    def docker(*args):
        commands.append(args)
        if args[0] == "inspect":
            return json.dumps([{"NetworkSettings": {"Ports": {"8000/tcp": [{"HostPort": "1"}]}}}])
        if args[0] == "cp":
            raise AssertionError("artifact directory missing")
        return "container"

    async def browser_run(*args, **kwargs):
        return subprocess.CompletedProcess([], 1, "Playwright failed", "browser assertion")

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
    with pytest.raises(AssertionError, match="Playwright failedbrowser assertion"):
        await browser_test.test_playwright_compose_replay_review_and_xss(runtime)
    assert not any(args[0] == "cp" for args in commands)
    assert sum(args[:2] == ("rm", "-f") for args in commands) == 3
