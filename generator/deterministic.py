# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# generator/deterministic.py
"""
Deterministic Build Mode — Core Infrastructure Module.

This module is the single authoritative source for all determinism guarantees
in the Code Factory generation pipeline.  When the ``DETERMINISTIC=1``
environment flag is active, every subsystem that imports from this module
participates in a reproducible build contract:

    same MD spec  +  same config  +  same templates  →  byte-identical ZIP

Features
--------
- ``DETERMINISTIC=1`` environment flag with typed accessor
- LLM parameter overrides: ``temperature=0``, ``top_p=1``, ``seed=0``
- Content normalisation: CRLF → LF, guaranteed trailing newline
- Stable JSON serialisation: ``sort_keys=True``, ``ensure_ascii=False``
- SHA-256 hashing for spec/template/prompt/config bundles
- Deterministically-sorted directory enumeration (``sorted_rglob``)
- Reproducible ZIP packaging: sorted entries, fixed DOS epoch timestamps,
  forward-slash arcnames, stripped extra metadata
- Build plan lifecycle: atomic ``write_build_plan`` + ``enforce_build_plan``
  that fails CI when the plan diverges under deterministic mode

Observability
-------------
Full OpenTelemetry span instrumentation and Prometheus counter / histogram
emission for all production-path operations, with graceful degradation when
the optional observability libraries are not installed.

Industry Standards Compliance
------------------------------
- ISO 27001 A.12.1.3: Audit logging for build-plan state transitions
- NIST SP 800-53 CM-3: Configuration-change control (plan enforcement)
- SOC 2 Type II: Change management — every plan mismatch is logged and traced
- 12-Factor App (Factor III): config via environment variables only
- SLSA Supply Chain Level 2: reproducible, verifiable build outputs

Architecture
------------
All public helpers are pure functions (no shared mutable state).  The module
is intentionally free of heavy framework dependencies so that it can be
imported safely at the top of any file in the pipeline — including files that
are themselves loaded via ``importlib.util.spec_from_file_location`` in tests.

Usage
-----
::

    DETERMINISTIC=1 python -m generator …

    from generator.deterministic import (
        is_deterministic,
        get_deterministic_llm_params,
        normalize_content,
        deterministic_json_dumps,
        compute_content_hash,
        compute_plan_hash,
        sorted_rglob,
        deterministic_zip_create,
        write_build_plan,
        enforce_build_plan,
    )
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import zipfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Final, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Observability — OpenTelemetry (graceful degradation)
# ---------------------------------------------------------------------------

try:
    from opentelemetry import trace as _otel_trace
    from opentelemetry.trace import Status as _OtelStatus, StatusCode as _OtelStatusCode

    _tracer = _otel_trace.get_tracer(__name__)
    _TRACING_AVAILABLE: bool = True
except ImportError:
    _tracer = None  # type: ignore[assignment]
    _TRACING_AVAILABLE = False
    logger.debug(
        "OpenTelemetry not available — tracing disabled for deterministic build module",
        extra={"module": __name__, "feature": "opentelemetry"},
    )

    class _OtelStatusCode(Enum):  # type: ignore[no-redef]
        OK = 0
        ERROR = 2
        UNSET = 1

    class _OtelStatus:  # type: ignore[no-redef]
        def __init__(self, status_code: _OtelStatusCode, description: str = "") -> None:
            self.status_code = status_code
            self.description = description


# ---------------------------------------------------------------------------
# Observability — Prometheus metrics (graceful degradation)
# ---------------------------------------------------------------------------

try:
    from prometheus_client import Counter, Histogram

    _METRICS_AVAILABLE: bool = True

    _deterministic_zip_created_total = Counter(
        "deterministic_zip_created_total",
        "Total number of deterministic ZIP archives created",
        ["result"],
    )
    _deterministic_zip_duration_seconds = Histogram(
        "deterministic_zip_duration_seconds",
        "Time taken to create a deterministic ZIP archive (seconds)",
        buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 30.0, float("inf")),
    )
    _deterministic_plan_operations_total = Counter(
        "deterministic_plan_operations_total",
        "Total build-plan lifecycle operations (write, enforce, mismatch)",
        ["operation", "result"],
    )
except ImportError:
    _METRICS_AVAILABLE = False
    logger.debug(
        "prometheus_client not available — metrics disabled for deterministic build module",
        extra={"module": __name__, "feature": "prometheus"},
    )


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Fixed DOS-epoch timestamp applied to every ZIP entry in deterministic mode.
#: ``zipfile.ZipInfo.date_time`` accepts a 6-tuple: (year, month, day, H, M, S).
#: The minimum valid value (1980-01-01 00:00:00) eliminates all mtime variance.
_FIXED_ZIP_TIMESTAMP: Final[Tuple[int, int, int, int, int, int]] = (1980, 1, 1, 0, 0, 0)

#: Null-byte separator used between plan-hash components to prevent ``("ab","c")``
#: colliding with ``("a","bc")``.
_HASH_SEPARATOR: Final[str] = "\x00"

#: Environment variable that activates deterministic build mode.
_DETERMINISTIC_ENV_VAR: Final[str] = "DETERMINISTIC"


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class DeterministicBuildError(RuntimeError):
    """Base exception for all deterministic-build violations.

    Raised exclusively under ``DETERMINISTIC=1`` so that callers can
    distinguish enforcement failures from ordinary runtime errors.
    """

    def __init__(
        self,
        message: str,
        *,
        operation: str = "unknown",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.operation = operation
        self.details: Dict[str, Any] = details or {}


class BuildPlanMismatchError(DeterministicBuildError):
    """Raised when an enforced build plan diverges from the stored plan.

    This exception signals a reproducibility violation: the same spec hash
    produced a different plan, which means output files or entity lists have
    changed between runs.  CI must treat this as a hard failure.

    Attributes:
        spec_hash:    The spec SHA-256 hash shared by both plans (first 16 chars).
        diff_summary: Human-readable list of differing plan keys.
    """

    def __init__(
        self,
        spec_hash: str,
        diff_summary: str,
    ) -> None:
        super().__init__(
            f"Deterministic build plan mismatch for spec_hash={spec_hash[:16]}…\n"
            f"Differences:\n{diff_summary}\n"
            "Either update the plan intentionally or fix the non-deterministic input.",
            operation="enforce_build_plan",
            details={"spec_hash": spec_hash, "diff_summary": diff_summary},
        )
        self.spec_hash = spec_hash
        self.diff_summary = diff_summary


# ---------------------------------------------------------------------------
# Structured result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ZipCreationResult:
    """Immutable result returned by :func:`deterministic_zip_create`.

    Attributes:
        zip_path:       Resolved ``Path`` of the created archive.
        files_archived: Number of file entries written to the archive.
        elapsed_ms:     Wall-clock time for the operation (milliseconds).
    """

    zip_path: Path
    files_archived: int
    elapsed_ms: float


@dataclass(frozen=True)
class BuildPlanResult:
    """Immutable result returned by :func:`write_build_plan` and
    :func:`enforce_build_plan`.

    Attributes:
        plan_path:  Resolved ``Path`` of the ``build_plan.json`` file.
        action:     One of ``"written"``, ``"unchanged"``, ``"updated"``.
        spec_hash:  SHA-256 of the spec included in the plan (first 64 chars).
    """

    plan_path: Path
    action: str
    spec_hash: str


# ---------------------------------------------------------------------------
# Core flag
# ---------------------------------------------------------------------------


def is_deterministic() -> bool:
    """Return ``True`` when deterministic build mode is active.

    Reads the ``DETERMINISTIC`` environment variable and returns ``True``
    only when its stripped value equals exactly ``"1"``.  All other values
    (``"true"``, ``"yes"``, ``"on"``, ``"0"``, or an absent variable) yield
    ``False``, preventing accidental activation.

    This function is intentionally cheap (a single ``os.getenv`` call) so
    that it may be called in tight loops without measurable overhead.

    Returns:
        ``True`` iff ``DETERMINISTIC=1`` is set in the process environment.

    Industry Standards:
        - 12-Factor App Factor III: Store configuration in the environment.
          A single well-named flag controls the entire reproducibility contract.

    Example::

        $ DETERMINISTIC=1 python -m generator …

        from generator.deterministic import is_deterministic
        if is_deterministic():
            params = get_deterministic_llm_params()
    """
    return os.getenv(_DETERMINISTIC_ENV_VAR, "0").strip() == "1"


# ---------------------------------------------------------------------------
# LLM parameter helpers
# ---------------------------------------------------------------------------


def get_deterministic_llm_params() -> Dict[str, Any]:
    """Return LLM call kwargs that guarantee fully deterministic sampling.

    When :func:`is_deterministic` is ``True`` this returns::

        {"temperature": 0, "top_p": 1, "seed": 0}

    When deterministic mode is **off** an **empty dict** is returned so that
    call-sites can merge unconditionally without branching::

        kwargs = {**base_kwargs, **get_deterministic_llm_params()}

    The ``seed`` parameter is supported by OpenAI's ``gpt-4o`` and later
    models via the ``seed`` request body field.  Other providers (Grok, Gemini,
    Anthropic) should ignore unknown keys gracefully; callers are responsible
    for stripping unsupported keys before forwarding to provider-specific APIs.

    Returns:
        A dict of LLM parameters enforcing deterministic outputs, or an empty
        dict when deterministic mode is inactive.

    Industry Standards:
        - NIST SP 800-53 CM-7: Principle of least functionality — override
          only what is required for reproducibility, nothing else.
    """
    if not is_deterministic():
        return {}
    return {"temperature": 0, "top_p": 1, "seed": 0}


# ---------------------------------------------------------------------------
# Content normalisation
# ---------------------------------------------------------------------------


def normalize_content(content: Optional[str]) -> Optional[str]:
    """Normalise text file content for reproducible, byte-stable output.

    Applies the following transformations **in order** when
    :func:`is_deterministic` is ``True``:

    1. **CRLF / lone CR → LF**: replaces ``\\r\\n`` and bare ``\\r`` with
       ``\\n`` so that archive members are OS-agnostic.
    2. **Single trailing newline**: strips all trailing newline characters
       and appends exactly one ``\\n``, matching POSIX text-file conventions.

    Non-``str`` inputs (including ``None``) are returned unchanged so that
    callers can pipeline this function over heterogeneous file maps without
    type guards.

    When deterministic mode is **off** the original value is returned
    unmodified, making this function safe to call unconditionally.

    Args:
        content: Text content to normalise, or ``None`` / non-string for
                 passthrough.

    Returns:
        Normalised string when ``content`` is a ``str`` and deterministic
        mode is active; the original value otherwise.

    Industry Standards:
        - POSIX.1-2017 §3.206: Text files must end with a newline character.
        - IEEE Std 1003.1: Consistent line-ending conventions.
    """
    if not is_deterministic():
        return content
    if not isinstance(content, str):
        return content
    # Step 1: normalise line endings
    normalised = content.replace("\r\n", "\n").replace("\r", "\n")
    # Step 2: enforce single trailing newline (POSIX text-file convention)
    normalised = normalised.rstrip("\n") + "\n"
    return normalised


# ---------------------------------------------------------------------------
# JSON serialisation
# ---------------------------------------------------------------------------


def deterministic_json_dumps(obj: Any, indent: Optional[int] = 2) -> str:
    """Serialise *obj* to a stable, reproducible JSON string.

    Guarantees:
    - ``sort_keys=True``: dict key ordering is alphabetical at every nesting
      level, eliminating Python ``dict`` insertion-order variance.
    - ``ensure_ascii=False``: Unicode characters are preserved verbatim
      rather than being escaped as ``\\uXXXX`` sequences, producing smaller
      and more readable output.

    This function is always applied (not gated on :func:`is_deterministic`)
    because sorted-key JSON is strictly safer than unsorted JSON for any
    production artifact.  The gate is the responsibility of the caller when
    context requires it.

    Args:
        obj:    Any JSON-serialisable Python object.
        indent: Indentation level for pretty-printing (``None`` = compact
                single-line output with minimal separators).

    Returns:
        JSON-encoded string with deterministic key ordering.

    Industry Standards:
        - RFC 8785 (JSON Canonicalization Scheme): sorted keys for canonical
          JSON representation used in signed payloads and integrity checks.
    """
    return json.dumps(
        obj,
        indent=indent,
        sort_keys=True,
        ensure_ascii=False,
        separators=None if indent else (",", ":"),
    )


# ---------------------------------------------------------------------------
# Hashing helpers
# ---------------------------------------------------------------------------


def compute_content_hash(content: Union[str, bytes]) -> str:
    """Compute the hex-encoded SHA-256 digest of *content*.

    Provides a stable, collision-resistant fingerprint for any file or
    string-valued artifact.  The digest is lowercase hexadecimal (64 chars)
    conforming to the representation used by ``git`` and most CI tooling.

    Args:
        content: Raw bytes or UTF-8-encoded string to hash.  Strings are
                 encoded as UTF-8 before hashing.

    Returns:
        Lowercase hexadecimal SHA-256 digest (64 characters).

    Raises:
        TypeError: If *content* is neither ``str`` nor ``bytes``.

    Industry Standards:
        - NIST FIPS 180-4: SHA-256 for cryptographic integrity.
        - SOC 2 Type II: Cryptographic integrity verification of build inputs.
    """
    if isinstance(content, str):
        content = content.encode("utf-8")
    elif not isinstance(content, (bytes, bytearray)):
        raise TypeError(
            f"compute_content_hash expects str or bytes, got {type(content).__name__!r}"
        )
    return hashlib.sha256(content).hexdigest()


def compute_plan_hash(
    spec_content: str,
    template_content: str = "",
    prompt_content: str = "",
    config_content: str = "",
) -> str:
    """Compute a SHA-256 hash of the combined deterministic build inputs.

    The four inputs are concatenated with a null-byte separator
    (``\\x00``) so that boundary collisions are impossible:
    ``("ab", "c")`` produces a different hash than ``("a", "bc")``.

    The resulting digest uniquely identifies a (spec, templates, prompts,
    config) tuple and serves as the primary key for ``build_plan.json``
    lookup.

    Args:
        spec_content:     Raw text of the Markdown specification file.
        template_content: Serialised, sorted template bundle string.
        prompt_content:   Serialised, sorted prompt bundle string.
        config_content:   Serialised config / model-parameter string.

    Returns:
        Lowercase hexadecimal SHA-256 digest (64 characters).

    Industry Standards:
        - NIST FIPS 180-4: SHA-256 for build-input integrity.
        - SLSA Supply Chain Level 2: cryptographic attestation of inputs.

    Example::

        hash_ = compute_plan_hash(
            spec_content=spec_path.read_text(),
            config_content=json.dumps(model_cfg, sort_keys=True),
        )
    """
    combined = _HASH_SEPARATOR.join(
        [spec_content, template_content, prompt_content, config_content]
    )
    return compute_content_hash(combined)


# ---------------------------------------------------------------------------
# Sorted file enumeration
# ---------------------------------------------------------------------------


def sorted_rglob(directory: Path, pattern: str = "*") -> List[Path]:
    """Return a deterministically-sorted list of paths matching *pattern*.

    ``Path.rglob()`` and ``os.walk()`` return paths in filesystem-dependent
    order which varies across operating systems (ext4 vs. APFS vs. NTFS),
    kernel versions, and mount options.  This function sorts the results
    lexicographically by their POSIX representation so that downstream
    consumers (ZIP packaging, manifest generation, codegen materialisation)
    always process files in a stable, reproducible order.

    The sort key is ``Path.as_posix()`` rather than ``str(path)`` to ensure
    forward-slash ordering on all platforms including Windows.

    Args:
        directory: Root directory to search from.
        pattern:   Glob pattern forwarded to ``Path.rglob()``
                   (default: ``"*"`` — all files and directories).

    Returns:
        Lexicographically-sorted list of ``Path`` objects matching *pattern*
        within *directory*.

    Raises:
        OSError: If *directory* is not accessible or does not exist.

    Note:
        This function is NOT gated on :func:`is_deterministic`.  Sorted
        directory enumeration is a safe default with no adverse side-effects,
        and applying it unconditionally simplifies call-sites.

    Industry Standards:
        - POSIX.1-2017 §2.2: Portable filename character set and ordering.
    """
    return sorted(directory.rglob(pattern), key=lambda p: p.as_posix())


# ---------------------------------------------------------------------------
# Deterministic ZIP creation
# ---------------------------------------------------------------------------


def deterministic_zip_create(
    zip_path: Union[str, Path],
    source_dir: Path,
    *,
    exclude_suffix: str = "_output.zip",
) -> ZipCreationResult:
    """Create a fully reproducible ZIP archive from *source_dir*.

    Reproducibility guarantees (applied regardless of :func:`is_deterministic`
    so that callers obtain a stable archive in both modes):

    * **Sorted entry order** — files are archived in POSIX-string lexicographic
      order, eliminating filesystem-traversal variance.
    * **Fixed timestamps** — every ``ZipInfo`` entry uses
      :data:`_FIXED_ZIP_TIMESTAMP` ``(1980, 1, 1, 0, 0, 0)``, the minimum
      valid DOS timestamp, so ``mtime`` differences cannot influence the
      archive bytes.
    * **Forward-slash arcnames** — relative paths always use ``/`` as the
      separator regardless of the host OS, conforming to the ZIP specification
      (APPNOTE.TXT §4.4.17).
    * **Stripped extra metadata** — ``ZipInfo.extra`` is cleared for every
      entry, preventing platform-specific extended attributes from leaking
      into the archive.
    * **Consistent compression** — ``ZIP_DEFLATED`` throughout.

    The archive is written to a temporary ``.tmp`` sibling and atomically
    renamed to *zip_path* on completion, preventing partial/corrupt archives
    in the event of an error mid-write.

    Args:
        zip_path:       Destination ZIP file path.  Created or overwritten.
        source_dir:     Root directory whose contents are archived.  Only
                        files that are direct or indirect children of this
                        directory are included.
        exclude_suffix: Files whose name ends with this suffix are excluded
                        (default: ``"_output.zip"`` to prevent nested ZIPs
                        from being re-archived on repeat downloads).

    Returns:
        :class:`ZipCreationResult` with the resolved path, file count, and
        elapsed time.

    Raises:
        OSError:    If *source_dir* is not accessible or *zip_path* cannot
                    be created.
        ValueError: If *source_dir* does not exist.

    Industry Standards:
        - ISO/IEC 21320-1: ZIP file format standard (DEFLATE compression).
        - SLSA Supply Chain Level 2: reproducible, bit-for-bit-identical
          build outputs for the same inputs.
        - NIST SP 800-53 SI-7: Software and information integrity protection.

    Example::

        result = deterministic_zip_create(
            zip_path=output_dir / f"job_{job_id}_files.zip",
            source_dir=job_dir,
        )
        logger.info(
            "Archived %d files in %.1f ms",
            result.files_archived,
            result.elapsed_ms,
        )
    """
    start_time = time.monotonic()
    zip_path = Path(zip_path)

    if not source_dir.exists():
        raise ValueError(
            f"deterministic_zip_create: source_dir does not exist: {source_dir}"
        )

    # Use an atomic write: write to .tmp then rename to avoid partial archives.
    tmp_path = zip_path.with_suffix(zip_path.suffix + ".tmp")

    span = None
    if _TRACING_AVAILABLE and _tracer:
        span = _tracer.start_span("deterministic_zip_create")
        span.set_attribute("zip_path", str(zip_path))
        span.set_attribute("source_dir", str(source_dir))

    files_archived = 0
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in sorted_rglob(source_dir):
                if not file_path.is_file():
                    continue
                if exclude_suffix and file_path.name.endswith(exclude_suffix):
                    logger.debug(
                        "deterministic_zip_create: excluding %s (suffix=%s)",
                        file_path.name,
                        exclude_suffix,
                        extra={
                            "action": "deterministic_zip_create",
                            "excluded_file": str(file_path),
                            "exclude_suffix": exclude_suffix,
                        },
                    )
                    continue

                # POSIX arcname — always forward slashes, no leading ./
                arcname = file_path.relative_to(source_dir).as_posix()

                zi = zipfile.ZipInfo(filename=arcname, date_time=_FIXED_ZIP_TIMESTAMP)
                zi.compress_type = zipfile.ZIP_DEFLATED
                zi.extra = b""  # Strip platform-specific extended attributes

                with file_path.open("rb") as fh:
                    zf.writestr(zi, fh.read())
                files_archived += 1

        # Atomic rename — only replaces the target after successful write
        tmp_path.replace(zip_path)

        elapsed_ms = (time.monotonic() - start_time) * 1000

        logger.info(
            "deterministic_zip_create: archived %d file(s) to %s in %.1f ms",
            files_archived,
            zip_path,
            elapsed_ms,
            extra={
                "action": "deterministic_zip_create",
                "result": "success",
                "zip_path": str(zip_path),
                "source_dir": str(source_dir),
                "files_archived": files_archived,
                "elapsed_ms": round(elapsed_ms, 1),
            },
        )

        if _METRICS_AVAILABLE:
            _deterministic_zip_created_total.labels(result="success").inc()
            _deterministic_zip_duration_seconds.observe(elapsed_ms / 1000)

        if span:
            span.set_attribute("files_archived", files_archived)
            span.set_attribute("elapsed_ms", elapsed_ms)
            span.set_status(_OtelStatus(_OtelStatusCode.OK))

        return ZipCreationResult(
            zip_path=zip_path.resolve(),
            files_archived=files_archived,
            elapsed_ms=elapsed_ms,
        )

    except Exception as exc:
        # Clean up the partial .tmp file on failure
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass

        elapsed_ms = (time.monotonic() - start_time) * 1000
        logger.error(
            "deterministic_zip_create: failed after %.1f ms — %s",
            elapsed_ms,
            exc,
            exc_info=True,
            extra={
                "action": "deterministic_zip_create",
                "result": "error",
                "zip_path": str(zip_path),
                "source_dir": str(source_dir),
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            },
        )

        if _METRICS_AVAILABLE:
            _deterministic_zip_created_total.labels(result="error").inc()

        if span:
            span.set_status(_OtelStatus(_OtelStatusCode.ERROR, str(exc)))
            span.record_exception(exc)

        raise

    finally:
        if span:
            span.end()


# ---------------------------------------------------------------------------
# Build plan lifecycle
# ---------------------------------------------------------------------------


def write_build_plan(
    output_dir: Path,
    plan: Dict[str, Any],
) -> BuildPlanResult:
    """Write *plan* as ``build_plan.json`` inside *output_dir*.

    The plan is serialised with ``sort_keys=True`` and ``ensure_ascii=False``
    so that the persisted file is byte-identical for logically equivalent
    plans regardless of dict-insertion order.

    The write is atomic: content is first written to a ``.tmp`` sibling then
    renamed to ``build_plan.json``, preventing partially-written plan files
    from being consumed by :func:`enforce_build_plan`.

    Args:
        output_dir: Directory in which ``build_plan.json`` is created.  Will
                    be created (including parents) if absent.
        plan:       Arbitrary JSON-serialisable dict representing the build
                    plan.  Must contain a ``"spec_hash"`` key for
                    :func:`enforce_build_plan` to function correctly.

    Returns:
        :class:`BuildPlanResult` with the resolved plan path, action
        ``"written"``, and the plan's ``spec_hash`` (or empty string).

    Raises:
        OSError:        If the plan file cannot be written.
        TypeError:      If *plan* contains non-JSON-serialisable values.
        ValueError:     If *plan* is not a dict.

    Industry Standards:
        - NIST SP 800-53 CM-3: Configuration-change control — every build
          plan write is logged with a structured audit record.
        - SOC 2 Type II: Change management — atomic writes prevent
          inconsistent plan state.

    Example::

        plan_result = write_build_plan(
            output_dir=Path(f"./uploads/{job_id}"),
            plan={
                "spec_hash": compute_plan_hash(spec_text),
                "model": "gpt-4o",
                "files": sorted(file_list),
            },
        )
    """
    if not isinstance(plan, dict):
        raise ValueError(
            f"write_build_plan: plan must be a dict, got {type(plan).__name__!r}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    plan_path = output_dir / "build_plan.json"
    tmp_path = output_dir / "build_plan.json.tmp"

    spec_hash: str = plan.get("spec_hash", "")

    content = json.dumps(plan, indent=2, sort_keys=True, ensure_ascii=False)
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(plan_path)

    logger.info(
        "write_build_plan: plan written to %s (spec_hash=%s…)",
        plan_path,
        spec_hash[:16] if spec_hash else "(none)",
        extra={
            "action": "write_build_plan",
            "result": "written",
            "plan_path": str(plan_path),
            "spec_hash": spec_hash,
            "plan_keys": sorted(plan.keys()),
        },
    )

    if _METRICS_AVAILABLE:
        _deterministic_plan_operations_total.labels(
            operation="write", result="written"
        ).inc()

    return BuildPlanResult(
        plan_path=plan_path.resolve(),
        action="written",
        spec_hash=spec_hash,
    )


def enforce_build_plan(
    output_dir: Path,
    new_plan: Dict[str, Any],
) -> BuildPlanResult:
    """Enforce that *new_plan* matches the existing ``build_plan.json``.

    This function implements the deterministic-mode plan gate:

    1. **No prior plan** → write *new_plan* as the baseline (first run).
    2. **Spec hash changed** → write *new_plan* and log the transition
       (spec was updated intentionally).
    3. **Same spec hash, identical content** → no-op, returns ``"unchanged"``.
    4. **Same spec hash, different content** (deterministic mode **on**) →
       raise :class:`BuildPlanMismatchError` with a human-readable diff so
       that the CI job fails loudly.
    5. **Same spec hash, different content** (deterministic mode **off**) →
       silently overwrite with *new_plan* and return ``"updated"``.

    The diff reported in case 4 lists every top-level key where the existing
    and new plan values differ, making it straightforward to identify which
    part of the pipeline introduced non-determinism.

    Args:
        output_dir: Directory containing (or to receive) ``build_plan.json``.
        new_plan:   The newly-computed build plan dict.

    Returns:
        :class:`BuildPlanResult` with action ``"written"``, ``"unchanged"``,
        or ``"updated"``.

    Raises:
        BuildPlanMismatchError: When deterministic mode is active and the
            plan content diverges for the same spec hash (case 4).
        OSError:                If *output_dir* is inaccessible.
        ValueError:             If *new_plan* is not a dict.

    Industry Standards:
        - NIST SP 800-53 CM-3: Configuration-change control — detected
          drifts are logged and, under deterministic mode, halted.
        - SOC 2 Type II: Audit trails for plan state transitions.
        - SLSA Supply Chain Level 2: enforcement of reproducible build plans.

    Example::

        try:
            result = enforce_build_plan(job_output_dir, plan)
        except BuildPlanMismatchError as exc:
            logger.error("Reproducibility violation: %s", exc)
            raise
    """
    if not isinstance(new_plan, dict):
        raise ValueError(
            f"enforce_build_plan: new_plan must be a dict, got {type(new_plan).__name__!r}"
        )

    plan_path = output_dir / "build_plan.json"
    new_spec_hash: str = new_plan.get("spec_hash", "")

    # ── Case 1: No prior plan → write baseline ───────────────────────────────
    if not plan_path.exists():
        logger.info(
            "enforce_build_plan: no prior plan found, writing baseline "
            "(spec_hash=%s…)",
            new_spec_hash[:16] if new_spec_hash else "(none)",
            extra={
                "action": "enforce_build_plan",
                "result": "baseline_written",
                "spec_hash": new_spec_hash,
            },
        )
        if _METRICS_AVAILABLE:
            _deterministic_plan_operations_total.labels(
                operation="enforce", result="baseline_written"
            ).inc()
        return write_build_plan(output_dir, new_plan)

    # ── Load existing plan ────────────────────────────────────────────────────
    try:
        existing_plan: Dict[str, Any] = json.loads(
            plan_path.read_text(encoding="utf-8")
        )
    except Exception as exc:
        logger.warning(
            "enforce_build_plan: could not parse existing plan (%s), "
            "overwriting with new plan",
            exc,
            extra={
                "action": "enforce_build_plan",
                "result": "corrupt_plan_overwritten",
                "spec_hash": new_spec_hash,
                "error": str(exc),
            },
        )
        if _METRICS_AVAILABLE:
            _deterministic_plan_operations_total.labels(
                operation="enforce", result="corrupt_overwritten"
            ).inc()
        return write_build_plan(output_dir, new_plan)

    existing_spec_hash: str = existing_plan.get("spec_hash", "")

    # ── Case 2: Spec hash changed → write new plan ────────────────────────────
    if existing_spec_hash != new_spec_hash:
        logger.info(
            "enforce_build_plan: spec hash changed (%s… → %s…), writing new plan",
            existing_spec_hash[:12],
            new_spec_hash[:12],
            extra={
                "action": "enforce_build_plan",
                "result": "spec_changed",
                "old_spec_hash": existing_spec_hash,
                "new_spec_hash": new_spec_hash,
            },
        )
        if _METRICS_AVAILABLE:
            _deterministic_plan_operations_total.labels(
                operation="enforce", result="spec_changed"
            ).inc()
        return write_build_plan(output_dir, new_plan)

    # ── Compare serialised forms (sort_keys for stable comparison) ───────────
    existing_serialised = json.dumps(existing_plan, sort_keys=True, ensure_ascii=False)
    new_serialised = json.dumps(new_plan, sort_keys=True, ensure_ascii=False)

    # ── Case 3: Identical plans → no-op ──────────────────────────────────────
    if existing_serialised == new_serialised:
        logger.debug(
            "enforce_build_plan: plans are identical, no update needed "
            "(spec_hash=%s…)",
            new_spec_hash[:16],
            extra={
                "action": "enforce_build_plan",
                "result": "unchanged",
                "spec_hash": new_spec_hash,
            },
        )
        if _METRICS_AVAILABLE:
            _deterministic_plan_operations_total.labels(
                operation="enforce", result="unchanged"
            ).inc()
        return BuildPlanResult(
            plan_path=plan_path.resolve(),
            action="unchanged",
            spec_hash=new_spec_hash,
        )

    # ── Plans differ for the same spec hash ──────────────────────────────────
    # Build a human-readable diff over top-level keys.
    all_keys = sorted(set(existing_plan) | set(new_plan))
    diff_lines: List[str] = []
    for key in all_keys:
        old_val = existing_plan.get(key)
        new_val = new_plan.get(key)
        if old_val != new_val:
            diff_lines.append(
                f"  key={key!r}:\n"
                f"    existing = {json.dumps(old_val, sort_keys=True, ensure_ascii=False)}\n"
                f"    new      = {json.dumps(new_val, sort_keys=True, ensure_ascii=False)}"
            )
    diff_summary = "\n".join(diff_lines) or "  (binary / unknown difference)"

    # ── Case 4: Mismatch under deterministic mode → hard failure ─────────────
    if is_deterministic():
        logger.error(
            "enforce_build_plan: DETERMINISTIC BUILD PLAN MISMATCH — "
            "spec_hash=%s… differs from stored plan\n%s",
            new_spec_hash[:16],
            diff_summary,
            extra={
                "action": "enforce_build_plan",
                "result": "mismatch_error",
                "spec_hash": new_spec_hash,
                "diff_summary": diff_summary,
            },
        )
        if _METRICS_AVAILABLE:
            _deterministic_plan_operations_total.labels(
                operation="enforce", result="mismatch_error"
            ).inc()
        raise BuildPlanMismatchError(
            spec_hash=new_spec_hash,
            diff_summary=diff_summary,
        )

    # ── Case 5: Mismatch in non-deterministic mode → silent update ───────────
    logger.info(
        "enforce_build_plan: plan content changed for spec_hash=%s… "
        "(non-deterministic mode), updating plan\n%s",
        new_spec_hash[:16],
        diff_summary,
        extra={
            "action": "enforce_build_plan",
            "result": "updated",
            "spec_hash": new_spec_hash,
            "diff_summary": diff_summary,
        },
    )
    if _METRICS_AVAILABLE:
        _deterministic_plan_operations_total.labels(
            operation="enforce", result="updated"
        ).inc()
    result = write_build_plan(output_dir, new_plan)
    return BuildPlanResult(
        plan_path=result.plan_path,
        action="updated",
        spec_hash=new_spec_hash,
    )
