from __future__ import annotations

from click.testing import CliRunner

from notifications_relay.cli import cli


def test_cli_help() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "serve" in result.output
    assert "pair" in result.output
