"""Simple text utilities shared across Goblin projects."""


def slugify(value: str) -> str:
    """Convert a string to a lowercase, hyphenated slug."""
    return "-".join(value.lower().split())
