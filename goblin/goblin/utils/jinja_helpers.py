from __future__ import annotations

import json


def filter_contains(messages: list, text: str) -> bool:
    """Jinja2 filter: True if any message in the list contains the substring."""
    for msg in messages:
        if isinstance(msg, str) and text in msg:
            return True
    return False


def filter_json_value(messages: list, key: str):
    """Jinja2 filter: parse message strings as JSON dicts and extract a value by dot-path.

    Iterates through messages, attempts JSON parsing, and returns the value at
    the dot-separated key path from the first message that contains it.
    Returns None if no message has the key.
    """
    parts = key.split(".")
    for msg in messages:
        if not isinstance(msg, str):
            continue
        try:
            data = json.loads(msg)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        obj = data
        for part in parts:
            if isinstance(obj, dict) and part in obj:
                obj = obj[part]
            else:
                obj = None
                break
        if obj is not None:
            return obj
    return None
