"""Tests for API key authentication middleware.

Validates that the server enforces API key authentication when
REQUIRE_API_KEY=1, and bypasses it when not set.

Addresses: #1787 (zero auth on main server API)
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from starlette.requests import Request

from server.middleware.api_key_auth import (
    _get_valid_keys,
    _is_auth_required,
    require_api_key,
    reset_key_cache,
    reset_rate_limit_state,
)


def _build_request(
    *,
    path: str = "/api/test",
    method: str = "GET",
    client_host: str = "127.0.0.1",
    headers: dict[str, str] | None = None,
) -> Request:
    """Create a minimal Starlette Request for dependency unit tests."""
    encoded_headers = [
        (name.lower().encode("latin-1"), value.encode("latin-1"))
        for name, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": encoded_headers,
        "client": (client_host, 12345),
        "server": ("testserver", 80),
        "root_path": "",
    }
    return Request(scope)


class TestAuthRequired(unittest.TestCase):
    """Test REQUIRE_API_KEY environment check."""

    def test_auth_not_required_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(_is_auth_required())

    def test_auth_required_when_set(self):
        with patch.dict(os.environ, {"REQUIRE_API_KEY": "1"}):
            self.assertTrue(_is_auth_required())

    def test_auth_not_required_when_zero(self):
        with patch.dict(os.environ, {"REQUIRE_API_KEY": "0"}):
            self.assertFalse(_is_auth_required())


class TestValidKeys(unittest.TestCase):
    """Test API key loading from environment."""

    def setUp(self):
        reset_key_cache()
        reset_rate_limit_state()

    def tearDown(self):
        reset_key_cache()
        reset_rate_limit_state()

    def test_loads_keys_from_env(self):
        with patch.dict(os.environ, {"SERVER_API_KEYS": "key1,key2,key3"}):
            keys = _get_valid_keys()
            self.assertEqual(keys, {"key1", "key2", "key3"})

    def test_strips_whitespace(self):
        with patch.dict(os.environ, {"SERVER_API_KEYS": " key1 , key2 "}):
            keys = _get_valid_keys()
            self.assertEqual(keys, {"key1", "key2"})

    def test_empty_when_not_set(self):
        with patch.dict(os.environ, {}, clear=True):
            keys = _get_valid_keys()
            self.assertEqual(keys, set())


class TestRequireApiKey(unittest.TestCase):
    """Test the FastAPI dependency function."""

    def setUp(self):
        reset_key_cache()
        reset_rate_limit_state()

    def tearDown(self):
        reset_key_cache()
        reset_rate_limit_state()

    def test_bypassed_when_not_required(self):
        import asyncio

        with patch.dict(os.environ, {"REQUIRE_API_KEY": "0"}):
            result = asyncio.get_event_loop().run_until_complete(
                require_api_key(api_key="anything")
            )
            self.assertIsNone(result)

    def test_rejects_missing_key_when_required(self):
        import asyncio

        from fastapi import HTTPException

        with patch.dict(
            os.environ,
            {"REQUIRE_API_KEY": "1", "SERVER_API_KEYS": "valid-key"},
        ):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.get_event_loop().run_until_complete(
                    require_api_key(api_key=None)
                )
            self.assertEqual(ctx.exception.status_code, 401)

    def test_rejects_invalid_key(self):
        import asyncio

        from fastapi import HTTPException

        with patch.dict(
            os.environ,
            {"REQUIRE_API_KEY": "1", "SERVER_API_KEYS": "valid-key"},
        ):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.get_event_loop().run_until_complete(
                    require_api_key(api_key="wrong-key")
                )
            self.assertEqual(ctx.exception.status_code, 403)

    def test_accepts_valid_key(self):
        import asyncio

        with patch.dict(
            os.environ,
            {"REQUIRE_API_KEY": "1", "SERVER_API_KEYS": "valid-key"},
        ):
            result = asyncio.get_event_loop().run_until_complete(
                require_api_key(api_key="valid-key")
            )
            self.assertEqual(result, "valid-key")

    def test_503_when_no_keys_configured(self):
        import asyncio

        from fastapi import HTTPException

        with patch.dict(
            os.environ,
            {"REQUIRE_API_KEY": "1", "SERVER_API_KEYS": ""},
        ):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.get_event_loop().run_until_complete(
                    require_api_key(api_key="any-key")
                )
            self.assertEqual(ctx.exception.status_code, 503)

    def test_rate_limit_rejects_excess_requests(self):
        import asyncio

        from fastapi import HTTPException

        request = _build_request(path="/api/jobs")

        with patch.dict(
            os.environ,
            {
                "REQUIRE_API_KEY": "0",
                "HTTP_RATE_LIMIT_ENABLED": "1",
                "HTTP_RATE_LIMIT_REQUESTS": "1",
                "HTTP_RATE_LIMIT_WINDOW_SECONDS": "60",
            },
            clear=True,
        ):
            asyncio.get_event_loop().run_until_complete(
                require_api_key(request=request, api_key=None)
            )
            with self.assertRaises(HTTPException) as ctx:
                asyncio.get_event_loop().run_until_complete(
                    require_api_key(request=request, api_key=None)
                )
            self.assertEqual(ctx.exception.status_code, 429)
            self.assertEqual(ctx.exception.headers["Retry-After"], "60")

    def test_rate_limit_exempts_health_path(self):
        import asyncio

        request = _build_request(path="/health")

        with patch.dict(
            os.environ,
            {
                "REQUIRE_API_KEY": "0",
                "HTTP_RATE_LIMIT_ENABLED": "1",
                "HTTP_RATE_LIMIT_REQUESTS": "1",
                "HTTP_RATE_LIMIT_WINDOW_SECONDS": "60",
            },
            clear=True,
        ):
            asyncio.get_event_loop().run_until_complete(
                require_api_key(request=request, api_key=None)
            )
            result = asyncio.get_event_loop().run_until_complete(
                require_api_key(request=request, api_key=None)
            )
            self.assertIsNone(result)

    def test_request_body_limit_rejects_large_content_length(self):
        import asyncio

        from fastapi import HTTPException

        request = _build_request(
            method="POST",
            headers={"Content-Length": "11"},
        )

        with patch.dict(
            os.environ,
            {
                "REQUIRE_API_KEY": "0",
                "MAX_REQUEST_BODY_SIZE_BYTES": "10",
            },
            clear=True,
        ):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.get_event_loop().run_until_complete(
                    require_api_key(request=request, api_key=None)
                )
            self.assertEqual(ctx.exception.status_code, 413)

    def test_request_body_limit_ignores_missing_content_length(self):
        import asyncio

        request = _build_request(method="POST")

        with patch.dict(
            os.environ,
            {
                "REQUIRE_API_KEY": "0",
                "MAX_REQUEST_BODY_SIZE_BYTES": "10",
            },
            clear=True,
        ):
            result = asyncio.get_event_loop().run_until_complete(
                require_api_key(request=request, api_key=None)
            )
            self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
