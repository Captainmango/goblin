#!/usr/bin/env python3
"""Validate checked-in VCR cassettes for leaked secrets.

Decode every ``*.b64`` cassette in the repository and reject any cassette that
contains non-redacted sensitive headers, query parameters, body fields, or
values that look like API keys or tokens.

Run from the repo root via:

    task goblin:check-cassettes

"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from goblin_common.testing.vcr import (
    DEFAULT_SENSITIVE_BODY_FIELDS,
    DEFAULT_SENSITIVE_HEADERS,
    DEFAULT_SENSITIVE_QUERY_PARAMS,
    Base64YamlSerializer,
)

CASSETTE_EXTENSION = ".b64"
REDACTED = "<REDACTED>"

# Common credential patterns.  These are deliberately narrow to avoid flagging
# hashes, ETags, request IDs, and URLs.
_CREDENTIAL_PATTERNS = [
    re.compile(r"\bsk-[a-zA-Z0-9]{20,}\b"),
    re.compile(r"\bBearer\s+[a-zA-Z0-9_\-]{20,}\b", re.IGNORECASE),
    re.compile(r"\bBasic\s+[a-zA-Z0-9+/]{20,}=*=\b", re.IGNORECASE),
]


def find_cassettes(root: Path) -> list[Path]:
    """Return all cassette files under ``root``, sorted."""
    return sorted(path for path in root.rglob(f"*{CASSETTE_EXTENSION}") if path.is_file())


def load_cassette(path: Path) -> dict:
    """Decode a base64-encoded, gzip-compressed YAML cassette."""
    serializer = Base64YamlSerializer()
    return serializer.deserialize(path.read_text(encoding="ascii"))


def _contains_credential(value: str) -> bool:
    """Return ``True`` if ``value`` matches a known credential pattern."""
    return any(pattern.search(value) for pattern in _CREDENTIAL_PATTERNS)


def check_headers(interaction: dict, path: Path, errors: list[str]) -> None:
    """Verify sensitive headers are redacted."""
    lowered_sensitive = {name.lower() for name in DEFAULT_SENSITIVE_HEADERS}

    for section in ("request", "response"):
        source = interaction.get(section, {})
        headers = source.get("headers", {})
        for name, values in headers.items():
            for value in values:
                if name.lower() in lowered_sensitive:
                    if value != REDACTED:
                        errors.append(
                            f"{path}: {section} header '{name}' is not redacted: {value!r}"
                        )
                elif _contains_credential(value):
                    errors.append(
                        f"{path}: {section} header '{name}' looks like a credential: {value!r}"
                    )


def check_query_params(interaction: dict, path: Path, errors: list[str]) -> None:
    """Verify sensitive query parameters are redacted."""
    request = interaction.get("request", {})
    uri = request.get("uri", "")
    parsed = urlparse(uri)
    if not parsed.query:
        return

    lowered_sensitive = {name.lower() for name in DEFAULT_SENSITIVE_QUERY_PARAMS}
    query = parse_qs(parsed.query, keep_blank_values=True)
    for name, values in query.items():
        for value in values:
            if name.lower() in lowered_sensitive and value != REDACTED:
                errors.append(
                    f"{path}: query parameter '{name}' is not redacted: {value!r}"
                )
            elif _contains_credential(value):
                errors.append(
                    f"{path}: query parameter '{name}' looks like a credential: {value!r}"
                )


def _check_json_body(
    data: object,
    lowered_sensitive: set[str],
    path: Path,
    section: str,
    errors: list[str],
) -> None:
    """Recursively check JSON body fields for leaked secrets."""
    if isinstance(data, dict):
        for key, value in data.items():
            if key.lower() in lowered_sensitive:
                if value != REDACTED:
                    errors.append(
                        f"{path}: {section} body field '{key}' is not redacted: {value!r}"
                    )
            elif isinstance(value, str) and _contains_credential(value):
                errors.append(
                    f"{path}: {section} body field '{key}' looks like a credential: {value!r}"
                )
            elif isinstance(value, (dict, list)):
                _check_json_body(value, lowered_sensitive, path, section, errors)
    elif isinstance(data, list):
        for item in data:
            _check_json_body(item, lowered_sensitive, path, section, errors)


def _check_form_body(
    body: str,
    lowered_sensitive: set[str],
    path: Path,
    section: str,
    errors: list[str],
) -> None:
    """Check form-encoded body fields for leaked secrets."""
    parsed = parse_qs(body, keep_blank_values=True)
    for name, values in parsed.items():
        for value in values:
            if name.lower() in lowered_sensitive and value != REDACTED:
                errors.append(
                    f"{path}: {section} form field '{name}' is not redacted: {value!r}"
                )
            elif _contains_credential(value):
                errors.append(
                    f"{path}: {section} form field '{name}' looks like a credential: {value!r}"
                )


def _extract_body(source: dict) -> str | None:
    """Return the body of a request/response as a string, if present."""
    body = source.get("body")
    if body is None:
        return None
    if isinstance(body, dict):
        body = body.get("string", "")
    if isinstance(body, bytes):
        try:
            body = body.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(body, str):
        return None
    return body


def check_body(interaction: dict, path: Path, errors: list[str]) -> None:
    """Verify request and response bodies contain no leaked secrets."""
    lowered_sensitive = {name.lower() for name in DEFAULT_SENSITIVE_BODY_FIELDS}

    for section in ("request", "response"):
        source = interaction.get(section, {})
        body = _extract_body(source)
        if body is None:
            continue

        try:
            data = json.loads(body)
            _check_json_body(data, lowered_sensitive, path, section, errors)
        except json.JSONDecodeError:
            if "application/x-www-form-urlencoded" in _get_content_type(source):
                _check_form_body(body, lowered_sensitive, path, section, errors)


def _get_content_type(source: dict) -> str:
    """Return the Content-Type header value, if present."""
    content_type = source.get("headers", {}).get("Content-Type", [""])
    return content_type[0] if isinstance(content_type, list) else str(content_type)


def main() -> int:
    """Find, decode, and validate all cassettes under the repo root."""
    root = Path(__file__).resolve().parent.parent
    cassettes = find_cassettes(root)

    if not cassettes:
        print("No cassettes found.")
        return 0

    errors: list[str] = []
    for cassette_path in cassettes:
        try:
            cassette = load_cassette(cassette_path)
        except Exception as exc:
            errors.append(f"{cassette_path}: could not decode cassette: {exc}")
            continue

        for interaction in cassette.get("interactions", []):
            check_headers(interaction, cassette_path, errors)
            check_query_params(interaction, cassette_path, errors)
            check_body(interaction, cassette_path, errors)

    if errors:
        print(f"FAIL: {len(errors)} issue(s) found in {len(cassettes)} cassette(s):")
        for error in errors:
            print(f"  - {error}")
        return 1

    print(f"OK: {len(cassettes)} cassette(s) checked, no secrets found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
