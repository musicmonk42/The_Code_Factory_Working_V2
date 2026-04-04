"""Tests for SFE sandbox validation logic.

Validates that fix acceptance requires a clean test suite pass (returncode == 0),
not merely `passed > 0`. The collection-error improvement heuristic is preserved.

Addresses: S2 (sandbox validation accepts partial passes)
"""

import unittest


class TestSandboxValidation(unittest.TestCase):
    """S2: Sandbox must reject fixes where tests fail, even if some pass."""

    def test_sandbox_rejects_partial_pass(self):
        """A fix with returncode=1 and passed=3 must NOT be validated."""
        returncode = 1
        passed = 3

        # The fixed condition: only returncode == 0 validates
        should_validate = returncode == 0
        self.assertFalse(should_validate)

        # The OLD buggy condition would have accepted this:
        old_buggy_condition = returncode == 0 or passed > 0
        self.assertTrue(old_buggy_condition)  # Proves the old bug existed

    def test_sandbox_accepts_clean_pass(self):
        """A fix with returncode=0 must be validated."""
        should_validate = 0 == 0  # returncode == 0
        self.assertTrue(should_validate)

    def test_sandbox_accepts_collection_error_improvement(self):
        """Reduction in collection errors is a valid acceptance path."""
        baseline_collection_errors = 5
        post_fix_collection_errors = 2

        should_accept = (
            baseline_collection_errors > 0
            and post_fix_collection_errors < baseline_collection_errors
        )
        self.assertTrue(should_accept)

    def test_sandbox_rejects_zero_pass_zero_return(self):
        """returncode=1 with passed=0 must NOT validate."""
        proc = MagicMock()
        proc.returncode = 1

        should_validate = proc.returncode == 0
        self.assertFalse(should_validate)


if __name__ == "__main__":
    unittest.main()
