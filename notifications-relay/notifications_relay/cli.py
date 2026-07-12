from __future__ import annotations

import asyncio

import click
import uvicorn

from notifications_relay.client import RelayClient
from notifications_relay.config import RelayConfig
from notifications_relay.local_store import resolve_token, save_token
from notifications_relay.main import create_app


@click.group()
def cli() -> None:
    """Notifications relay CLI."""
    pass


@cli.command()
def serve() -> None:
    """Run the relay HTTP server."""
    config = RelayConfig.from_env()
    app = create_app(config)
    uvicorn.run(app, host=config.host, port=config.port)


@cli.command()
@click.option("--label", required=True, help="Label for the pairing.")
@click.option("--username", default=None, help="Optional username for the pairing.")
@click.option(
    "--config-path",
    default=None,
    help="Path to the local token store (default: .goblin/notifications.json).",
)
def pair(label: str, username: str | None, config_path: str | None) -> None:
    """Request a pairing token from the relay and save it locally."""
    relay_config = RelayConfig.from_env()
    base_url = f"http://{relay_config.host}:{relay_config.port}"

    async def _run() -> None:
        async with RelayClient(base_url, relay_config.api_key) as client:
            pairing = await client.create_pairing(label=label, username=username)
            save_token(label, pairing.token, path=config_path)
            click.echo(f"Pairing token for '{label}': {pairing.token}")
            click.echo("Send '/pair <token>' to the Telegram bot to complete pairing.")

    asyncio.run(_run())


@cli.command()
@click.option("--token", default=None, help="Pairing token to use.")
@click.option("--label", default=None, help="Label whose saved token should be used.")
@click.argument("text")
@click.option(
    "--question-id",
    default=None,
    help="Optional question ID to correlate replies.",
)
def send(
    token: str | None,
    label: str | None,
    text: str,
    question_id: str | None,
) -> None:
    """Send a message through the relay."""
    resolved = resolve_token(label=label, token=token)
    relay_config = RelayConfig.from_env()
    base_url = f"http://{relay_config.host}:{relay_config.port}"

    async def _run() -> None:
        async with RelayClient(base_url, relay_config.api_key) as client:
            response = await client.send(resolved, text, question_id=question_id)
            click.echo(f"Sent message {response.message_id} via {response.channel}")

    asyncio.run(_run())


@cli.command()
@click.option("--token", default=None, help="Pairing token to use.")
@click.option("--label", default=None, help="Label whose saved token should be used.")
@click.argument("text")
@click.option("--question-id", required=True, help="Question ID used to correlate the reply.")
@click.option("--timeout", default=300.0, help="Maximum time to wait for a reply.")
def ask(
    token: str | None,
    label: str | None,
    text: str,
    question_id: str,
    timeout: float,
) -> None:
    """Send a question and wait for a reply."""
    resolved = resolve_token(label=label, token=token)
    relay_config = RelayConfig.from_env()
    base_url = f"http://{relay_config.host}:{relay_config.port}"

    async def _run() -> None:
        async with RelayClient(base_url, relay_config.api_key) as client:
            reply = await client.ask(resolved, text, question_id, timeout=timeout)
            click.echo(reply.text)

    asyncio.run(_run())


@cli.command()
@click.option("--question-id", default=None, help="Filter replies by question ID.")
@click.option("--token", default=None, help="Filter replies by pairing token.")
def inbox(question_id: str | None, token: str | None) -> None:
    """Fetch and consume replies from the relay inbox."""
    if not question_id and not token:
        raise click.UsageError("Provide --question-id or --token")
    relay_config = RelayConfig.from_env()
    base_url = f"http://{relay_config.host}:{relay_config.port}"

    async def _run() -> None:
        async with RelayClient(base_url, relay_config.api_key) as client:
            messages = await client.get_inbox(question_id=question_id, token=token)
            for message in messages:
                click.echo(f"[{message.question_id or '-'}] {message.text}")

    asyncio.run(_run())


@cli.command(name="list")
def list_pairings() -> None:
    """List active pairings from the relay."""
    relay_config = RelayConfig.from_env()
    base_url = f"http://{relay_config.host}:{relay_config.port}"

    async def _run() -> None:
        async with RelayClient(base_url, relay_config.api_key) as client:
            pairings = await client.list_pairings()
            for pairing in pairings:
                label = pairing.label or "(no label)"
                click.echo(f"{pairing.token} {label} {pairing.telegram_user_id or '-'}")

    asyncio.run(_run())


@cli.command()
@click.option("--token", default=None, help="Pairing token to revoke.")
@click.option("--label", default=None, help="Label whose saved token should be revoked.")
@click.option(
    "--config-path",
    default=None,
    help="Path to the local token store (default: .goblin/notifications.json).",
)
def revoke(token: str | None, label: str | None, config_path: str | None) -> None:
    """Revoke a pairing on the relay."""
    resolved = resolve_token(label=label, token=token)
    relay_config = RelayConfig.from_env()
    base_url = f"http://{relay_config.host}:{relay_config.port}"

    async def _run() -> None:
        async with RelayClient(base_url, relay_config.api_key) as client:
            await client.revoke(resolved)
            click.echo(f"Revoked pairing {resolved}")

    asyncio.run(_run())


if __name__ == "__main__":
    cli()
