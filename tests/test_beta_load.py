"""The load run's own safety checks (PR C review f1 and f2). No Docker is started here."""

import subprocess

import pytest

from scripts.load import beta_load


@pytest.fixture(autouse=True)
def no_shell_settings(monkeypatch):
    # The suite blanks the model keys in the environment, and a blank shell variable still
    # overrides the env file for compose.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


# Fake values only: one shaped like a real key, one the stub accepts.
REAL_LOOKING_KEY = {"ANTHROPIC_API_KEY": "sk-ant-api03-x"}  # pragma: allowlist secret
STUB_KEY = {"ANTHROPIC_API_KEY": "stub-key"}  # pragma: allowlist secret
GOOD = {
    "samples_planned": 60,
    "samples_accepted": 60,
    "samples_refused": {},
    "sample_outcomes": {"before reset http 404": 31, "filed": 10, "needs_review": 19},
    "reset_exit": 0,
    "reset": "Beta reset: {'terminated': 28, 'seeded': {'needs_review': 12}}",
    "stub_requests": 58,
    "stats_failures": 0,
    "peaks": {name: {"memory_mb": 100.0, "limit_mb": 256.0} for name in beta_load.SERVICES},
}


def test_a_complete_run_passes():
    assert beta_load.problems(GOOD) == []


def test_refusals_a_failed_reset_and_missing_stats_each_fail_the_run():
    broken = GOOD | {
        "samples_accepted": 0,
        "samples_refused": {"429": 60},
        "sample_outcomes": {},
        "reset_exit": 1,
        "reset": "",
        "stats_failures": 3,
        "peaks": {},
    }
    found = beta_load.problems(broken)
    assert "samples refused: {'429': 60}" in found
    assert "the reset did not seed its 12 held documents" in found
    assert "docker stats failed 3 times" in found
    assert any(problem.startswith("no stats for api") for problem in found)


@pytest.mark.parametrize(
    ("outcomes", "problem"),
    [
        ({"failed": 2}, "2 samples ended failed"),
        ({"processing": 1}, "1 samples ended processing"),
        # A sample sent after the reset must still exist.
        ({"http 404": 1}, "1 samples ended http 404"),
    ],
)
def test_a_sample_that_did_not_settle_fails_the_run(outcomes, problem):
    assert problem in beta_load.problems(GOOD | {"sample_outcomes": outcomes})


def test_no_stub_calls_or_a_peak_at_the_limit_fails_the_run():
    peaks = GOOD["peaks"] | {"reset": {"memory_mb": 256.0, "limit_mb": 256.0}}
    found = beta_load.problems(GOOD | {"stub_requests": 0, "peaks": peaks})
    assert "the stub answered no model call" in found
    assert "reset reached its memory limit" in found


def answers(base_url: str, stub_id: str = "msg_stub"):
    def compose(env_file, *args):
        output = base_url if "ANTHROPIC_BASE_URL" in args[-1] else stub_id
        return subprocess.CompletedProcess(args, 0, output + "\n", "")

    return compose


def test_the_run_refuses_a_real_key_before_asking_the_worker(monkeypatch, tmp_path):
    asked = []
    monkeypatch.setattr(beta_load, "compose", lambda *args: asked.append(args))
    with pytest.raises(beta_load.LoadRefused, match="real Anthropic key"):
        beta_load.confirm_stub(tmp_path, REAL_LOOKING_KEY)
    assert asked == []


@pytest.mark.parametrize(
    ("base_url", "stub_id", "message"),
    [
        ("", "msg_stub", "does not point at the stub"),
        ("https://api.anthropic.com", "msg_stub", "does not point at the stub"),
        ("http://stub:8080", "msg_real", "did not answer"),
    ],
)
def test_the_run_refuses_unless_the_worker_reaches_the_stub(
    monkeypatch, tmp_path, base_url, stub_id, message
):
    monkeypatch.setattr(beta_load, "compose", answers(base_url, stub_id))
    with pytest.raises(beta_load.LoadRefused, match=message):
        beta_load.confirm_stub(tmp_path, STUB_KEY)
    monkeypatch.setattr(beta_load, "compose", answers("http://stub:8080"))
    beta_load.confirm_stub(tmp_path, STUB_KEY)


def test_every_compose_command_names_the_load_project(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        beta_load.subprocess, "run", lambda command, **kwargs: calls.append(command)
    )
    beta_load.compose(tmp_path / ".env", "down", "-v")
    [command] = calls
    assert command[:4] == ["docker", "compose", "-p", "docket-load"]
    assert "scripts/load/compose.load.yaml" in command


def test_the_run_refuses_settings_the_shell_would_override(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-the-shell")
    monkeypatch.setattr(beta_load, "compose", answers("http://stub:8080"))
    with pytest.raises(beta_load.LoadRefused, match="ANTHROPIC_API_KEY"):
        beta_load.confirm_stub(tmp_path, STUB_KEY)
