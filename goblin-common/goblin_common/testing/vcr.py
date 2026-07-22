"""Centralised VCR cassette helpers for the Goblin monorepo.

This module provides a single, shared place for:

* Serialising cassettes as gzip-compressed, base64-encoded YAML.
* Deserialising those cassettes during replay.
* Scrubbing API keys, tokens, and other sensitive values before a cassette is
  written.

Tests should use :func:`use_cassette` (or the ``cassette`` fixture) instead of
configuring ``vcr.use_cassette`` directly.
"""

from __future__ import annotations

import base64
import gzip
import json
import re
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import pytest
import vcr
import yaml

_CassetteContext = AbstractContextManager[vcr.cassette.Cassette]

_REDACTED = "<REDACTED>"

DEFAULT_SENSITIVE_HEADERS = [
    "authorization",
    "api-key",
    "x-api-key",
    "x-litellm-api-key",
    "x-openai-api-key",
]

DEFAULT_SENSITIVE_QUERY_PARAMS = ["api_key", "apikey", "key", "token", "secret"]
DEFAULT_SENSITIVE_BODY_FIELDS = [
    "api_key",
    "apikey",
    "key",
    "token",
    "secret",
    "password",
    "access_token",
]

API_KEYISH_RE = re.compile(r"\b[a-zA-Z0-9_\-]{32,}\b|\b[a-f0-9]{32,}\b|\b[A-Za-z0-9+/]{40,}={0,2}\b")


class Base64YamlSerializer:
    """Read and write cassettes as gzip-compressed, base64-encoded YAML."""

    def serialize(self, cassette_dict: dict[str, Any]) -> str:
        """Encode ``cassette_dict`` as an ASCII string."""
        yaml_text = yaml.safe_dump(
            cassette_dict,
            default_flow_style=False,
            allow_unicode=True,
        )
        compressed = gzip.compress(yaml_text.encode("utf-8"), compresslevel=9)
        return base64.b64encode(compressed).decode("ascii")

    def deserialize(self, cassette_string: str) -> dict[str, Any]:
        """Decode a cassette dict from ``cassette_string``."""
        compressed = base64.b64decode(cassette_string)
        yaml_bytes = gzip.decompress(compressed)
        return yaml.safe_load(yaml_bytes)


vcr.default_vcr.register_serializer("base64_yaml", Base64YamlSerializer())


def _patch_aiohttp_record_response() -> None:
    """Reset aiohttp response streams after VCR records them.

    vcrpy's aiohttp stub consumes the response body when recording.  litellm
    wraps the same aiohttp response in ``AiohttpResponseStream`` and iterates
    the (now empty) stream, so downstream code sees an empty body and fails to
    parse JSON.  After vcrpy records the body we create a fresh ``MockStream``
    seeded with the captured bytes so the real caller can still read it.
    """
    try:
        from vcr.stubs import aiohttp_stubs
    except ImportError:
        return

    original = aiohttp_stubs.record_response

    async def _record_response_with_reset(
        cassette: Any,
        vcr_request: Any,
        response: Any,
    ) -> None:
        await original(cassette, vcr_request, response)

        body = getattr(response, "_body", None)
        if body is None:
            return

        new_stream = aiohttp_stubs.MockStream()
        new_stream.feed_data(body)
        new_stream.feed_eof()
        response.content = new_stream

    aiohttp_stubs.record_response = _record_response_with_reset


_patch_aiohttp_record_response()


def _scrub_headers(headers: Any, sensitive_headers: list[str]) -> dict[str, list[Any]]:
    """Redact values for headers whose names match ``sensitive_headers``."""
    result: dict[str, list[Any]] = {}
    if not sensitive_headers:
        if isinstance(headers, Mapping):
            for name, value in headers.items():
                result[name] = list(value) if isinstance(value, (list, tuple)) else [value]
            return result
        for name, value in headers:
            result.setdefault(name, []).append(value)
        return result

    lowered = {name.lower() for name in sensitive_headers}

    if isinstance(headers, Mapping):
        for name, value in headers.items():
            if isinstance(value, (list, tuple)):
                result[name] = [_REDACTED if name.lower() in lowered else v for v in value]
            else:
                result[name] = [_REDACTED if name.lower() in lowered else value]
    else:
        for name, value in headers:
            scrubbed = _REDACTED if name.lower() in lowered else value
            result.setdefault(name, []).append(scrubbed)

    return result


def _scrub_uri(uri: str, sensitive_query_params: list[str]) -> str:
    """Redact values for query parameters matching ``sensitive_query_params``."""
    if not sensitive_query_params:
        return uri

    lowered = {name.lower() for name in sensitive_query_params}
    parsed = urlparse(uri)
    if not parsed.query:
        return uri

    query = parse_qs(parsed.query, keep_blank_values=True)
    scrubbed = {
        name: [_REDACTED if name.lower() in lowered else value for value in vals] for name, vals in query.items()
    }
    new_query = urlencode(scrubbed, doseq=True, safe="<>")
    return urlunparse(parsed._replace(query=new_query))


def _scrub_json_value(value: Any, sensitive_fields: set[str]) -> Any:
    """Recursively redact values for keys matching ``sensitive_fields``."""
    if isinstance(value, dict):
        return {
            key: _REDACTED if key.lower() in sensitive_fields else _scrub_json_value(val, sensitive_fields)
            for key, val in value.items()
        }
    if isinstance(value, list):
        return [_scrub_json_value(item, sensitive_fields) for item in value]
    return value


def _scrub_form_body(body: bytes, sensitive_fields: set[str]) -> bytes:
    """Redact values in an ``application/x-www-form-urlencoded`` body."""
    text = body.decode("utf-8")
    parsed = parse_qs(text, keep_blank_values=True)
    scrubbed = {
        name: [_REDACTED if name.lower() in sensitive_fields else value for value in vals]
        for name, vals in parsed.items()
    }
    return urlencode(scrubbed, doseq=True, safe="<>").encode("utf-8")


def _read_body(body: bytes | str | BytesIO) -> bytes | str:
    """Normalise a vcrpy body value to ``bytes`` or ``str``."""
    if isinstance(body, BytesIO):
        return body.read()
    return body


def _scrub_body(
    body: bytes | str | BytesIO | None,
    sensitive_body_fields: list[str],
) -> bytes | str | None:
    """Redact sensitive fields inside a request or response body."""
    if body is None:
        return body

    body = _read_body(body)

    if isinstance(body, str):
        try:
            data = json.loads(body)
            lowered = {name.lower() for name in sensitive_body_fields}
            return json.dumps(_scrub_json_value(data, lowered))
        except json.JSONDecodeError:
            return body

    if not isinstance(body, bytes):
        return body

    lowered = {name.lower() for name in sensitive_body_fields}

    try:
        data = json.loads(body.decode("utf-8"))
        return json.dumps(_scrub_json_value(data, lowered)).encode("utf-8")
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass

    try:
        return _scrub_form_body(body, lowered)
    except (UnicodeDecodeError, ValueError):
        pass

    return body


def _scrub_text(text: str, patterns: list[re.Pattern[str]]) -> str:
    """Apply every compiled regex in ``patterns`` to ``text``."""
    for pattern in patterns:
        text = pattern.sub(_REDACTED, text)
    return text


def _apply_patterns_to_headers(headers: Any, patterns: list[re.Pattern[str]]) -> dict[str, list[Any]]:
    """Apply regex patterns to header values."""
    result: dict[str, list[Any]] = {}

    if isinstance(headers, Mapping):
        for name, value in headers.items():
            if isinstance(value, (list, tuple)):
                result[name] = [_scrub_text(str(v), patterns) for v in value]
            else:
                result[name] = [_scrub_text(str(value), patterns)]
    else:
        for name, value in headers:
            result.setdefault(name, []).append(_scrub_text(str(value), patterns))

    return result


def _request_body_is_form_encoded(request: vcr.request.Request) -> bool:
    """Return ``True`` when the request claims form-encoded content."""
    content_type = request.headers.get("Content-Type", [""])
    if isinstance(content_type, list):
        content_type = content_type[0] if content_type else ""
    return "application/x-www-form-urlencoded" in content_type


def make_scrubber(
    sensitive_headers: list[str] | None = None,
    sensitive_query_params: list[str] | None = None,
    sensitive_body_fields: list[str] | None = None,
    extra_patterns: list[str] | None = None,
) -> tuple[Callable[[vcr.request.Request], vcr.request.Request], Callable[[Any], Any]]:
    """Return ``(before_record_request, before_record_response)`` for vcrpy.

    The callbacks redact:

    * Matching headers (case-insensitive).
    * Matching query parameters from the request URI.
    * Matching JSON/form fields anywhere in request and response bodies.
    * Optional regex patterns in headers, URIs, and bodies.

    Redacted values are replaced with ``<REDACTED>`` so cassettes remain stable
    across re-recordings.
    """
    sensitive_headers = list(sensitive_headers or DEFAULT_SENSITIVE_HEADERS)
    sensitive_query_params = list(sensitive_query_params or DEFAULT_SENSITIVE_QUERY_PARAMS)
    sensitive_body_fields = list(sensitive_body_fields or DEFAULT_SENSITIVE_BODY_FIELDS)
    compiled_patterns = [re.compile(pattern) for pattern in (extra_patterns or [])]

    def before_record_request(request: vcr.request.Request) -> vcr.request.Request:
        request.headers = _scrub_headers(request.headers, sensitive_headers)
        request.uri = _scrub_uri(request.uri, sensitive_query_params)

        if compiled_patterns:
            request.headers = _apply_patterns_to_headers(request.headers, compiled_patterns)
            request.uri = _scrub_text(request.uri, compiled_patterns)

        if request.body is not None:
            body = _read_body(request.body)
            if _request_body_is_form_encoded(request):
                lowered = {name.lower() for name in sensitive_body_fields}
                try:
                    body_bytes = body if isinstance(body, bytes) else body.encode("utf-8")
                    body = _scrub_form_body(body_bytes, lowered)
                except (UnicodeDecodeError, ValueError):
                    body = _scrub_body(body, sensitive_body_fields)
            else:
                body = _scrub_body(body, sensitive_body_fields)

            if compiled_patterns:
                if isinstance(body, bytes):
                    try:
                        body = _scrub_text(body.decode("utf-8"), compiled_patterns).encode("utf-8")
                    except UnicodeDecodeError:
                        pass
                elif isinstance(body, str):
                    body = _scrub_text(body, compiled_patterns)

            request.body = body

        return request

    def before_record_response(response: Any) -> Any:
        if hasattr(response, "headers"):
            response.headers = _scrub_headers(response.headers, sensitive_headers)
            if compiled_patterns:
                response.headers = _apply_patterns_to_headers(response.headers, compiled_patterns)

        if hasattr(response, "body"):
            response.body = _scrub_body(response.body, sensitive_body_fields)
            if compiled_patterns and response.body is not None:
                if isinstance(response.body, bytes):
                    try:
                        response.body = _scrub_text(response.body.decode("utf-8"), compiled_patterns).encode("utf-8")
                    except UnicodeDecodeError:
                        pass
                elif isinstance(response.body, str):
                    response.body = _scrub_text(response.body, compiled_patterns)

        return response

    return before_record_request, before_record_response


@contextmanager
def use_cassette(
    path: str | Path,
    *,
    record_mode: str = "once",
    decode_compressed_response: bool = True,
    serializer: str = "base64_yaml",
    **kwargs: Any,
) -> Iterator[vcr.cassette.Cassette]:
    """Open a scrubbed, base64-encoded VCR cassette.

    This is a thin wrapper around ``vcr.use_cassette`` that applies the
    project's default scrubbing rules and serializer. Tests should use it
    instead of configuring ``vcr.use_cassette`` directly.
    """
    before_record_request, before_record_response = make_scrubber()

    with vcr.use_cassette(
        str(path),
        record_mode=record_mode,
        decode_compressed_response=decode_compressed_response,
        serializer=serializer,
        before_record_request=before_record_request,
        before_record_response=before_record_response,
        **kwargs,
    ) as cassette:
        yield cassette


@pytest.fixture
def cassette() -> Callable[..., _CassetteContext]:
    """Yield a factory that opens a scrubbed, base64-encoded VCR cassette."""

    def _open(path: str | Path, **kwargs: Any) -> _CassetteContext:
        return use_cassette(path, **kwargs)

    return _open
