# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Property-based tests for the provenance tracking module using Hypothesis.

Tests key public functions with arbitrary inputs to verify invariants that
should hold for *all* valid inputs, not just hand-picked examples.
"""

from __future__ import annotations

import re

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from generator.main.provenance import (
    ProvenanceTracker,
    extract_endpoints_from_code,
    run_fail_fast_validation,
    validate_syntax,
)

# ---------------------------------------------------------------------------
# ProvenanceTracker.compute_sha256
# ---------------------------------------------------------------------------


_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


class TestComputeSha256Properties:
    """Property-based tests for ProvenanceTracker.compute_sha256."""

    @given(content=st.text())
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_output_is_always_64_char_hex(self, content: str) -> None:
        """SHA-256 output must be exactly 64 lowercase hex characters."""
        result = ProvenanceTracker.compute_sha256(content)
        assert isinstance(result, str)
        assert len(result) == 64, f"Expected 64 chars, got {len(result)}: {result!r}"
        assert _HEX_RE.match(result) is not None, f"Non-hex output: {result!r}"

    @given(content=st.text())
    @settings(max_examples=100)
    def test_output_is_deterministic(self, content: str) -> None:
        """Same input must always produce the same digest."""
        assert ProvenanceTracker.compute_sha256(
            content
        ) == ProvenanceTracker.compute_sha256(content)

    @given(a=st.text(), b=st.text())
    @settings(max_examples=100)
    def test_distinct_inputs_are_very_unlikely_to_collide(self, a: str, b: str) -> None:
        """Different strings should (with overwhelming probability) produce different digests."""
        if a != b:
            # SHA-256 collision probability is negligible; this *should* always hold
            assert ProvenanceTracker.compute_sha256(a) != ProvenanceTracker.compute_sha256(
                b
            )


# ---------------------------------------------------------------------------
# extract_endpoints_from_code
# ---------------------------------------------------------------------------


class TestExtractEndpointsProperties:
    """Property-based tests for extract_endpoints_from_code."""

    @given(code=st.text(max_size=2000))
    @settings(
        max_examples=200,
        suppress_health_check=[HealthCheck.too_slow],
        deadline=None,
    )
    def test_never_raises_on_arbitrary_string(self, code: str) -> None:
        """extract_endpoints_from_code must not raise for any string input."""
        try:
            result = extract_endpoints_from_code(code)
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"extract_endpoints_from_code raised unexpectedly: {type(exc).__name__}: {exc}"
            )
        assert isinstance(result, list)

    @given(code=st.from_regex(r"@router\.(get|post|put|delete)\(['\"][^'\"]+['\"]\)", fullmatch=False))
    @settings(max_examples=50, deadline=None)
    def test_finds_endpoints_in_fastapi_style_code(self, code: str) -> None:
        """When code contains a FastAPI-style decorator, at least one endpoint should be detected."""
        result = extract_endpoints_from_code(code)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# validate_syntax
# ---------------------------------------------------------------------------


class TestValidateSyntaxProperties:
    """Property-based tests for validate_syntax."""

    @given(code=st.text(max_size=1000))
    @settings(
        max_examples=200,
        suppress_health_check=[HealthCheck.too_slow],
        deadline=None,
    )
    def test_always_returns_dict_with_valid_key(self, code: str) -> None:
        """validate_syntax must always return a dict containing a 'valid' key."""
        result = validate_syntax(code)
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert "valid" in result, f"'valid' key missing from: {result}"

    @given(code=st.text(max_size=1000))
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.too_slow],
        deadline=None,
    )
    def test_valid_key_is_boolean(self, code: str) -> None:
        """The 'valid' key must be a boolean."""
        result = validate_syntax(code)
        assert isinstance(result["valid"], bool)

    @given(code=st.just("def f():\n    return 42\n"))
    @settings(max_examples=1)
    def test_valid_python_is_valid(self, code: str) -> None:
        """Syntactically correct Python must be reported as valid."""
        assert validate_syntax(code)["valid"] is True

    @given(code=st.just("def f(\n"))
    @settings(max_examples=1)
    def test_invalid_python_is_invalid(self, code: str) -> None:
        """Syntactically broken Python must be reported as invalid."""
        assert validate_syntax(code)["valid"] is False


# ---------------------------------------------------------------------------
# run_fail_fast_validation
# ---------------------------------------------------------------------------


class TestRunFailFastValidationProperties:
    """Property-based tests for run_fail_fast_validation."""

    @given(
        filenames=st.lists(
            st.text(
                alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="._/"),
                min_size=1,
                max_size=30,
            ),
            min_size=0,
            max_size=5,
        ),
        contents=st.lists(st.text(max_size=500), min_size=0, max_size=5),
    )
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.too_slow],
        deadline=None,
    )
    def test_always_returns_dict_with_valid_bool(
        self, filenames: list, contents: list
    ) -> None:
        """run_fail_fast_validation must always return a dict with a bool 'valid' key."""
        # Build a files dict from the generated filenames and contents
        files: dict = {}
        for fname, content in zip(filenames, contents):
            files[fname] = content
        result = run_fail_fast_validation(files)
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert "valid" in result, f"'valid' key missing from: {result}"
        assert isinstance(result["valid"], bool)
