from click.testing import CliRunner

from notifications_relay.cli import cli


def test_send() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["send", "slack", "hello"])
    assert result.exit_code == 0
    assert "[slack] hello" in result.output
