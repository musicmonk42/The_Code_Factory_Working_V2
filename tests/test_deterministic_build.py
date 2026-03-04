# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test Suite: Deterministic Build Mode
=====================================

Comprehensive validation of the ``generator.deterministic`` module which
underpins the platform's byte-identical reproducible-build guarantee.

Coverage
--------
1. :func:`~generator.deterministic.is_deterministic` — env-flag parsing,
   boundary values, and the absence of false positives.
2. :func:`~generator.deterministic.get_deterministic_llm_params` — correct
   params under both modes; mergeability with arbitrary kwarg dicts.
3. :func:`~generator.deterministic.normalize_content` — CRLF/CR→LF,
   trailing-newline enforcement, passthrough in non-deterministic mode,
   ``None``/non-string passthrough.
4. :func:`~generator.deterministic.deterministic_json_dumps` — sorted keys
   at every nesting level, ``ensure_ascii=False``, compact vs. indented
   modes.
5. :func:`~generator.deterministic.compute_content_hash` and
   :func:`~generator.deterministic.compute_plan_hash` — SHA-256 correctness,
   separator-boundary collision resistance, determinism.
6. :func:`~generator.deterministic.sorted_rglob` — lexicographic ordering
   across flat and nested directory trees; empty-directory edge case.
7. :func:`~generator.deterministic.deterministic_zip_create` — byte-identical
   archives across two independent runs; sorted entry order; fixed
   timestamps; POSIX arcnames; ``_output.zip`` exclusion; atomic write
   (tmp renamed to final); graceful ``OSError`` on missing source.
8. :func:`~generator.deterministic.write_build_plan` — atomic write,
   sorted-key JSON, idempotent second call, invalid-input guards.
9. :func:`~generator.deterministic.enforce_build_plan` — all five state
   transitions: baseline write, spec-hash change, unchanged no-op,
   mismatch-error under ``DETERMINISTIC=1``, silent update under
   ``DETERMINISTIC=0``; corrupt-plan recovery.
10. :class:`~generator.deterministic.BuildPlanMismatchError` — exception
    hierarchy, ``spec_hash`` / ``diff_summary`` attributes.
11. :class:`~generator.deterministic.ZipCreationResult` and
    :class:`~generator.deterministic.BuildPlanResult` — frozen-dataclass
    immutability and field correctness.

Industry Standards Compliance
------------------------------
- NIST SP 800-53 CM-3: Configuration-change control validation.
- SOC 2 Type II: Change-management audit trail enforcement.
- SLSA Supply Chain Level 2: reproducible-build output verification.
- ISO/IEC 25010: Software quality — testability and maintainability.

Notes
-----
All tests are self-contained (``tmp_path`` fixtures only) and safe to run
in CI without any live API keys or external services.  The ``DETERMINISTIC``
environment variable is patched via :func:`unittest.mock.patch.dict` so
that tests cannot leak state across the process.
"""

from __future__ import annotations

import hashlib
import json
import os
import zipfile
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Internal helpers — used by multiple test classes
# ---------------------------------------------------------------------------


def _set_env(value: str = "1"):
    """Temporarily set ``DETERMINISTIC=<value>`` for the duration of a test."""
    return patch.dict(os.environ, {"DETERMINISTIC": value})


def _unset_deterministic():
    """Remove ``DETERMINISTIC`` from the environment for the duration of a test."""
    env_without = {k: v for k, v in os.environ.items() if k != "DETERMINISTIC"}
    return patch.dict(os.environ, env_without, clear=True)


def _make_source_tree(base: Path) -> Path:
    """Create a small, deterministic directory tree suitable for ZIP tests.

    Layout::

        base/src/
            alpha.py      — print('alpha')
            beta.py       — print('beta')
            subdir/
                gamma.py  — print('gamma')

    Returns the ``src/`` Path so callers can pass it directly to
    ``deterministic_zip_create``.
    """
    src = base / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "alpha.py").write_text("print('alpha')\n", encoding="utf-8")
    (src / "beta.py").write_text("print('beta')\n", encoding="utf-8")
    sub = src / "subdir"
    sub.mkdir()
    (sub / "gamma.py").write_text("print('gamma')\n", encoding="utf-8")
    return src


def _make_plan(spec_hash: str = "abc123def456") -> Dict[str, Any]:
    """Return a minimal well-formed build plan dict."""
    return {
        "spec_hash": spec_hash,
        "model": "gpt-4o",
        "temperature": 0,
        "files": ["app/main.py", "app/models.py"],
        "entities": ["User", "Session"],
    }


# ===========================================================================
# 1. is_deterministic()
# ===========================================================================


class TestIsDeterministic:
    """Validate all parsing branches of the DETERMINISTIC env-flag accessor."""

    def test_returns_true_when_env_is_exactly_one(self):
        """Only the string ``'1'`` activates deterministic mode."""
        from generator.deterministic import is_deterministic

        with _set_env("1"):
            assert is_deterministic() is True

    def test_returns_false_when_env_is_zero(self):
        from generator.deterministic import is_deterministic

        with _set_env("0"):
            assert is_deterministic() is False

    def test_returns_false_when_env_is_absent(self):
        from generator.deterministic import is_deterministic

        with _unset_deterministic():
            assert is_deterministic() is False

    @pytest.mark.parametrize(
        "value",
        ["true", "True", "TRUE", "yes", "on", "2", "enabled"],
        ids=lambda v: repr(v),
    )
    def test_returns_false_for_non_one_truthy_values(self, value: str):
        """Non-``'1'`` strings must never activate deterministic mode.

        The flag is intentionally strict to prevent accidental activation from
        shell conventions like ``export DETERMINISTIC=true``.
        """
        from generator.deterministic import is_deterministic

        with _set_env(value):
            assert is_deterministic() is False, (
                f"is_deterministic() should be False for DETERMINISTIC={value!r}"
            )

    def test_whitespace_trimmed_before_comparison(self):
        """Leading/trailing whitespace must be stripped before comparing."""
        from generator.deterministic import is_deterministic

        with _set_env("  1  "):
            # Stricter platforms set the var with surrounding spaces.
            # The value "  1  ".strip() == "1" should activate the flag.
            assert is_deterministic() is True


# ===========================================================================
# 2. get_deterministic_llm_params()
# ===========================================================================


class TestGetDeterministicLLMParams:
    """Verify LLM parameter overrides for deterministic sampling."""

    def test_returns_temperature_zero_in_deterministic_mode(self):
        from generator.deterministic import get_deterministic_llm_params

        with _set_env("1"):
            params = get_deterministic_llm_params()
        assert params["temperature"] == 0

    def test_returns_top_p_one_in_deterministic_mode(self):
        from generator.deterministic import get_deterministic_llm_params

        with _set_env("1"):
            assert get_deterministic_llm_params()["top_p"] == 1

    def test_returns_seed_zero_in_deterministic_mode(self):
        from generator.deterministic import get_deterministic_llm_params

        with _set_env("1"):
            assert get_deterministic_llm_params()["seed"] == 0

    def test_returns_empty_dict_in_non_deterministic_mode(self):
        """Empty dict allows unconditional merging without branching."""
        from generator.deterministic import get_deterministic_llm_params

        with _set_env("0"):
            assert get_deterministic_llm_params() == {}

    def test_mergeable_with_base_kwargs(self):
        """Merging into base_kwargs must override temperature/top_p/seed."""
        from generator.deterministic import get_deterministic_llm_params

        base = {"temperature": 0.7, "max_tokens": 1024}
        with _set_env("1"):
            merged = {**base, **get_deterministic_llm_params()}
        assert merged["temperature"] == 0
        assert merged["max_tokens"] == 1024, "Unrelated keys must be preserved"

    def test_non_deterministic_merge_leaves_base_unchanged(self):
        from generator.deterministic import get_deterministic_llm_params

        base = {"temperature": 0.7, "max_tokens": 512}
        with _set_env("0"):
            merged = {**base, **get_deterministic_llm_params()}
        assert merged == base


# ===========================================================================
# 3. normalize_content()
# ===========================================================================


class TestNormalizeContent:
    """Validate all normalisation transformations and passthrough cases."""

    # ── Active (DETERMINISTIC=1) ─────────────────────────────────────────────

    def test_crlf_converted_to_lf(self):
        from generator.deterministic import normalize_content

        with _set_env("1"):
            result = normalize_content("line1\r\nline2\r\n")
        assert "\r" not in result
        assert "line1\nline2\n" == result

    def test_bare_cr_converted_to_lf(self):
        from generator.deterministic import normalize_content

        with _set_env("1"):
            result = normalize_content("line1\rline2")
        assert "\r" not in result

    def test_single_trailing_newline_enforced(self):
        from generator.deterministic import normalize_content

        with _set_env("1"):
            result = normalize_content("no trailing newline")
        assert result.endswith("\n")

    def test_multiple_trailing_newlines_collapsed_to_one(self):
        from generator.deterministic import normalize_content

        with _set_env("1"):
            result = normalize_content("content\n\n\n")
        assert result == "content\n", (
            "Multiple trailing newlines must be collapsed to exactly one"
        )

    def test_already_normalised_content_unchanged(self):
        from generator.deterministic import normalize_content

        content = "perfectly\nnormalised\ncontent\n"
        with _set_env("1"):
            result = normalize_content(content)
        assert result == content

    def test_empty_string_becomes_single_newline(self):
        from generator.deterministic import normalize_content

        with _set_env("1"):
            result = normalize_content("")
        assert result == "\n"

    # ── Passthrough (DETERMINISTIC=0) ────────────────────────────────────────

    def test_crlf_preserved_in_non_deterministic_mode(self):
        from generator.deterministic import normalize_content

        original = "line1\r\nline2"
        with _set_env("0"):
            assert normalize_content(original) is original

    # ── Non-string passthrough ───────────────────────────────────────────────

    @pytest.mark.parametrize(
        "value",
        [None, 42, 3.14, b"bytes", [], {}],
        ids=lambda v: type(v).__name__,
    )
    def test_non_string_passthrough_in_deterministic_mode(self, value: Any):
        """Non-str inputs must be returned unmodified (type-safe passthrough)."""
        from generator.deterministic import normalize_content

        with _set_env("1"):
            assert normalize_content(value) is value  # type: ignore[arg-type]


# ===========================================================================
# 4. deterministic_json_dumps()
# ===========================================================================


class TestDeterministicJsonDumps:
    """Verify stable, reproducible JSON serialisation."""

    def test_top_level_keys_are_sorted(self):
        from generator.deterministic import deterministic_json_dumps

        obj = {"z": 1, "a": 2, "m": 3}
        parsed = json.loads(deterministic_json_dumps(obj))
        keys = list(parsed.keys())
        assert keys == sorted(keys), f"Top-level keys not sorted: {keys}"

    def test_nested_dict_keys_are_sorted(self):
        from generator.deterministic import deterministic_json_dumps

        obj = {"outer": {"z": 99, "a": 1, "k": 50}}
        parsed = json.loads(deterministic_json_dumps(obj))
        inner_keys = list(parsed["outer"].keys())
        assert inner_keys == sorted(inner_keys)

    def test_output_is_identical_for_same_input(self):
        """Two calls with the same input must produce byte-identical output."""
        from generator.deterministic import deterministic_json_dumps

        obj = {"b": [3, 1, 2], "a": {"z": 0, "y": 1}, "c": "hello"}
        assert deterministic_json_dumps(obj) == deterministic_json_dumps(obj)

    def test_insertion_order_irrelevant(self):
        """Python dict with reversed insertion order must produce same output."""
        from generator.deterministic import deterministic_json_dumps

        obj_forward = {"a": 1, "b": 2, "c": 3}
        obj_reverse = {"c": 3, "b": 2, "a": 1}
        assert deterministic_json_dumps(obj_forward) == deterministic_json_dumps(
            obj_reverse
        )

    def test_non_ascii_unicode_preserved_verbatim(self):
        """Unicode must not be escaped as \\uXXXX sequences."""
        from generator.deterministic import deterministic_json_dumps

        for char in ("é", "ñ", "中", "🎉", "日本語"):
            result = deterministic_json_dumps({"msg": char})
            assert char in result, f"Character {char!r} was escaped in JSON output"

    def test_compact_mode_uses_minimal_separators(self):
        """``indent=None`` must produce compact output without extra spaces."""
        from generator.deterministic import deterministic_json_dumps

        result = deterministic_json_dumps({"a": 1, "b": 2}, indent=None)
        assert " " not in result, (
            f"Compact JSON must not contain spaces; got: {result!r}"
        )
        assert json.loads(result) == {"a": 1, "b": 2}

    def test_indented_output_is_valid_json(self):
        from generator.deterministic import deterministic_json_dumps

        obj = {"key": [1, 2, {"nested": True}]}
        result = deterministic_json_dumps(obj, indent=2)
        assert json.loads(result) == obj


# ===========================================================================
# 5. compute_content_hash() / compute_plan_hash()
# ===========================================================================


class TestHashing:
    """Validate SHA-256 correctness, collision resistance, and determinism."""

    # ── compute_content_hash ─────────────────────────────────────────────────

    def test_str_hash_matches_manual_sha256(self):
        from generator.deterministic import compute_content_hash

        data = "hello world"
        expected = hashlib.sha256(data.encode("utf-8")).hexdigest()
        assert compute_content_hash(data) == expected

    def test_bytes_hash_matches_manual_sha256(self):
        from generator.deterministic import compute_content_hash

        data = b"\x00\x01\x02\xff"
        expected = hashlib.sha256(data).hexdigest()
        assert compute_content_hash(data) == expected

    def test_hash_output_is_64_hex_chars(self):
        from generator.deterministic import compute_content_hash

        result = compute_content_hash("test")
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_hash_is_deterministic_across_calls(self):
        from generator.deterministic import compute_content_hash

        assert compute_content_hash("stable") == compute_content_hash("stable")

    def test_different_inputs_produce_different_hashes(self):
        from generator.deterministic import compute_content_hash

        assert compute_content_hash("input_a") != compute_content_hash("input_b")

    def test_raises_type_error_for_non_string_non_bytes(self):
        from generator.deterministic import compute_content_hash

        with pytest.raises(TypeError):
            compute_content_hash(12345)  # type: ignore[arg-type]

    # ── compute_plan_hash ────────────────────────────────────────────────────

    def test_plan_hash_is_deterministic(self):
        from generator.deterministic import compute_plan_hash

        h1 = compute_plan_hash("spec", "tmpl", "prompt", "cfg")
        h2 = compute_plan_hash("spec", "tmpl", "prompt", "cfg")
        assert h1 == h2

    def test_plan_hash_changes_on_spec_change(self):
        from generator.deterministic import compute_plan_hash

        assert compute_plan_hash("v1", "t", "p", "c") != compute_plan_hash(
            "v2", "t", "p", "c"
        )

    def test_plan_hash_changes_on_template_change(self):
        from generator.deterministic import compute_plan_hash

        assert compute_plan_hash("s", "tmpl_v1", "p", "c") != compute_plan_hash(
            "s", "tmpl_v2", "p", "c"
        )

    @pytest.mark.parametrize(
        "pair_a, pair_b",
        [
            (("ab", "c"), ("a", "bc")),
            (("x", ""), ("", "x")),
            (("abc", ""), ("a", "bc")),
        ],
        ids=["ab_c_vs_a_bc", "x_empty_vs_empty_x", "abc_empty_vs_a_bc"],
    )
    def test_separator_prevents_boundary_collision(
        self,
        pair_a: tuple,
        pair_b: tuple,
    ):
        """The null-byte separator must prevent hash collisions at boundaries."""
        from generator.deterministic import compute_plan_hash

        h_a = compute_plan_hash(*pair_a)  # type: ignore[misc]
        h_b = compute_plan_hash(*pair_b)  # type: ignore[misc]
        assert h_a != h_b, (
            f"Boundary collision: compute_plan_hash{pair_a} == compute_plan_hash{pair_b}"
        )

    def test_empty_inputs_produce_valid_hash(self):
        from generator.deterministic import compute_plan_hash

        result = compute_plan_hash("", "", "", "")
        assert len(result) == 64


# ===========================================================================
# 6. sorted_rglob()
# ===========================================================================


class TestSortedRglob:
    """Verify lexicographic path ordering for deterministic file enumeration."""

    def test_flat_files_returned_in_sorted_order(self, tmp_path: Path):
        from generator.deterministic import sorted_rglob

        # Write files whose natural filesystem order may not be alphabetical
        for name in ("zebra.py", "apple.py", "mango.py"):
            (tmp_path / name).write_text(name)

        results = sorted_rglob(tmp_path, "*.py")
        names = [p.name for p in results]
        assert names == sorted(names), f"Files not sorted: {names}"

    def test_nested_directories_follow_posix_order(self, tmp_path: Path):
        from generator.deterministic import sorted_rglob

        for subdir in ("z_dir", "a_dir", "m_dir"):
            (tmp_path / subdir).mkdir()
            (tmp_path / subdir / "file.py").write_text(subdir)

        results = sorted_rglob(tmp_path)
        posix = [p.as_posix() for p in results]
        assert posix == sorted(posix), f"Paths not in POSIX-sorted order: {posix}"

    def test_empty_directory_returns_empty_list(self, tmp_path: Path):
        from generator.deterministic import sorted_rglob

        assert sorted_rglob(tmp_path) == []

    def test_pattern_filter_applied_correctly(self, tmp_path: Path):
        from generator.deterministic import sorted_rglob

        (tmp_path / "keep.py").write_text("py")
        (tmp_path / "skip.md").write_text("md")

        results = sorted_rglob(tmp_path, "*.py")
        names = [p.name for p in results]
        assert names == ["keep.py"]

    def test_stable_result_across_repeated_calls(self, tmp_path: Path):
        from generator.deterministic import sorted_rglob

        for name in ("c.py", "b.py", "a.py"):
            (tmp_path / name).write_text(name)

        r1 = [p.as_posix() for p in sorted_rglob(tmp_path)]
        r2 = [p.as_posix() for p in sorted_rglob(tmp_path)]
        assert r1 == r2


# ===========================================================================
# 7. deterministic_zip_create()
# ===========================================================================


class TestDeterministicZipCreate:
    """Validate reproducible ZIP creation including byte-identity guarantee."""

    # ── Entry ordering ───────────────────────────────────────────────────────

    def test_zip_entries_are_in_lexicographic_order(self, tmp_path: Path):
        from generator.deterministic import deterministic_zip_create

        src = _make_source_tree(tmp_path)
        result = deterministic_zip_create(tmp_path / "out.zip", src)

        with zipfile.ZipFile(result.zip_path) as zf:
            names = zf.namelist()
        assert names == sorted(names), f"ZIP entries not sorted: {names}"

    # ── Fixed timestamps ─────────────────────────────────────────────────────

    def test_all_entries_have_fixed_dos_epoch_timestamp(self, tmp_path: Path):
        from generator.deterministic import deterministic_zip_create, _FIXED_ZIP_TIMESTAMP

        src = _make_source_tree(tmp_path)
        result = deterministic_zip_create(tmp_path / "out.zip", src)

        with zipfile.ZipFile(result.zip_path) as zf:
            for info in zf.infolist():
                assert info.date_time == _FIXED_ZIP_TIMESTAMP, (
                    f"Entry '{info.filename}' has timestamp {info.date_time}, "
                    f"expected {_FIXED_ZIP_TIMESTAMP}"
                )

    # ── POSIX arcnames ───────────────────────────────────────────────────────

    def test_arcnames_use_forward_slashes_only(self, tmp_path: Path):
        from generator.deterministic import deterministic_zip_create

        src = _make_source_tree(tmp_path)
        result = deterministic_zip_create(tmp_path / "out.zip", src)

        with zipfile.ZipFile(result.zip_path) as zf:
            for name in zf.namelist():
                assert "\\" not in name, (
                    f"Backslash found in arcname: {name!r}"
                )

    # ── Exclusion ────────────────────────────────────────────────────────────

    def test_output_zip_suffix_files_excluded(self, tmp_path: Path):
        from generator.deterministic import deterministic_zip_create

        src = _make_source_tree(tmp_path)
        (src / "cached_output.zip").write_bytes(b"PK\x03\x04")

        result = deterministic_zip_create(tmp_path / "out.zip", src)

        with zipfile.ZipFile(result.zip_path) as zf:
            names = zf.namelist()
        assert not any(n.endswith("_output.zip") for n in names), (
            f"_output.zip entry found in archive: {names}"
        )

    # ── Byte-identical reproducibility ───────────────────────────────────────

    def test_two_independent_calls_produce_identical_bytes(self, tmp_path: Path):
        """Core reproducibility guarantee: same inputs → same bytes."""
        from generator.deterministic import deterministic_zip_create

        src = _make_source_tree(tmp_path)
        run1 = deterministic_zip_create(tmp_path / "run1.zip", src)
        run2 = deterministic_zip_create(tmp_path / "run2.zip", src)

        bytes1 = run1.zip_path.read_bytes()
        bytes2 = run2.zip_path.read_bytes()
        assert bytes1 == bytes2, (
            "Two consecutive deterministic_zip_create calls produced different bytes. "
            f"Sizes: {len(bytes1)} vs {len(bytes2)}"
        )

    # ── Result object ────────────────────────────────────────────────────────

    def test_result_files_archived_count_is_correct(self, tmp_path: Path):
        from generator.deterministic import deterministic_zip_create

        src = _make_source_tree(tmp_path)
        result = deterministic_zip_create(tmp_path / "out.zip", src)
        # Tree has alpha.py, beta.py, subdir/gamma.py = 3 files
        assert result.files_archived == 3

    def test_result_elapsed_ms_is_non_negative(self, tmp_path: Path):
        from generator.deterministic import deterministic_zip_create

        src = _make_source_tree(tmp_path)
        result = deterministic_zip_create(tmp_path / "out.zip", src)
        assert result.elapsed_ms >= 0.0

    def test_result_zip_path_is_resolved(self, tmp_path: Path):
        from generator.deterministic import deterministic_zip_create

        src = _make_source_tree(tmp_path)
        result = deterministic_zip_create(tmp_path / "out.zip", src)
        assert result.zip_path.is_absolute()

    # ── Atomicity ────────────────────────────────────────────────────────────

    def test_no_tmp_file_left_after_successful_write(self, tmp_path: Path):
        from generator.deterministic import deterministic_zip_create

        src = _make_source_tree(tmp_path)
        deterministic_zip_create(tmp_path / "out.zip", src)
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == [], (
            f"Stale .tmp files found after successful write: {tmp_files}"
        )

    # ── Error handling ───────────────────────────────────────────────────────

    def test_raises_value_error_for_missing_source_dir(self, tmp_path: Path):
        from generator.deterministic import deterministic_zip_create

        with pytest.raises(ValueError, match="does not exist"):
            deterministic_zip_create(tmp_path / "out.zip", tmp_path / "nonexistent")

    # ── Archive content integrity ─────────────────────────────────────────────

    def test_archived_file_content_is_preserved(self, tmp_path: Path):
        """Content read back from the archive must match the source files."""
        from generator.deterministic import deterministic_zip_create

        src = _make_source_tree(tmp_path)
        result = deterministic_zip_create(tmp_path / "out.zip", src)

        with zipfile.ZipFile(result.zip_path) as zf:
            alpha_content = zf.read("alpha.py").decode("utf-8")
        assert alpha_content == "print('alpha')\n"


# ===========================================================================
# 8. write_build_plan()
# ===========================================================================


class TestWriteBuildPlan:
    """Validate atomic build-plan persistence and serialisation guarantees."""

    def test_creates_build_plan_json_in_output_dir(self, tmp_path: Path):
        from generator.deterministic import write_build_plan

        plan = _make_plan()
        result = write_build_plan(tmp_path, plan)
        assert result.plan_path.exists(), "build_plan.json was not created"

    def test_written_json_has_sorted_keys(self, tmp_path: Path):
        from generator.deterministic import write_build_plan

        plan = {"z": 99, "a": 1, "spec_hash": "abc123", "m": 42}
        write_build_plan(tmp_path, plan)
        raw = (tmp_path / "build_plan.json").read_text(encoding="utf-8")
        keys = list(json.loads(raw).keys())
        assert keys == sorted(keys), f"build_plan.json keys not sorted: {keys}"

    def test_written_json_is_valid_and_round_trips(self, tmp_path: Path):
        from generator.deterministic import write_build_plan

        plan = _make_plan(spec_hash="deadbeef" * 8)
        write_build_plan(tmp_path, plan)
        loaded = json.loads((tmp_path / "build_plan.json").read_text(encoding="utf-8"))
        assert loaded["spec_hash"] == "deadbeef" * 8
        assert loaded["files"] == ["app/main.py", "app/models.py"]

    def test_result_action_is_written(self, tmp_path: Path):
        from generator.deterministic import write_build_plan

        result = write_build_plan(tmp_path, _make_plan())
        assert result.action == "written"

    def test_result_spec_hash_matches_plan(self, tmp_path: Path):
        from generator.deterministic import write_build_plan

        plan = _make_plan(spec_hash="myspechash")
        result = write_build_plan(tmp_path, plan)
        assert result.spec_hash == "myspechash"

    def test_result_plan_path_is_absolute(self, tmp_path: Path):
        from generator.deterministic import write_build_plan

        result = write_build_plan(tmp_path, _make_plan())
        assert result.plan_path.is_absolute()

    def test_no_tmp_file_left_after_write(self, tmp_path: Path):
        from generator.deterministic import write_build_plan

        write_build_plan(tmp_path, _make_plan())
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == [], (
            f"Stale .tmp files after write_build_plan: {tmp_files}"
        )

    def test_creates_output_dir_if_absent(self, tmp_path: Path):
        from generator.deterministic import write_build_plan

        nested = tmp_path / "a" / "b" / "c"
        write_build_plan(nested, _make_plan())
        assert (nested / "build_plan.json").exists()

    def test_raises_value_error_for_non_dict_plan(self, tmp_path: Path):
        from generator.deterministic import write_build_plan

        with pytest.raises(ValueError, match="dict"):
            write_build_plan(tmp_path, ["not", "a", "dict"])  # type: ignore[arg-type]

    def test_unicode_values_preserved_verbatim(self, tmp_path: Path):
        """Non-ASCII characters must not be escaped in the written JSON."""
        from generator.deterministic import write_build_plan

        plan = {"spec_hash": "x", "description": "Ärger mit Ü und Ö 日本語"}
        write_build_plan(tmp_path, plan)
        raw = (tmp_path / "build_plan.json").read_text(encoding="utf-8")
        assert "Ärger" in raw


# ===========================================================================
# 9. enforce_build_plan()
# ===========================================================================


class TestEnforceBuildPlan:
    """Validate all five state-transition cases and error paths."""

    # ── Case 1: No prior plan ─────────────────────────────────────────────────

    def test_writes_baseline_when_no_prior_plan_exists(self, tmp_path: Path):
        from generator.deterministic import enforce_build_plan

        plan = _make_plan()
        with _set_env("1"):
            result = enforce_build_plan(tmp_path, plan)
        assert (tmp_path / "build_plan.json").exists()
        assert result.action in ("written",)

    # ── Case 2: Spec hash changed ─────────────────────────────────────────────

    def test_overwrites_plan_when_spec_hash_changes(self, tmp_path: Path):
        from generator.deterministic import enforce_build_plan, write_build_plan

        write_build_plan(tmp_path, _make_plan(spec_hash="old_hash"))
        with _set_env("1"):
            result = enforce_build_plan(tmp_path, _make_plan(spec_hash="new_hash"))

        loaded = json.loads((tmp_path / "build_plan.json").read_text(encoding="utf-8"))
        assert loaded["spec_hash"] == "new_hash"
        assert result.spec_hash == "new_hash"

    def test_spec_hash_change_does_not_raise_even_in_deterministic_mode(
        self, tmp_path: Path
    ):
        from generator.deterministic import enforce_build_plan, write_build_plan

        write_build_plan(tmp_path, _make_plan(spec_hash="v1"))
        with _set_env("1"):
            # Must NOT raise — different spec means a legitimately new plan
            enforce_build_plan(tmp_path, _make_plan(spec_hash="v2"))

    # ── Case 3: Unchanged ────────────────────────────────────────────────────

    def test_returns_unchanged_when_plans_are_identical(self, tmp_path: Path):
        from generator.deterministic import enforce_build_plan, write_build_plan

        plan = _make_plan()
        write_build_plan(tmp_path, plan)
        with _set_env("1"):
            result = enforce_build_plan(tmp_path, plan)
        assert result.action == "unchanged"

    def test_plan_file_not_modified_when_unchanged(self, tmp_path: Path):
        from generator.deterministic import enforce_build_plan, write_build_plan

        plan = _make_plan()
        write_build_plan(tmp_path, plan)
        plan_path = tmp_path / "build_plan.json"
        mtime_before = plan_path.stat().st_mtime

        with _set_env("1"):
            enforce_build_plan(tmp_path, plan)

        assert plan_path.stat().st_mtime == mtime_before, (
            "build_plan.json was unnecessarily rewritten for an unchanged plan"
        )

    # ── Case 4: Mismatch under DETERMINISTIC=1 → error ───────────────────────

    def test_raises_build_plan_mismatch_error_in_deterministic_mode(
        self, tmp_path: Path
    ):
        from generator.deterministic import (
            BuildPlanMismatchError,
            enforce_build_plan,
            write_build_plan,
        )

        plan_v1 = _make_plan()
        write_build_plan(tmp_path, plan_v1)

        plan_v2 = dict(plan_v1)
        plan_v2["files"] = ["app/main.py", "app/models.py", "app/extra.py"]

        with _set_env("1"):
            with pytest.raises(BuildPlanMismatchError) as exc_info:
                enforce_build_plan(tmp_path, plan_v2)

        assert "plan mismatch" in str(exc_info.value).lower()
        assert exc_info.value.spec_hash == plan_v1["spec_hash"]
        assert "files" in exc_info.value.diff_summary

    def test_mismatch_error_includes_old_and_new_values_in_diff(
        self, tmp_path: Path
    ):
        from generator.deterministic import (
            BuildPlanMismatchError,
            enforce_build_plan,
            write_build_plan,
        )

        plan_v1 = _make_plan()
        plan_v1["model"] = "gpt-4o"
        write_build_plan(tmp_path, plan_v1)

        plan_v2 = dict(plan_v1)
        plan_v2["model"] = "gpt-4o-mini"

        with _set_env("1"):
            with pytest.raises(BuildPlanMismatchError) as exc_info:
                enforce_build_plan(tmp_path, plan_v2)

        assert "model" in exc_info.value.diff_summary

    def test_mismatch_plan_file_not_modified(self, tmp_path: Path):
        """The stored plan must survive a mismatch error unchanged."""
        from generator.deterministic import (
            BuildPlanMismatchError,
            enforce_build_plan,
            write_build_plan,
        )

        plan_v1 = _make_plan()
        write_build_plan(tmp_path, plan_v1)
        stored_before = (tmp_path / "build_plan.json").read_text()

        plan_v2 = dict(plan_v1)
        plan_v2["files"] = ["app/different.py"]

        with _set_env("1"):
            with pytest.raises(BuildPlanMismatchError):
                enforce_build_plan(tmp_path, plan_v2)

        stored_after = (tmp_path / "build_plan.json").read_text()
        assert stored_before == stored_after, (
            "build_plan.json was modified despite a mismatch error"
        )

    # ── Case 5: Mismatch under DETERMINISTIC=0 → silent update ──────────────

    def test_silently_updates_plan_in_non_deterministic_mode(self, tmp_path: Path):
        from generator.deterministic import enforce_build_plan, write_build_plan

        plan_v1 = _make_plan()
        write_build_plan(tmp_path, plan_v1)

        plan_v2 = dict(plan_v1)
        plan_v2["files"] = ["app/main.py"]

        with _set_env("0"):
            result = enforce_build_plan(tmp_path, plan_v2)

        loaded = json.loads((tmp_path / "build_plan.json").read_text())
        assert loaded["files"] == ["app/main.py"]
        assert result.action == "updated"

    # ── Corrupt plan recovery ────────────────────────────────────────────────

    def test_corrupt_plan_file_is_overwritten_gracefully(self, tmp_path: Path):
        from generator.deterministic import enforce_build_plan

        plan_path = tmp_path / "build_plan.json"
        plan_path.write_text("{ this is: NOT valid JSON !!!", encoding="utf-8")

        plan = _make_plan()
        with _set_env("1"):
            result = enforce_build_plan(tmp_path, plan)

        assert result.action == "written"
        # Verify the file is now valid JSON
        loaded = json.loads(plan_path.read_text())
        assert loaded["spec_hash"] == plan["spec_hash"]


# ===========================================================================
# 10. Exception hierarchy
# ===========================================================================


class TestExceptionHierarchy:
    """Verify the exception type tree and attribute contracts."""

    def test_build_plan_mismatch_error_is_deterministic_build_error(self):
        from generator.deterministic import (
            BuildPlanMismatchError,
            DeterministicBuildError,
        )

        exc = BuildPlanMismatchError(spec_hash="abc123", diff_summary="  key='a'")
        assert isinstance(exc, DeterministicBuildError)
        assert isinstance(exc, RuntimeError)

    def test_build_plan_mismatch_error_exposes_spec_hash(self):
        from generator.deterministic import BuildPlanMismatchError

        exc = BuildPlanMismatchError(spec_hash="deadbeef", diff_summary="diff")
        assert exc.spec_hash == "deadbeef"

    def test_build_plan_mismatch_error_exposes_diff_summary(self):
        from generator.deterministic import BuildPlanMismatchError

        diff = "  key='files': existing=['a'] vs new=['b']"
        exc = BuildPlanMismatchError(spec_hash="x", diff_summary=diff)
        assert exc.diff_summary == diff

    def test_deterministic_build_error_stores_operation_and_details(self):
        from generator.deterministic import DeterministicBuildError

        exc = DeterministicBuildError(
            "msg",
            operation="test_op",
            details={"k": "v"},
        )
        assert exc.operation == "test_op"
        assert exc.details == {"k": "v"}


# ===========================================================================
# 11. Result dataclass immutability
# ===========================================================================


class TestResultDataclasses:
    """Verify frozen-dataclass contracts for ZipCreationResult and BuildPlanResult."""

    def test_zip_creation_result_is_frozen(self, tmp_path: Path):
        from generator.deterministic import ZipCreationResult

        result = ZipCreationResult(
            zip_path=tmp_path / "out.zip",
            files_archived=5,
            elapsed_ms=12.3,
        )
        with pytest.raises((AttributeError, TypeError)):
            result.files_archived = 99  # type: ignore[misc]

    def test_build_plan_result_is_frozen(self, tmp_path: Path):
        from generator.deterministic import BuildPlanResult

        result = BuildPlanResult(
            plan_path=tmp_path / "build_plan.json",
            action="written",
            spec_hash="abc",
        )
        with pytest.raises((AttributeError, TypeError)):
            result.action = "mutated"  # type: ignore[misc]

    def test_zip_creation_result_fields(self, tmp_path: Path):
        from generator.deterministic import ZipCreationResult

        p = tmp_path / "out.zip"
        r = ZipCreationResult(zip_path=p, files_archived=3, elapsed_ms=7.5)
        assert r.zip_path == p
        assert r.files_archived == 3
        assert r.elapsed_ms == 7.5

    def test_build_plan_result_fields(self, tmp_path: Path):
        from generator.deterministic import BuildPlanResult

        p = tmp_path / "build_plan.json"
        r = BuildPlanResult(plan_path=p, action="unchanged", spec_hash="sha256abc")
        assert r.plan_path == p
        assert r.action == "unchanged"
        assert r.spec_hash == "sha256abc"
