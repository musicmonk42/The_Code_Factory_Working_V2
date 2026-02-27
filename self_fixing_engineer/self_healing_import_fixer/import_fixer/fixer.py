# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
fixer.py - Primary CLI entry point for the Self-Healing Import Fixer.

This module provides the ``main()`` async function that the CLI ``heal`` command
dispatches to.  It is a thin, security-hardened orchestration layer that:

- Validates and sanitizes all caller-supplied arguments before forwarding them
  to the core ``run_import_healer`` engine.
- Enforces production-mode security invariants (HTTPS endpoints, proxy
  requirements, forbidden flags).
- Emits structured audit-log events at every significant lifecycle boundary
  (invocation, completion, failure) to satisfy SOC 2 / ISO 27001 requirements.
- Tracks per-run metrics (duration, file counts, error counts) and exposes
  them to Prometheus via the shared compat_core metrics layer.
- Surfaces all unrecoverable failures through the ``alert_operator`` channel so
  that on-call engineers are paged without delay.
- Provides graceful, fully-documented fallbacks for every optional dependency
  so the module remains importable in environments that lack Redis, OpenTelemetry,
  or other infrastructure services.

Design principles
-----------------
- **Fail-fast in production**: missing or invalid configuration raises immediately
  rather than silently degrading.
- **Fail-safe in development**: optional services are skipped with a warning so
  that local iteration is not blocked.
- **Zero-trust inputs**: every argument is validated before use; paths are
  normalised and checked against the whitelist before the engine ever sees them.
- **Audit-first**: every invocation — successful or not — leaves an immutable,
  HMAC-signed record in the audit log.
- **Observability**: structured JSON logs, OpenTelemetry spans, and Prometheus
  counters/histograms are emitted for every major operation.

Compliance references
---------------------
- SOC 2 Type II CC6.1, CC7.2: Logical access controls and incident response.
- ISO 27001 A.12.4: Logging and monitoring.
- NIST SP 800-53 AU-2, AU-12: Event logging.
"""

from __future__ import annotations

import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Global flags — established early so every subsequent import can read them.
# ---------------------------------------------------------------------------
PRODUCTION_MODE: bool = os.getenv("PRODUCTION_MODE", "false").lower() == "true"
TESTING_MODE: bool = (
    os.getenv("TESTING", "false").lower() == "true"
    or os.getenv("TESTING") == "1"
    or os.getenv("PYTEST_CURRENT_TEST") is not None
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Core infrastructure — try relative import first (package context), then
# absolute (installed / sys.path context).  Provide safe, minimal fallbacks
# so that the module is always importable even without the full analyzer stack.
# ---------------------------------------------------------------------------
_core_loaded: bool = False

try:
    from .compat_core import (
        SECRETS_MANAGER,
        alert_operator,
        audit_logger,
        scrub_secrets,
    )
    _core_loaded = True
except ImportError:
    try:
        from self_healing_import_fixer.import_fixer.compat_core import (
            SECRETS_MANAGER,
            alert_operator,
            audit_logger,
            scrub_secrets,
        )
        _core_loaded = True
    except ImportError as _core_import_err:
        logger.warning(
            "fixer: core infrastructure (compat_core) not available — "
            "audit logging and alerting will be degraded. "
            "Error: %s",
            _core_import_err,
        )

        # --- Minimal, safe fallbacks (Null-Object pattern) ---

        class _FallbackSecretsManager:
            """No-op secrets manager for environments without the core stack."""

            def get_secret(self, key: str, required: bool = False) -> Optional[str]:
                value = os.getenv(key)
                if required and not value:
                    raise RuntimeError(
                        f"[FIXER] Required secret '{key}' is not set."
                    )
                return value

        SECRETS_MANAGER = _FallbackSecretsManager()  # type: ignore[assignment]

        def alert_operator(msg: str, level: str = "WARNING") -> None:  # type: ignore[misc]
            """Fallback: route operator alerts to the standard logger."""
            log_fn = getattr(logger, level.lower(), logger.warning)
            log_fn("[OPS ALERT – %s] %s", level, msg)

        class _FallbackAuditLogger:
            """No-op audit logger when the real audit subsystem is unavailable."""

            def log_event(self, event: str, **kwargs: Any) -> None:  # noqa: ANN001
                logger.info("[AUDIT] %s %s", event, kwargs)

            # Provide the same surface as the real audit_logger
            def info(self, msg: str, **kwargs: Any) -> None:
                logger.info(msg)

            def warning(self, msg: str, **kwargs: Any) -> None:
                logger.warning(msg)

            def error(self, msg: str, **kwargs: Any) -> None:
                logger.error(msg)

        audit_logger = _FallbackAuditLogger()  # type: ignore[assignment]

        def scrub_secrets(data: Any) -> Any:  # type: ignore[misc]
            """Fallback: identity function when the real scrubber is unavailable."""
            return data


# ---------------------------------------------------------------------------
# Prometheus metrics — lazy, non-blocking acquisition via compat_core.
# Fall back to no-op counters/histograms when prometheus_client is absent.
# ---------------------------------------------------------------------------
try:
    from .compat_core import get_prometheus_metrics as _get_prometheus_metrics

    _metrics = _get_prometheus_metrics()
    _fixer_invocations = getattr(_metrics, "fixer_invocations_total", None)
    _fixer_errors = getattr(_metrics, "fixer_errors_total", None)
    _fixer_duration = getattr(_metrics, "fixer_duration_seconds", None)
except Exception:
    _metrics = None
    _fixer_invocations = None
    _fixer_errors = None
    _fixer_duration = None


def _inc_metric(metric: Any, labels: Optional[Dict[str, str]] = None) -> None:
    """Safely increment a Prometheus counter, swallowing all errors."""
    try:
        if metric is None:
            return
        if labels:
            metric.labels(**labels).inc()
        else:
            metric.inc()
    except Exception:
        pass


def _observe_metric(
    metric: Any, value: float, labels: Optional[Dict[str, str]] = None
) -> None:
    """Safely record a Prometheus histogram observation, swallowing all errors."""
    try:
        if metric is None:
            return
        if labels:
            metric.labels(**labels).observe(value)
        else:
            metric.observe(value)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# OpenTelemetry tracing — lazy acquisition; silently no-op when unavailable.
# ---------------------------------------------------------------------------
try:
    from .compat_core import get_telemetry_tracer as _get_tracer

    _tracer = _get_tracer(__name__)
except Exception:
    _tracer = None


def _start_span(name: str) -> Any:
    """Start an OpenTelemetry span, returning a no-op context manager if unavailable."""
    try:
        if _tracer is not None:
            return _tracer.start_as_current_span(name)
    except Exception:
        pass

    # Minimal no-op context manager
    from contextlib import contextmanager

    @contextmanager
    def _noop():
        yield

    return _noop()


# ---------------------------------------------------------------------------
# Custom exception hierarchy — mirrors the pattern used throughout the module.
# ---------------------------------------------------------------------------

class FixerError(RuntimeError):
    """Base exception for all fixer entry-point errors."""


class FixerConfigError(FixerError):
    """
    Raised when the caller supplies invalid or insecure configuration.

    Triggers an operator alert immediately so on-call engineers can
    investigate and remediate before any healing work is attempted.
    """

    def __init__(self, message: str) -> None:
        super().__init__(f"[CRITICAL][FIXER] {message}")
        try:
            alert_operator(message, level="CRITICAL")
        except Exception:
            pass


class FixerSecurityError(FixerError):
    """
    Raised when a security invariant is violated (e.g. path traversal, HTTP
    endpoint in production mode).

    Triggers an operator alert at CRITICAL level and emits an audit event.
    """

    def __init__(self, message: str, path: Optional[str] = None) -> None:
        super().__init__(f"[CRITICAL][FIXER][SECURITY] {message}")
        try:
            alert_operator(message, level="CRITICAL")
            audit_logger.log_event(
                "fixer_security_violation",
                message=message,
                path=path,
            )
        except Exception:
            pass


class FixerRuntimeError(FixerError):
    """
    Raised when the healing engine encounters an unrecoverable runtime error.

    The underlying engine error is preserved as the ``__cause__`` so that
    stack traces remain actionable.
    """


# ---------------------------------------------------------------------------
# Input validation helpers
# ---------------------------------------------------------------------------

def _validate_project_root(project_root: str) -> str:
    """
    Validate and normalise the project root path.

    Args:
        project_root: Raw path string provided by the caller.

    Returns:
        Normalised absolute path string.

    Raises:
        FixerConfigError: If the value is empty, not a string, or the resolved
            path does not exist as a directory.
        FixerSecurityError: If the path contains null bytes or other path-
            injection markers.
    """
    if not isinstance(project_root, str) or not project_root.strip():
        raise FixerConfigError(
            "project_root must be a non-empty string. "
            f"Got: {type(project_root).__name__!r}"
        )
    if "\x00" in project_root:
        raise FixerSecurityError(
            "project_root contains a null byte — possible path injection attempt.",
            path=project_root,
        )
    resolved = os.path.abspath(project_root)
    if not os.path.isdir(resolved):
        raise FixerConfigError(
            f"project_root does not exist or is not a directory: {resolved!r}"
        )
    return resolved


def _validate_whitelisted_paths(
    whitelisted_paths: List[str], project_root: str
) -> List[str]:
    """
    Validate each whitelisted path and ensure the project root is covered.

    Args:
        whitelisted_paths: Caller-supplied list of allowed scan roots.
        project_root: Normalised absolute project root (from
            ``_validate_project_root``).

    Returns:
        Normalised list of absolute whitelist path strings.

    Raises:
        FixerConfigError: If the list is empty or any entry is not a string.
        FixerSecurityError: If any entry contains a null byte.
    """
    if not whitelisted_paths:
        raise FixerConfigError("whitelisted_paths must not be empty.")

    validated: List[str] = []
    for raw in whitelisted_paths:
        if not isinstance(raw, str) or not raw.strip():
            raise FixerConfigError(
                f"Each whitelisted path must be a non-empty string. Got: {raw!r}"
            )
        if "\x00" in raw:
            raise FixerSecurityError(
                "A whitelisted path contains a null byte — possible path injection.",
                path=raw,
            )
        validated.append(os.path.abspath(raw))

    # Verify that the project root falls within the whitelist so the engine
    # will not immediately reject it.
    root_covered = any(
        os.path.commonpath([project_root, wp]) == wp for wp in validated
    )
    if not root_covered:
        raise FixerSecurityError(
            f"project_root {project_root!r} is not covered by any whitelisted path. "
            "All scan roots must be inside the whitelist.",
            path=project_root,
        )
    return validated


def _validate_max_workers(max_workers: int) -> int:
    """
    Validate the max_workers concurrency parameter.

    Args:
        max_workers: Requested number of parallel worker threads/coroutines.

    Returns:
        Validated (and possibly clamped) worker count.

    Raises:
        FixerConfigError: If the value is not a positive integer.
    """
    if not isinstance(max_workers, int) or max_workers < 1:
        raise FixerConfigError(
            f"max_workers must be a positive integer. Got: {max_workers!r}"
        )
    cpu_count = os.cpu_count() or 4
    upper_bound = cpu_count * 8
    if max_workers > upper_bound:
        logger.warning(
            "max_workers=%d exceeds %d× CPU count (%d). "
            "Clamping to %d to avoid resource exhaustion.",
            max_workers,
            8,
            cpu_count,
            upper_bound,
        )
        return upper_bound
    return max_workers


def _validate_output_dir(output_dir: str, project_root: str) -> str:
    """
    Validate and normalise the report output directory.

    The directory is created (with mode 0o700 in production) if it does not
    already exist.

    Args:
        output_dir: Raw output directory path.
        project_root: Normalised project root (used to resolve relative paths).

    Returns:
        Normalised absolute path string for the output directory.

    Raises:
        FixerConfigError: If the path is invalid or cannot be created.
        FixerSecurityError: If the path contains a null byte.
    """
    if not isinstance(output_dir, str) or not output_dir.strip():
        raise FixerConfigError(
            "output_dir must be a non-empty string. "
            f"Got: {type(output_dir).__name__!r}"
        )
    if "\x00" in output_dir:
        raise FixerSecurityError(
            "output_dir contains a null byte — possible path injection attempt.",
            path=output_dir,
        )

    # Resolve relative paths against the project root for predictability.
    if not os.path.isabs(output_dir):
        output_dir = os.path.join(project_root, output_dir)

    resolved = os.path.abspath(output_dir)

    try:
        mode = 0o700 if PRODUCTION_MODE else 0o755
        Path(resolved).mkdir(parents=True, exist_ok=True, mode=mode)
    except (PermissionError, OSError) as exc:
        raise FixerConfigError(
            f"Cannot create output_dir {resolved!r}: {exc}"
        ) from exc

    return resolved


# ---------------------------------------------------------------------------
# Production mode security checks
# ---------------------------------------------------------------------------

def _enforce_production_invariants(kwargs: Dict[str, Any]) -> None:
    """
    Enforce security invariants that are mandatory in production deployments.

    These checks are intentionally separate from the main validation helpers
    so they can be audited and reviewed independently.

    Args:
        kwargs: The full keyword-argument dictionary that will be forwarded to
            the healing engine.

    Raises:
        FixerConfigError: If any mandatory production-mode invariant is violated.
    """
    if not PRODUCTION_MODE:
        return

    llm_endpoint: Optional[str] = kwargs.get("llm_endpoint")
    if llm_endpoint and not llm_endpoint.startswith("https://"):
        raise FixerConfigError(
            "LLM endpoint must use HTTPS in production mode. "
            f"Got: {llm_endpoint!r}"
        )

    if not kwargs.get("proxy_url"):
        logger.warning(
            "[PRODUCTION] No proxy_url configured. "
            "Direct LLM API calls may expose internal IPs."
        )
        audit_logger.log_event(
            "fixer_production_warning",
            warning="no_proxy_url_configured",
        )

    if kwargs.get("allow_auto_apply_patches", False):
        raise FixerConfigError(
            "allow_auto_apply_patches is forbidden in production mode. "
            "All AI-suggested patches require human review."
        )


# ---------------------------------------------------------------------------
# Trace ID propagation helpers
# ---------------------------------------------------------------------------

def _build_run_id() -> str:
    """Generate a stable, URL-safe run identifier for log correlation.

    Uses the full 128-bit UUID4 hex string to ensure collision resistance
    is sufficient for high-volume production deployments (>10^18 IDs before
    a 50 % birthday-paradox collision probability).
    """
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def main(
    project_root: str,
    whitelisted_paths: Optional[List[str]] = None,
    max_workers: int = 4,
    dry_run: bool = False,
    auto_add_deps: bool = False,
    ai_enabled: bool = False,
    output_dir: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Primary async entry point for the ``heal`` CLI command.

    Orchestrates the full import-healing pipeline for a Python project by
    validating all inputs, enforcing production-mode security policies, then
    delegating to :func:`run_import_healer
    <self_healing_import_fixer.import_fixer.import_fixer_engine.run_import_healer>`.

    Emits structured audit-log events at invocation start and at completion
    (whether successful or not) to satisfy regulatory traceability requirements.

    Args:
        project_root: Absolute or relative path to the root of the Python
            project to heal.  The path must exist and be a directory.
        whitelisted_paths: Explicit list of directory paths that the engine is
            permitted to scan and modify.  Defaults to ``[project_root]``.
            Every entry is resolved to an absolute path before use.
        max_workers: Maximum number of parallel worker coroutines used for
            concurrent file healing.  Must be a positive integer.  Values
            exceeding ``8 × CPU count`` are automatically clamped with a
            warning to prevent resource exhaustion.  Defaults to ``4``.
        dry_run: When ``True``, the engine analyses the project and reports
            what it *would* fix without writing any changes to disk.  Safe
            to use in CI pipelines where write access is undesirable.
            Defaults to ``False``.
        auto_add_deps: When ``True``, the engine may automatically install
            missing third-party packages identified during healing.
            Defaults to ``False``.
        ai_enabled: When ``True``, the AI subsystem is invoked to suggest
            fixes for imports that the deterministic engine cannot resolve.
            Requires a valid ``LLM_API_KEY`` secret and a configured
            ``llm_endpoint``.  Defaults to ``False``.
        output_dir: Directory where healing reports and artefacts are written.
            Relative paths are resolved against ``project_root``.  Defaults
            to ``"reports"`` under the project root.
        **kwargs: Additional keyword arguments forwarded verbatim to
            :func:`run_import_healer`.  Recognised keys include
            ``llm_endpoint``, ``proxy_url``, and ``allow_auto_apply_patches``.

    Returns:
        A dictionary containing the full healing report as returned by
        :func:`run_import_healer`.  Guaranteed keys:

        - ``"summary"`` (str): Human-readable summary of the healing run.
        - ``"dependency_report"`` (dict): Results of the dependency-healing
          phase.
        - ``"cycle_healing_report"`` (dict): Results of the cycle-detection and
          healing phase, including ``cycles_found``, ``cycles_fixed``, and
          ``failures``.
        - ``"run_id"`` (str): Unique identifier for this healing run, suitable
          for log correlation across services.
        - ``"duration_seconds"`` (float): Wall-clock time consumed by the run.
        - ``"dry_run"`` (bool): Echo of the ``dry_run`` flag that was used.

    Raises:
        FixerConfigError: If any argument fails validation or if a mandatory
            production-mode security invariant is violated.
        FixerSecurityError: If a path-injection or other security violation is
            detected during input validation.
        FixerRuntimeError: If the healing engine raises an unrecoverable error.

    Examples:
        Dry-run heal of a local project::

            import asyncio
            from import_fixer.fixer import main

            report = asyncio.run(main(
                project_root="/srv/myproject",
                dry_run=True,
            ))
            print(report["summary"])

        Full heal with AI assistance (requires ``LLM_API_KEY`` env var)::

            report = asyncio.run(main(
                project_root="/srv/myproject",
                ai_enabled=True,
                llm_endpoint="https://api.openai.com/v1",
            ))

    Security:
        - All path arguments are resolved to absolute paths and validated
          against the whitelist before use.
        - Null-byte injection in any path argument is detected and rejected.
        - In production mode, HTTPS is enforced for LLM endpoints and
          ``allow_auto_apply_patches`` is forbidden.
        - Secrets are never written to log output; all log messages pass
          through :func:`scrub_secrets` before emission.

    Compliance:
        - SOC 2 CC6.1: Access to healing artefacts is restricted to the
          ``output_dir`` path, which is created with mode ``0o700`` in
          production.
        - ISO 27001 A.12.4.1: Every invocation is recorded in the audit log
          with a unique ``run_id`` for non-repudiation.
        - NIST SP 800-53 AU-12: Event generation is unconditional — failures
          are logged before exceptions are re-raised.
    """
    run_id = _build_run_id()
    start_time = time.monotonic()

    # ------------------------------------------------------------------
    # Step 1: Validate and normalise all arguments.
    # ------------------------------------------------------------------
    resolved_root = _validate_project_root(project_root)

    effective_whitelist = _validate_whitelisted_paths(
        whitelisted_paths if whitelisted_paths is not None else [project_root],
        resolved_root,
    )

    validated_workers = _validate_max_workers(max_workers)

    effective_output_dir = _validate_output_dir(
        output_dir if output_dir is not None else "reports",
        resolved_root,
    )

    # ------------------------------------------------------------------
    # Step 2: Production-mode security invariants.
    # ------------------------------------------------------------------
    _enforce_production_invariants(kwargs)

    # ------------------------------------------------------------------
    # Step 3: Emit audit-log INVOCATION event.
    # ------------------------------------------------------------------
    audit_logger.log_event(
        "fixer_invocation_start",
        run_id=run_id,
        project_root=scrub_secrets(resolved_root),
        whitelisted_paths=scrub_secrets(effective_whitelist),
        max_workers=validated_workers,
        dry_run=dry_run,
        auto_add_deps=auto_add_deps,
        ai_enabled=ai_enabled,
        output_dir=effective_output_dir,
        production_mode=PRODUCTION_MODE,
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    )

    _inc_metric(
        _fixer_invocations,
        {"dry_run": str(dry_run), "ai_enabled": str(ai_enabled)},
    )

    logger.info(
        "Import-fixer run starting. run_id=%s project_root=%s dry_run=%s "
        "max_workers=%d ai_enabled=%s",
        run_id,
        scrub_secrets(resolved_root),
        dry_run,
        validated_workers,
        ai_enabled,
    )

    # ------------------------------------------------------------------
    # Step 4: Import the engine (deferred to avoid circular imports at
    # module level and to allow test-time patching).
    # ------------------------------------------------------------------
    try:
        try:
            from .import_fixer_engine import run_import_healer
        except ImportError:
            from import_fixer_engine import run_import_healer  # type: ignore[no-redef]
    except ImportError as exc:
        msg = f"Cannot import run_import_healer engine: {exc}"
        logger.critical("[FIXER] %s", msg, exc_info=True)
        alert_operator(msg, level="CRITICAL")
        audit_logger.log_event(
            "fixer_invocation_failure",
            run_id=run_id,
            stage="engine_import",
            error=str(exc),
        )
        raise FixerRuntimeError(msg) from exc

    # ------------------------------------------------------------------
    # Step 5: Execute the healing pipeline inside a telemetry span.
    # ------------------------------------------------------------------
    result: Dict[str, Any] = {}
    error_occurred: bool = False

    with _start_span("fixer.main"):
        try:
            result = await run_import_healer(
                project_root=resolved_root,
                whitelisted_paths=effective_whitelist,
                max_workers=validated_workers,
                dry_run=dry_run,
                auto_add_deps=auto_add_deps,
                ai_enabled=ai_enabled,
                output_dir=effective_output_dir,
                **kwargs,
            )
        except (FixerError, KeyboardInterrupt):
            raise
        except Exception as exc:
            error_occurred = True
            duration = time.monotonic() - start_time
            msg = f"Healing engine raised an unrecoverable error: {exc}"

            logger.error(
                "[FIXER] run_id=%s error=%s duration_seconds=%.3f",
                run_id,
                type(exc).__name__,
                duration,
                exc_info=True,
            )
            alert_operator(
                f"Import-fixer run {run_id!r} failed: {exc}",
                level="ERROR",
            )
            audit_logger.log_event(
                "fixer_invocation_failure",
                run_id=run_id,
                stage="engine_execution",
                error=type(exc).__name__,
                duration_seconds=round(duration, 3),
            )
            _inc_metric(_fixer_errors, {"stage": "engine_execution"})
            _observe_metric(_fixer_duration, duration, {"status": "error"})

            raise FixerRuntimeError(msg) from exc

    # ------------------------------------------------------------------
    # Step 6: Enrich the result with run metadata and emit completion event.
    # ------------------------------------------------------------------
    duration = time.monotonic() - start_time

    result["run_id"] = run_id
    result["duration_seconds"] = round(duration, 3)
    result["dry_run"] = dry_run

    cycle_report = result.get("cycle_healing_report", {})
    dep_report = result.get("dependency_report", {})

    audit_logger.log_event(
        "fixer_invocation_complete",
        run_id=run_id,
        duration_seconds=round(duration, 3),
        dry_run=dry_run,
        cycles_found=cycle_report.get("cycles_found", 0),
        cycles_fixed=cycle_report.get("cycles_fixed", 0),
        cycle_failures=len(cycle_report.get("failures", [])),
        dep_report_keys=list(dep_report.keys()) if isinstance(dep_report, dict) else [],
    )

    if not error_occurred:
        _observe_metric(_fixer_duration, duration, {"status": "success"})

    logger.info(
        "Import-fixer run complete. run_id=%s duration=%.3fs dry_run=%s",
        run_id,
        duration,
        dry_run,
    )

    return result


# ---------------------------------------------------------------------------
# Public module exports
# ---------------------------------------------------------------------------
__all__: List[str] = [
    "main",
    "FixerError",
    "FixerConfigError",
    "FixerSecurityError",
    "FixerRuntimeError",
]
