from __future__ import annotations

import click

# Re-export the relay's command group so goblin users get the same client
# surface without duplicating commands and drifting out of sync.
from notifications_relay.cli import (
    ask as relay_ask,
)
from notifications_relay.cli import (
    inbox as relay_inbox,
)
from notifications_relay.cli import (
    list_pairings as relay_list,
)
from notifications_relay.cli import (
    pair as relay_pair,
)
from notifications_relay.cli import (
    revoke as relay_revoke,
)
from notifications_relay.cli import (
    send as relay_send,
)


@click.group()
def notifications() -> None:
    """Manage Telegram notifications via the relay."""
    pass


notifications.add_command(relay_pair, name="pair")
notifications.add_command(relay_send, name="send")
notifications.add_command(relay_ask, name="ask")
notifications.add_command(relay_list, name="list")
notifications.add_command(relay_revoke, name="revoke")
notifications.add_command(relay_inbox, name="inbox")
