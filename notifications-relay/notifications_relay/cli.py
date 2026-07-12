import click


@click.group()
def cli() -> None:
    """Notifications relay CLI."""
    pass


@cli.command()
@click.argument("channel")
@click.argument("message")
def send(channel: str, message: str) -> None:
    """Send a notification to a channel."""
    click.echo(f"[{channel}] {message}")
