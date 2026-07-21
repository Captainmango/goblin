from __future__ import annotations

import asyncio

import click
from notifications_relay.local_store import save_token

from goblin.notifications.client import make_client, resolve


@click.group()
def notifications() -> None:
    """Manage Telegram notifications via the relay."""
    pass


@notifications.command()
@click.option("--label", required=True, help="Label for the pairing.")
@click.option("--username", default=None, help="Optional username for the pairing.")
def pair(label: str, username: str | None) -> None:
    """Request a pairing token and save it locally."""

    async def _run() -> None:
        async with make_client() as client:
            pairing = await client.create_pairing(label=label, username=username)
            save_token(label, pairing.token)
            click.echo(f"Pairing token for '{label}': {pairing.token}")
            click.echo("Send '/pair <token>' to the Telegram bot to complete pairing.")

    asyncio.run(_run())


@notifications.command()
@click.option("--label", required=True, help="Label used when pairing.")
@click.argument("text")
@click.option("--question-id", default=None, help="Optional question ID.")
def send(label: str, text: str, question_id: str | None) -> None:
    """Send a message to the paired operator."""
    token = resolve(label=label)

    async def _run() -> None:
        async with make_client() as client:
            response = await client.send(token, text, question_id=question_id)
            click.echo(f"Sent message {response.message_id} via {response.channel}")

    asyncio.run(_run())


@notifications.command()
@click.option("--label", required=True, help="Label used when pairing.")
@click.argument("text")
@click.option("--question-id", required=True, help="Question ID used to correlate the reply.")
@click.option("--timeout", default=300.0, help="Maximum time to wait for a reply.")
def ask(label: str, text: str, question_id: str, timeout: float) -> None:
    """Send a question and wait for a reply."""
    token = resolve(label=label)

    async def _run() -> None:
        async with make_client() as client:
            reply = await client.ask(token, text, question_id, timeout=timeout)
            click.echo(reply.text)

    asyncio.run(_run())


@notifications.command(name="list")
def list_pairings() -> None:
    """List active pairings from the relay."""

    async def _run() -> None:
        async with make_client() as client:
            pairings = await client.list_pairings()
            for pairing in pairings:
                label = pairing.label or "(no label)"
                click.echo(f"{pairing.token} {label} {pairing.telegram_user_id or '-'}")

    asyncio.run(_run())


@notifications.command()
@click.option("--label", required=True, help="Label whose pairing should be revoked.")
def revoke(label: str) -> None:
    """Revoke a pairing on the relay."""
    token = resolve(label=label)

    async def _run() -> None:
        async with make_client() as client:
            await client.revoke(token)
            click.echo(f"Revoked pairing for '{label}'")

    asyncio.run(_run())
