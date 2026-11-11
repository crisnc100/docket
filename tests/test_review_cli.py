import uuid

import httpx
from typer.testing import CliRunner

from docket.cli import app


def test_submit_and_status_commands_use_http_and_preserve_trace(tmp_path, monkeypatch):
    path = tmp_path / "sample.pdf"
    path.write_bytes(b"%PDF-test")
    document_id = str(uuid.uuid4())

    def post(url, *, files, timeout):
        assert url == "http://test/documents"
        assert files["file"][0] == "sample.pdf"
        assert files["file"][1].read() == b"%PDF-test"
        return httpx.Response(
            200, json={"document_id": document_id}, request=httpx.Request("POST", url)
        )

    def get(url, *, timeout):
        assert url == "http://test/documents/" + document_id
        return httpx.Response(
            200,
            json={"status": "received", "score": None, "events": []},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(httpx, "get", get)
    runner = CliRunner()
    submitted = runner.invoke(app, ["submit", str(path), "--api-url", "http://test/"])
    assert submitted.exit_code == 0 and document_id in submitted.output
    traced = runner.invoke(app, ["status", document_id, "--api-url", "http://test"])
    assert traced.exit_code == 0 and '"score": null' in traced.output
    assert runner.invoke(app, ["status", "invalid"]).exit_code == 1
    assert runner.invoke(app, ["submit", str(tmp_path / "missing")]).exit_code == 1


def test_cli_http_error_returns_nonzero(monkeypatch):
    def get(url, *, timeout):
        return httpx.Response(404, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)
    assert CliRunner().invoke(app, ["status", str(uuid.uuid4())]).exit_code == 1
