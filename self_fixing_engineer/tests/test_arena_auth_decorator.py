"""Tests for arena auth decorator HTTP status propagation.

Validates that HTTPException (401/403) is not swallowed by the broad
except Exception handler and converted to 500.

Addresses: D3 (auth decorator swallows 401/403 as 500)
"""

import asyncio
import unittest
from unittest.mock import MagicMock, patch


class TestArenaAuthDecorator(unittest.TestCase):
    """D3: Auth decorator must propagate 401/403, not convert to 500."""

    def _run_async(self, coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def test_auth_returns_401_for_invalid_token(self):
        """Invalid JWT token should return 401, not 500."""
        from fastapi import HTTPException

        from self_fixing_engineer.arbiter.arena import require_auth

        mock_settings = MagicMock()
        mock_settings.ARENA_JWT_SECRET = MagicMock()
        mock_settings.ARENA_JWT_SECRET.get_secret_value.return_value = "test-secret"

        @require_auth
        async def endpoint(request, settings=None):
            return {"ok": True}

        mock_request = MagicMock()
        mock_request.headers = {"Authorization": "Bearer invalid-jwt-token"}

        with self.assertRaises(HTTPException) as ctx:
            self._run_async(endpoint(mock_request, settings=mock_settings))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_auth_returns_503_for_missing_secret(self):
        """Missing ARENA_JWT_SECRET should return 503, not 500."""
        from fastapi import HTTPException

        from self_fixing_engineer.arbiter.arena import require_auth

        mock_settings = MagicMock()
        mock_settings.ARENA_JWT_SECRET = None

        @require_auth
        async def endpoint(request, settings=None):
            return {"ok": True}

        mock_request = MagicMock()
        mock_request.headers = {"Authorization": "Bearer some-token"}

        with self.assertRaises(HTTPException) as ctx:
            self._run_async(endpoint(mock_request, settings=mock_settings))
        # Must be 503 (not configured), not 500 (swallowed)
        self.assertEqual(ctx.exception.status_code, 503)

    def test_auth_returns_401_for_missing_header(self):
        """Missing Authorization header should return 401."""
        from fastapi import HTTPException

        from self_fixing_engineer.arbiter.arena import require_auth

        @require_auth
        async def endpoint(request, settings=None):
            return {"ok": True}

        mock_request = MagicMock()
        mock_request.headers = {}

        with self.assertRaises(HTTPException) as ctx:
            self._run_async(endpoint(mock_request, settings=mock_settings))
        self.assertEqual(ctx.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
