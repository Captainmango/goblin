"""Unit tests for :mod:`goblin_common.testing.vcr`."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import vcr
import yaml

from goblin_common.testing.vcr import (
    DEFAULT_SENSITIVE_BODY_FIELDS,
    DEFAULT_SENSITIVE_HEADERS,
    DEFAULT_SENSITIVE_QUERY_PARAMS,
    Base64YamlSerializer,
    make_scrubber,
    use_cassette,
)


class TestBase64YamlSerializer:
    """Tests for the custom cassette serializer."""

    def test_round_trip_preserves_cassette_dict(self):
        serializer = Base64YamlSerializer()
        cassette = {
            "interactions": [
                {
                    "request": {"method": "GET", "uri": "https://example.com/api"},
                    "response": {"status": {"code": 200, "message": "OK"}},
                },
            ],
            "version": 1,
        }

        encoded = serializer.serialize(cassette)
        deserialized = serializer.deserialize(encoded)

        assert deserialized == cassette

    def test_encoded_output_is_base64_text(self):
        serializer = Base64YamlSerializer()
        cassette = {"interactions": [], "version": 1}

        encoded = serializer.serialize(cassette)

        assert isinstance(encoded, str)
        assert base64.b64decode(encoded.encode("ascii")) is not None

    def test_encoded_output_is_smaller_than_raw_yaml(self):
        serializer = Base64YamlSerializer()
        cassette = {
            "interactions": [
                {
                    "request": {"method": "GET", "uri": "https://example.com/api" * 50},
                    "response": {"body": {"string": "ok" * 200}},
                },
            ],
            "version": 1,
        }
        raw_yaml = yaml.safe_dump(cassette, encoding="utf-8")

        encoded = serializer.serialize(cassette)

        assert len(encoded.encode("ascii")) < len(raw_yaml)

    def test_deserialize_invalid_data_raises(self):
        serializer = Base64YamlSerializer()

        with pytest.raises(Exception):
            serializer.deserialize("not valid base64!!!")


class TestMakeScrubber:
    """Tests for the scrubber factory."""

    def test_scrubs_configured_headers_case_insensitive(self):
        before_request, _ = make_scrubber()
        request = MagicMock()
        request.headers = {
            "Authorization": ["Bearer secret-token"],
            "X-Api-Key": ["abc123"],
            "Content-Type": ["application/json"],
        }
        request.uri = "https://example.com/api"
        request.body = None

        result = before_request(request)

        assert result.headers["Authorization"] == ["<REDACTED>"]
        assert result.headers["X-Api-Key"] == ["<REDACTED>"]
        assert result.headers["Content-Type"] == ["application/json"]

    def test_default_headers_cover_expected_names(self):
        assert "authorization" in DEFAULT_SENSITIVE_HEADERS
        assert "x-api-key" in DEFAULT_SENSITIVE_HEADERS

    def test_scrubs_query_parameters_from_uri(self):
        before_request, _ = make_scrubber()
        request = MagicMock()
        request.headers = {}
        request.uri = "https://example.com/api?token=secret&name=goblin&api_key=xyz"
        request.body = None

        result = before_request(request)

        assert "token=<REDACTED>" in result.uri
        assert "api_key=<REDACTED>" in result.uri
        assert "name=goblin" in result.uri
        assert "secret" not in result.uri
        assert "xyz" not in result.uri

    def test_default_query_params_cover_expected_names(self):
        for name in ["api_key", "apikey", "key", "token", "secret"]:
            assert name in DEFAULT_SENSITIVE_QUERY_PARAMS

    def test_scrubs_json_request_body_fields(self):
        before_request, _ = make_scrubber()
        request = MagicMock()
        request.headers = {"Content-Type": ["application/json"]}
        request.uri = "https://example.com/api"
        request.body = json.dumps({"password": "hunter2", "name": "goblin"}).encode("utf-8")

        result = before_request(request)

        body = json.loads(result.body)
        assert body["password"] == "<REDACTED>"
        assert body["name"] == "goblin"

    def test_scrubs_form_encoded_request_body(self):
        before_request, _ = make_scrubber()
        request = MagicMock()
        request.headers = {"Content-Type": ["application/x-www-form-urlencoded"]}
        request.uri = "https://example.com/api"
        request.body = b"api_key=secret&name=goblin"

        result = before_request(request)

        text = result.body.decode("utf-8")
        assert "api_key=<REDACTED>" in text
        assert "name=goblin" in text
        assert "secret" not in text

    def test_scrubs_json_response_body_fields(self):
        _, before_response = make_scrubber()
        response = MagicMock()
        response.headers = {}
        response.body = json.dumps({"access_token": "abc", "id": 1}).encode("utf-8")

        result = before_response(response)

        body = json.loads(result.body)
        assert body["access_token"] == "<REDACTED>"
        assert body["id"] == 1

    def test_default_body_fields_cover_expected_names(self):
        for name in ["api_key", "apikey", "key", "token", "secret", "password", "access_token"]:
            assert name in DEFAULT_SENSITIVE_BODY_FIELDS

    def test_extra_patterns_are_scrubbed_in_uri_and_body(self):
        before_request, before_response = make_scrubber(extra_patterns=[r"goblin-\d{4}"])
        request = MagicMock()
        request.headers = {}
        request.uri = "https://example.com/api?project=goblin-1234"
        request.body = b'{"name": "goblin-5678"}'

        request = before_request(request)
        assert "goblin-1234" not in request.uri
        assert "<REDACTED>" in request.uri
        assert b"goblin-5678" not in request.body

        response = MagicMock()
        response.headers = {}
        response.body = b'{"message": "goblin-9999"}'
        response = before_response(response)
        assert b"goblin-9999" not in response.body

    def test_custom_sensitive_lists_override_defaults(self):
        before_request, _ = make_scrubber(sensitive_headers=["x-custom"], sensitive_body_fields=["custom_field"])
        request = MagicMock()
        request.headers = {"Authorization": ["should-remain"], "X-Custom": ["scrub-me"]}
        request.uri = "https://example.com/api"
        request.body = json.dumps({"password": "should-remain", "custom_field": "scrub-me"}).encode("utf-8")

        result = before_request(request)

        assert result.headers["Authorization"] == ["should-remain"]
        assert result.headers["X-Custom"] == ["<REDACTED>"]
        body = json.loads(result.body)
        assert body["password"] == "should-remain"
        assert body["custom_field"] == "<REDACTED>"


class TestUseCassette:
    """Tests for the :func:`use_cassette` context manager."""

    def test_passes_serializer_and_callbacks_to_vcr(self, tmp_path: Path):
        cassette_path = tmp_path / "test.b64"

        with patch("goblin_common.testing.vcr.vcr.use_cassette") as mock_use_cassette:
            mock_cassette = MagicMock()
            mock_use_cassette.return_value.__enter__ = MagicMock(return_value=mock_cassette)
            mock_use_cassette.return_value.__exit__ = MagicMock(return_value=False)

            with use_cassette(cassette_path, record_mode="once") as cassette:
                assert cassette is mock_cassette

            mock_use_cassette.assert_called_once()
            call_kwargs = mock_use_cassette.call_args.kwargs
            assert call_kwargs["record_mode"] == "once"
            assert call_kwargs["serializer"] == "base64_yaml"
            assert call_kwargs["decode_compressed_response"] is True
            assert callable(call_kwargs["before_record_request"])
            assert callable(call_kwargs["before_record_response"])

    def test_creates_encoded_cassette_on_record(self, tmp_path: Path):
        cassette_path = tmp_path / "test.b64"
        sample = {
            "interactions": [
                {
                    "request": {"method": "GET", "uri": "https://example.com/api", "body": None, "headers": {}},
                    "response": {"status": {"code": 200, "message": "OK"}, "body": {"string": "ok"}, "headers": {}},
                },
            ],
            "version": 1,
        }

        serializer = Base64YamlSerializer()
        cassette_path.write_text(serializer.serialize(sample), encoding="ascii")

        with use_cassette(cassette_path, record_mode="none") as cassette:
            assert len(cassette.requests) == 1


class TestVcrSerializerRegistration:
    """Tests that the serializer is registered with the default vcrpy VCR."""

    def test_base64_yaml_serializer_is_registered(self):
        assert "base64_yaml" in vcr.default_vcr.serializers
        assert isinstance(vcr.default_vcr.serializers["base64_yaml"], Base64YamlSerializer)
