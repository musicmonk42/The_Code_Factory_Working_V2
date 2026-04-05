# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for the iterative retry behavior added to the SFE fix pipeline (Arena).

Validates:
  1. SFE_FIX_MAX_ATTEMPTS env var controls how many attempts the pipeline makes.
  2. _run_sfe_fix_pipeline retries when sandbox validation rejects a fix.
  3. Feedback from the previous failed validation is passed to propose_fix
     on retry.
  4. When the second attempt succeeds, the result status is "applied" and
     attempt_history has exactly 2 entries.
"""

import inspect
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("TESTING", "1")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _fake_server_modules(mock_sfe):
    """Return a sys.modules patch dict that satisfies all imports inside
    _run_sfe_fix_pipeline without requiring installed server dependencies.
    Each call produces fresh MagicMock instances to prevent cross-test state
    leakage.
    """
    return {
        "server.services.sfe_service": MagicMock(SFEService=lambda: mock_sfe),
        "server.storage": MagicMock(fixes_db={}),
        "server.schemas": MagicMock(),
    }


def _make_sfe_mock(*, proposals, validations, apply_return=None):
    """Build a MagicMock SFE service with deterministic async method responses."""
    mock_sfe = MagicMock()
    mock_sfe.propose_fix = AsyncMock(side_effect=list(proposals))
    mock_sfe.validate_fix_in_sandbox = AsyncMock(side_effect=list(validations))
    mock_sfe.apply_fix = AsyncMock(
        return_value=apply_return if apply_return is not None else {"applied": True}
    )
    mock_sfe.register_defect = MagicMock()
    return mock_sfe


# ===========================================================================
# Test 1: SFE_FIX_MAX_ATTEMPTS behavioural contract
# ===========================================================================


class TestSfeFixMaxAttempts:
    """_run_sfe_fix_pipeline must respect SFE_FIX_MAX_ATTEMPTS."""

    @pytest.mark.asyncio
    async def test_defaults_to_three_attempts(self, monkeypatch):
        """Without SFE_FIX_MAX_ATTEMPTS set, the pipeline stops after 3 attempts."""
        try:
            from self_fixing_engineer.arbiter.arena import ArbiterArena
        except ImportError as exc:
            pytest.skip(f"ArbiterArena not importable: {exc}")

        monkeypatch.delenv("SFE_FIX_MAX_ATTEMPTS", raising=False)

        rejected = {"status": "rejected", "reason": "still broken"}
        mock_sfe = _make_sfe_mock(
            proposals=[
                {"fix_id": f"fix-{i}", "description": f"attempt {i}"}
                for i in range(1, 4)
            ],
            validations=[rejected] * 3,
        )

        mock_settings = MagicMock()
        arena = ArbiterArena(settings=mock_settings, name="default-attempts-arena")
        with patch.dict("sys.modules", _fake_server_modules(mock_sfe)):
            results = await arena._run_sfe_fix_pipeline(
                [{"id": "err-default", "type": "import_error", "message": "x"}],
                job_id="job-0",
            )

        assert len(results) == 1
        # Default of 3 means exactly 3 propose_fix calls were made
        assert mock_sfe.propose_fix.call_count == 3, (
            f"Expected 3 propose_fix calls (default max_attempts), "
            f"got {mock_sfe.propose_fix.call_count}"
        )
        assert results[0]["status"] == "validation_failed"
        assert len(results[0]["attempt_history"]) == 3

    @pytest.mark.asyncio
    async def test_custom_max_attempts_is_respected(self, monkeypatch):
        """SFE_FIX_MAX_ATTEMPTS=2 stops the pipeline after exactly 2 attempts."""
        try:
            from self_fixing_engineer.arbiter.arena import ArbiterArena
        except ImportError as exc:
            pytest.skip(f"ArbiterArena not importable: {exc}")

        monkeypatch.setenv("SFE_FIX_MAX_ATTEMPTS", "2")

        rejected = {"status": "rejected", "reason": "still broken"}
        mock_sfe = _make_sfe_mock(
            proposals=[
                {"fix_id": f"fix-{i}", "description": f"attempt {i}"}
                for i in range(1, 3)
            ],
            validations=[rejected, rejected],
        )

        mock_settings = MagicMock()
        arena = ArbiterArena(settings=mock_settings, name="custom-attempts-arena")
        with patch.dict("sys.modules", _fake_server_modules(mock_sfe)):
            results = await arena._run_sfe_fix_pipeline(
                [{"id": "err-custom", "type": "import_error", "message": "x"}],
                job_id="job-c",
            )

        assert mock_sfe.propose_fix.call_count == 2, (
            f"Expected exactly 2 propose_fix calls, "
            f"got {mock_sfe.propose_fix.call_count}"
        )
        assert results[0]["status"] == "validation_failed"
        assert len(results[0]["attempt_history"]) == 2


# ===========================================================================
# Test 2: propose_fix accepts feedback parameter
# ===========================================================================


class TestProposeFeedbackParameter:
    """SFEService.propose_fix must accept an optional feedback argument."""

    def test_propose_fix_has_feedback_param(self):
        """propose_fix signature must include a 'feedback' keyword argument."""
        try:
            from server.services.sfe_service import SFEService
        except ImportError as exc:
            pytest.skip(f"SFEService not importable: {exc}")

        sig = inspect.signature(SFEService.propose_fix)
        assert "feedback" in sig.parameters, (
            "SFEService.propose_fix must accept a 'feedback' keyword argument"
        )
        param = sig.parameters["feedback"]
        assert param.default is None, (
            "feedback parameter must default to None for backwards compatibility"
        )


# ===========================================================================
# Test 3: Retry pipeline — first attempt fails, second succeeds
# ===========================================================================


class TestArenaRetryPipeline:
    """_run_sfe_fix_pipeline should retry and succeed on the second attempt."""

    @pytest.mark.asyncio
    async def test_retry_on_validation_failure(self, monkeypatch):
        """
        Simulate:
          - Attempt 1: propose_fix → fix_id="fix-1", validate → rejected
          - Attempt 2: propose_fix → fix_id="fix-2", validate → validated
          - apply_fix → applied

        Assert:
          - Final status is "applied"
          - attempt_history has 2 entries
          - propose_fix was called twice; second call received the first
            validation result as the feedback kwarg
          - apply_fix was called exactly once with the winning fix_id
        """
        try:
            from self_fixing_engineer.arbiter.arena import ArbiterArena
        except ImportError as exc:
            pytest.skip(f"ArbiterArena not importable: {exc}")

        mock_settings = MagicMock()
        arena = ArbiterArena(settings=mock_settings, name="retry-test-arena")

        failed_validation = {"status": "rejected", "reason": "tests still fail"}
        passed_validation = {"status": "validated", "reason": "tests pass"}

        mock_sfe = _make_sfe_mock(
            proposals=[
                {"fix_id": "fix-1", "description": "first attempt"},
                {"fix_id": "fix-2", "description": "second attempt"},
            ],
            validations=[failed_validation, passed_validation],
            apply_return={"applied": True, "fix_id": "fix-2"},
        )

        monkeypatch.setenv("SFE_FIX_MAX_ATTEMPTS", "3")

        with patch.dict("sys.modules", _fake_server_modules(mock_sfe)):
            results = await arena._run_sfe_fix_pipeline(
                [{"id": "err-1", "type": "import_error", "message": "missing import"}],
                job_id="job-1",
            )

        assert len(results) == 1, f"Expected 1 result, got {len(results)}: {results}"
        result = results[0]

        # Status must be "applied" because the second attempt succeeded
        assert result["status"] == "applied", (
            f"Expected status 'applied', got {result['status']!r}"
        )

        attempt_history = result.get("attempt_history", [])
        assert len(attempt_history) == 2, (
            f"Expected 2 entries in attempt_history, got {len(attempt_history)}: "
            f"{attempt_history}"
        )

        # First attempt — rejected
        entry1 = attempt_history[0]
        assert entry1["attempt"] == 1
        assert entry1["fix_id"] == "fix-1"
        assert entry1["validation"]["status"] == "rejected"

        # Second attempt — validated
        entry2 = attempt_history[1]
        assert entry2["attempt"] == 2
        assert entry2["fix_id"] == "fix-2"
        assert entry2["validation"]["status"] == "validated"

        # propose_fix must have been called exactly twice
        assert mock_sfe.propose_fix.call_count == 2

        # First call: no feedback (None)
        first_call = mock_sfe.propose_fix.call_args_list[0]
        assert first_call.args == ("err-1",)
        assert first_call.kwargs.get("feedback") is None

        # Second call: feedback must be the rejected validation result
        second_call = mock_sfe.propose_fix.call_args_list[1]
        assert second_call.args == ("err-1",)
        assert second_call.kwargs.get("feedback") == failed_validation, (
            f"Second propose_fix call must carry the failed validation as feedback; "
            f"got: {second_call.kwargs.get('feedback')!r}"
        )

        # apply_fix must be called once with the winning fix
        mock_sfe.apply_fix.assert_called_once_with("fix-2", dry_run=False)

    @pytest.mark.asyncio
    async def test_exhausted_attempts_records_full_history(self, monkeypatch):
        """
        When all max_attempts are exhausted the result must be
        ``validation_failed`` and attempt_history must contain one entry per
        attempt.  apply_fix must never be called.
        """
        try:
            from self_fixing_engineer.arbiter.arena import ArbiterArena
        except ImportError as exc:
            pytest.skip(f"ArbiterArena not importable: {exc}")

        mock_settings = MagicMock()
        arena = ArbiterArena(settings=mock_settings, name="exhaust-test-arena")

        rejected = {"status": "rejected", "reason": "still broken"}
        mock_sfe = _make_sfe_mock(
            proposals=[
                {"fix_id": f"fix-{i}", "description": f"attempt {i}"}
                for i in range(1, 4)
            ],
            validations=[rejected, rejected, rejected],
        )

        monkeypatch.setenv("SFE_FIX_MAX_ATTEMPTS", "3")

        with patch.dict("sys.modules", _fake_server_modules(mock_sfe)):
            results = await arena._run_sfe_fix_pipeline(
                [{"id": "err-2", "type": "syntax_error", "message": "bad syntax"}],
                job_id="job-2",
            )

        assert len(results) == 1
        result = results[0]
        assert result["status"] == "validation_failed", (
            f"Expected 'validation_failed', got {result['status']!r}"
        )

        attempt_history = result.get("attempt_history", [])
        assert len(attempt_history) == 3, (
            f"Expected 3 entries in attempt_history, got {len(attempt_history)}"
        )
        for i, entry in enumerate(attempt_history, start=1):
            assert entry["attempt"] == i
            assert entry["validation"]["status"] == "rejected"

        # apply_fix must never be called when all validation attempts fail
        mock_sfe.apply_fix.assert_not_called()

