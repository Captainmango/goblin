from __future__ import annotations


def is_allowed(user_id: int, allowed_user_ids: set[int]) -> bool:
    """Return True if the user is allowed to interact with the bot.

    An empty allow-list permits all users.
    """
    return not allowed_user_ids or user_id in allowed_user_ids
