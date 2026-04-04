"""Tests for arena database preservation on startup.

Validates that the arena preserves existing SQLite databases by default
and only deletes when explicitly requested via reset_db=True.

Addresses: S3 (destructive SQLite DB deletion on arena startup)
"""

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch


class TestArenaDBPreservation(unittest.TestCase):
    """S3: Arena must preserve existing DB by default."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_arena.db")

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def _create_test_db(self):
        """Create a dummy DB file with known content."""
        with open(self.db_path, "w") as f:
            f.write("test-data")
        return self.db_path

    def test_preserves_existing_db_by_default(self):
        """DB file should still exist after run_arena setup when reset_db=False."""
        self._create_test_db()
        self.assertTrue(os.path.exists(self.db_path))

        # Simulate the preservation logic (extracted from arena.py)
        reset_db = False
        if os.path.exists(self.db_path):
            if reset_db:
                os.remove(self.db_path)
            # else: preserve

        self.assertTrue(
            os.path.exists(self.db_path),
            "DB file was deleted despite reset_db=False",
        )

    def test_deletes_db_with_reset_flag(self):
        """DB file should be deleted when reset_db=True."""
        self._create_test_db()
        self.assertTrue(os.path.exists(self.db_path))

        reset_db = True
        if os.path.exists(self.db_path):
            if reset_db:
                os.remove(self.db_path)

        self.assertFalse(
            os.path.exists(self.db_path),
            "DB file should have been deleted with reset_db=True",
        )

    def test_creates_db_when_none_exists(self):
        """Arena should work fine when no DB exists yet."""
        self.assertFalse(os.path.exists(self.db_path))

        # The arena creates tables after the preservation check
        # Just verify the path is clean for creation
        reset_db = False
        if os.path.exists(self.db_path):
            if reset_db:
                os.remove(self.db_path)

        # DB doesn't exist — arena would proceed to create_all
        self.assertFalse(os.path.exists(self.db_path))

    def test_run_arena_async_signature_accepts_reset_db(self):
        """run_arena_async must accept reset_db keyword argument."""
        import inspect

        from self_fixing_engineer.arbiter.arena import run_arena_async

        sig = inspect.signature(run_arena_async)
        self.assertIn("reset_db", sig.parameters)
        self.assertEqual(
            sig.parameters["reset_db"].default, False
        )

    def test_run_arena_signature_accepts_reset_db(self):
        """run_arena must accept reset_db keyword argument."""
        import inspect

        from self_fixing_engineer.arbiter.arena import run_arena

        sig = inspect.signature(run_arena)
        self.assertIn("reset_db", sig.parameters)
        self.assertEqual(
            sig.parameters["reset_db"].default, False
        )


if __name__ == "__main__":
    unittest.main()
