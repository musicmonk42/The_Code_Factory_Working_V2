"""Tests for fail-closed secret handling across all modules.

Validates that hardcoded secret fallbacks have been removed and that
each module raises an appropriate error when secrets are not configured.

Addresses: S1 (arena JWT), S4 (OmniCore secret), S5 (generator JWT), HMAC (audit key)
"""

import os
import unittest
from unittest.mock import MagicMock, patch


class TestArenaJWTFailClosed(unittest.TestCase):
    """S1: Arena must reject requests when ARENA_JWT_SECRET is not configured."""

    def test_arena_auth_rejects_without_jwt_secret(self):
        from fastapi import HTTPException

        from self_fixing_engineer.arbiter.arena import require_auth

        mock_settings = MagicMock()
        mock_settings.ARENA_JWT_SECRET = None

        @require_auth
        async def protected_endpoint(request, settings=None):
            return {"ok": True}

        mock_request = MagicMock()
        mock_request.headers = {"Authorization": "Bearer some-token"}

        import asyncio

        with self.assertRaises(HTTPException) as ctx:
            asyncio.get_event_loop().run_until_complete(
                protected_endpoint(mock_request, settings=mock_settings)
            )
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertIn("ARENA_JWT_SECRET", ctx.exception.detail)


class TestOmniCoreSecretFailClosed(unittest.TestCase):
    """S4: OmniCore must refuse initialization without a secret."""

    def test_omnicore_security_rejects_without_secret(self):
        env = os.environ.copy()
        env.pop("OMNICORE_SECRET", None)

        with patch.dict(os.environ, env, clear=True):
            from omnicore_engine.security_utils import EnterpriseSecurityUtils

            with self.assertRaises(RuntimeError) as ctx:
                EnterpriseSecurityUtils()
            self.assertIn("OMNICORE_SECRET", str(ctx.exception))


class TestGeneratorJWTFailClosed(unittest.TestCase):
    """S5: Generator API must not use a hardcoded dev key."""

    def test_generator_api_ephemeral_key_in_dev(self):
        """Dev mode should generate a random key, not a hardcoded one."""
        env = os.environ.copy()
        env.pop("JWT_SECRET_KEY", None)
        env["TESTING"] = "1"

        with patch.dict(os.environ, env, clear=True):
            # Re-import to trigger module-level key generation
            import importlib

            import generator.main.api as api_mod

            importlib.reload(api_mod)

            self.assertIsNotNone(api_mod.SECRET_KEY)
            self.assertNotEqual(
                api_mod.SECRET_KEY,
                "dev-secret-key-do-not-use-in-production",
            )
            self.assertTrue(len(api_mod.SECRET_KEY) >= 32)


class TestHMACKeyFailClosed(unittest.TestCase):
    """HMAC: Server must refuse to start without audit signing key."""

    def test_server_rejects_without_hmac_key(self):
        env = os.environ.copy()
        env.pop("AGENTIC_AUDIT_HMAC_KEY", None)
        env.pop("TESTING", None)
        env.pop("CI", None)
        env["APP_ENV"] = "production"

        with patch.dict(os.environ, env, clear=True):
            with patch("sys.modules", {**__import__("sys").modules}):
                # The server/main.py module-level code should raise
                # when AGENTIC_AUDIT_HMAC_KEY is missing in production
                with self.assertRaises(RuntimeError) as ctx:
                    import importlib

                    import server.main as srv

                    importlib.reload(srv)
                self.assertIn("AGENTIC_AUDIT_HMAC_KEY", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
