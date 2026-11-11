import uuid

import httpx
import pytest
from typer.testing import CliRunner

from docket.cli import app


@pytest.mark.parametrize("command", ["submit", "status"])
def test_cli_refuses_missing_token_without_making_http_request(command, tmp_path, monkeypatch):
    monkeypatch.delenv("DOCKET_REVIEW_TOKEN", raising=False)
    path = tmp_path / "review.pdf"
    path.write_bytes(b"%PDF-test")

    def unexpected(*args, **kwargs):
        raise AssertionError("Missing token must not reach HTTP")

    monkeypatch.setattr(httpx, "post", unexpected)
    monkeypatch.setattr(httpx, "get", unexpected)
    result = CliRunner().invoke(
        app, [command, str(path) if command == "submit" else str(uuid.uuid4())]
    )
    assert result.exit_code == 1
    assert "DOCKET_REVIEW_TOKEN" in result.output
