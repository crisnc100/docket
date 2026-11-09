from typer.testing import CliRunner

from docket.cli import app


def test_cli_version() -> None:
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout == "docket 0.1.0\n"
