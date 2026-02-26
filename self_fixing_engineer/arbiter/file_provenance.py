# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
File Provenance Registry — Enterprise-grade origin tracking for Generator-produced files.

This module provides a production-ready :class:`FileProvenanceRegistry` that records
the full provenance chain for every file emitted by the Generator pipeline.  The
registry is consumed by the Arbiter and the Self-Fixing Engineer (SFE) to:

* Prioritise quality checks on machine-generated artefacts without interfering
  with manually-authored code.
* Enable automatic routing of defects on generated files through the SFE fix
  pipeline.
* Provide an auditable lineage trail (generator_id, workflow correlation ID,
  language, timestamp) for compliance and debugging.

Architecture
------------
Persistence strategy (evaluated in order of preference):

1. **Async SQLAlchemy engine** injected at construction time — full ACID
   transactional guarantees, compatible with PostgreSQL and SQLite.
2. **JSON file** at ``provenance_path`` — zero-dependency fallback for
   single-node or development deployments; written atomically via a
   temporary-file rename to prevent corruption on crash.
3. **In-memory only** — degraded mode when both the DB engine and the
   filesystem are unavailable; logged as WARNING.

All public methods are ``async`` so they integrate seamlessly with the
Arbiter's event-loop-driven architecture.  Internal state is protected by
an :class:`asyncio.Lock` to guard concurrent event-handler writes.

Observability
-------------
* **Prometheus metrics** — counters and histograms for registrations,
  validations, lookups, and persistence latency, using the platform-wide
  ``get_or_create_*`` helpers to avoid duplicate-registration races.
* **OpenTelemetry traces** — each public operation opens a child span so
  distributed traces surface provenance activity alongside SFE fix spans.
* **Structured logging** — all log records carry ``component`` and
  ``operation`` fields consumed by the platform log pipeline.

Security
--------
Provenance records may contain file paths and generator identifiers.  The
PII redaction filter from :mod:`self_fixing_engineer.arbiter.logging_utils`
is applied to the module logger so that any accidentally logged user data
is scrubbed before reaching persistent log sinks.

Usage
-----
::

    from self_fixing_engineer.arbiter.file_provenance import FileProvenanceRegistry

    registry = FileProvenanceRegistry(
        db_engine=my_async_engine,          # optional
        provenance_path="./provenance.json" # JSON fallback path
    )
    await registry.initialize()

    # Generator pipeline registers a new file
    await registry.register_generated_file(
        "/workspace/src/auth_service.py",
        {
            "generator_id": "codegen-v3",
            "language": "python",
            "workflow_id": "wf-20250226-001",
        },
    )

    # SFE checks provenance before deciding to auto-fix
    if await registry.is_generated("/workspace/src/auth_service.py"):
        prov = await registry.get_provenance("/workspace/src/auth_service.py")
        ...

    # After SFE validates the file
    await registry.mark_validated("/workspace/src/auth_service.py")

    # Retrieve all files still awaiting SFE review
    pending = await registry.get_generated_files_needing_review()
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tempfile
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Platform-internal imports with graceful degradation
# ---------------------------------------------------------------------------

# Prometheus metrics — thread-safe registration via platform helpers
try:
    from self_fixing_engineer.arbiter.metrics import (
        get_or_create_counter,
        get_or_create_gauge,
        get_or_create_histogram,
    )
    _METRICS_AVAILABLE = True
except ImportError:
    _METRICS_AVAILABLE = False

    def get_or_create_counter(name, doc, labelnames=None):  # type: ignore[misc]
        class _NoOp:
            def labels(self, **kw):
                return self
            def inc(self, amount=1):
                pass
        return _NoOp()

    def get_or_create_gauge(name, doc, labelnames=None):  # type: ignore[misc]
        class _NoOp:
            def labels(self, **kw):
                return self
            def set(self, v):
                pass
            def inc(self, amount=1):
                pass
            def dec(self, amount=1):
                pass
        return _NoOp()

    def get_or_create_histogram(name, doc, labelnames=None, buckets=None):  # type: ignore[misc]
        class _NoOp:
            def labels(self, **kw):
                return self
            def observe(self, v):
                pass
        return _NoOp()


# OpenTelemetry tracing — zero-overhead no-op when unavailable
try:
    from self_fixing_engineer.arbiter.otel_config import get_tracer
    _OTEL_AVAILABLE = True
except ImportError:
    _OTEL_AVAILABLE = False

    class _NoOpSpan:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def set_attribute(self, k, v):
            pass
        def record_exception(self, exc):
            pass
        def add_event(self, name, attributes=None):
            pass

    class _NoOpTracer:
        def start_as_current_span(self, name, **kw):
            return _NoOpSpan()

    def get_tracer(name=None):  # type: ignore[misc]
        return _NoOpTracer()


# PII-safe logger — strips emails, tokens, paths from log records
try:
    from self_fixing_engineer.arbiter.logging_utils import PIIRedactorFilter as _PIIFilter
    _logger = logging.getLogger(__name__)
    if not any(isinstance(f, _PIIFilter) for f in _logger.filters):
        _logger.addFilter(_PIIFilter())
except ImportError:
    _logger = logging.getLogger(__name__)


# Tenacity retry — for transient DB/IO failures
try:
    from tenacity import (
        retry,
        retry_if_exception_type,
        stop_after_attempt,
        wait_exponential,
    )
    _TENACITY_AVAILABLE = True
except ImportError:
    _TENACITY_AVAILABLE = False

    def retry(*args, **kwargs):  # type: ignore[misc]
        def decorator(fn):
            return fn
        return decorator

    def stop_after_attempt(n):  # type: ignore[misc]
        return None

    def wait_exponential(**kw):  # type: ignore[misc]
        return None

    def retry_if_exception_type(*args):  # type: ignore[misc]
        return None


# aiofiles — async file I/O (falls back to sync executor)
try:
    import aiofiles
    _AIOFILES_AVAILABLE = True
except ImportError:
    _AIOFILES_AVAILABLE = False


# ---------------------------------------------------------------------------
# Optional SQLAlchemy — JSON-file fallback when unavailable
# ---------------------------------------------------------------------------
try:
    from sqlalchemy import Column, DateTime, String, Text, select, update as sa_update
    from sqlalchemy.ext.asyncio import AsyncEngine
    from sqlalchemy.orm import declarative_base as _sa_declarative_base

    _SA_BASE = _sa_declarative_base()

    class _ProvenanceRecord(_SA_BASE):  # type: ignore[misc,valid-type]
        """SQLAlchemy ORM model for file provenance records.

        The ``validated`` column stores the string ``"true"``/``"false"``
        rather than a boolean so the schema remains compatible with both
        SQLite (no native boolean) and PostgreSQL.
        """

        __tablename__ = "file_provenance"
        __table_args__ = {"extend_existing": True}

        file_path = Column(String(4096), primary_key=True, index=True, nullable=False)
        generator_id = Column(String(256), nullable=True, index=True)
        language = Column(String(64), nullable=True)
        workflow_id = Column(String(256), nullable=True, index=True)
        registered_at = Column(DateTime(timezone=True), nullable=False)
        validated = Column(String(8), default="false", nullable=False)
        extra_json = Column(Text, nullable=True)

    _SA_AVAILABLE = True
except ImportError:
    _SA_AVAILABLE = False
    AsyncEngine = None  # type: ignore[assignment,misc]
    _ProvenanceRecord = None  # type: ignore[assignment]
    _SA_BASE = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Module-level Prometheus metrics (lazy-initialised, thread-safe)
# ---------------------------------------------------------------------------
_metrics_lock = threading.Lock()
_metrics_initialised = False

_PROV_REGISTRATIONS: Any = None
_PROV_LOOKUPS: Any = None
_PROV_VALIDATIONS: Any = None
_PROV_PERSIST_ERRORS: Any = None
_PROV_OPERATION_DURATION: Any = None
_PROV_REGISTRY_SIZE: Any = None


def _init_metrics() -> None:
    """Idempotently initialise module-level Prometheus metrics.

    Called on first :class:`FileProvenanceRegistry` instantiation so that
    importing this module does not register metrics (which would fail in
    test collection mode where the Prometheus registry is reset between runs).
    """
    global _metrics_initialised, _PROV_REGISTRATIONS, _PROV_LOOKUPS, _PROV_VALIDATIONS
    global _PROV_PERSIST_ERRORS, _PROV_OPERATION_DURATION, _PROV_REGISTRY_SIZE
    with _metrics_lock:
        if _metrics_initialised:
            return
        _PROV_REGISTRATIONS = get_or_create_counter(
            "file_provenance_registrations_total",
            "Total files registered as generator-produced artefacts",
            ("language",),
        )
        _PROV_LOOKUPS = get_or_create_counter(
            "file_provenance_lookups_total",
            "Total provenance lookups performed",
            ("hit",),
        )
        _PROV_VALIDATIONS = get_or_create_counter(
            "file_provenance_validations_total",
            "Total files marked as SFE-validated",
        )
        _PROV_PERSIST_ERRORS = get_or_create_counter(
            "file_provenance_persistence_errors_total",
            "Total provenance persistence failures",
            ("backend",),
        )
        _PROV_OPERATION_DURATION = get_or_create_histogram(
            "file_provenance_operation_duration_seconds",
            "Duration of FileProvenanceRegistry public operations",
            ("operation",),
            buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, float("inf")),
        )
        _PROV_REGISTRY_SIZE = get_or_create_gauge(
            "file_provenance_registry_size",
            "Number of files currently tracked by the provenance registry",
        )
        _metrics_initialised = True


# ---------------------------------------------------------------------------
# Sentinel for missing provenance entries
# ---------------------------------------------------------------------------
_MISSING: object = object()


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------


class FileProvenanceRegistry:
    """Production-grade registry that tracks files produced by the Generator.

    Provides a unified, persistence-backed API for:

    * Registering generated files with full provenance metadata.
    * Querying whether a given path was machine-generated.
    * Listing all generated files and those awaiting SFE review.
    * Marking files as validated by the SFE after a successful fix cycle.

    Thread-/coroutine-safety
    ~~~~~~~~~~~~~~~~~~~~~~~~
    All mutations are serialised through an :class:`asyncio.Lock`.  The
    in-memory cache is the authoritative read path; persistence operations
    are fire-and-forget (errors are logged and metered but never propagated
    to callers) so as never to stall the hot generator event path.

    Graceful degradation
    ~~~~~~~~~~~~~~~~~~~~
    If the DB engine is unavailable the registry automatically falls back to
    a JSON file.  If neither is available it operates in-memory only, logging
    a ``WARNING`` to surface the degraded mode in monitoring dashboards.

    Args:
        db_engine: Optional async SQLAlchemy engine.  When supplied and
            ``_SA_AVAILABLE`` is ``True`` all writes are persisted to the
            ``file_provenance`` table.
        provenance_path: Path for the JSON fallback file.  Ignored when a DB
            engine is provided.  Parent directories are created on first
            write.  Defaults to ``"./provenance.json"``.
    """

    # ------------------------------------------------------------------
    # Construction & lifecycle
    # ------------------------------------------------------------------

    def __init__(
        self,
        db_engine: Optional[Any] = None,
        provenance_path: str = "./provenance.json",
    ) -> None:
        _init_metrics()
        self._tracer = get_tracer(__name__)
        self._db_engine: Optional[Any] = db_engine if _SA_AVAILABLE else None
        self._provenance_path = Path(provenance_path)
        # In-memory cache: path → record dict.  Provides O(1) reads without I/O.
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._initialized = False
        self._persistence_backend: str = "none"

        _logger.debug(
            "[FileProvenance] Registry created",
            extra={
                "component": "FileProvenanceRegistry",
                "db_available": self._db_engine is not None,
                "provenance_path": str(self._provenance_path),
            },
        )

    async def initialize(self) -> None:
        """Bootstrap the backing store and warm the in-memory cache.

        This method is idempotent — calling it multiple times is safe.
        It must be awaited before the first read/write call, although all
        public methods will call it lazily if it has not been invoked.

        Raises:
            Does not raise.  All initialisation errors degrade gracefully
            and are captured in log records and Prometheus counters.
        """
        if self._initialized:
            return
        async with self._lock:
            if self._initialized:
                return
            with self._tracer.start_as_current_span(
                "file_provenance.initialize"
            ) as span:
                if self._db_engine is not None and _SA_AVAILABLE:
                    try:
                        async with self._db_engine.begin() as conn:
                            await conn.run_sync(_SA_BASE.metadata.create_all)
                        await self._load_from_db()
                        self._persistence_backend = "db"
                        span.set_attribute("backend", "db")
                        _logger.info(
                            "[FileProvenance] Initialised with DB backend "
                            f"({len(self._cache)} record(s) loaded)"
                        )
                    except Exception as exc:
                        _logger.warning(
                            f"[FileProvenance] DB backend unavailable ({exc!r}); "
                            "falling back to JSON file"
                        )
                        self._db_engine = None
                        await self._load_from_json()
                        self._persistence_backend = "json"
                        span.set_attribute("backend", "json_fallback")
                else:
                    await self._load_from_json()
                    self._persistence_backend = "json" if self._provenance_path.exists() else "memory"
                    span.set_attribute("backend", self._persistence_backend)

                if self._persistence_backend == "memory":
                    _logger.warning(
                        "[FileProvenance] Operating in-memory only — no persistence "
                        "configured.  Provenance records will be lost on restart."
                    )

                _PROV_REGISTRY_SIZE.set(len(self._cache))  # type: ignore[union-attr]
                self._initialized = True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def register_generated_file(
        self,
        path: str,
        metadata: Dict[str, Any],
    ) -> None:
        """Register *path* as a file produced by the Generator pipeline.

        This is the primary write path, called by :meth:`Arbiter._on_generator_output`
        for every file path emitted by the Generator.  The call returns as
        soon as the in-memory cache is updated; persistence is attempted
        asynchronously and failures are silently absorbed so this method
        **never blocks or raises on persistence errors**.

        Args:
            path: Absolute or relative file-system path of the generated file.
            metadata: Provenance metadata dict.  Recognised keys (all optional):

                * ``generator_id`` (str) — identifier of the generating model/agent.
                * ``language`` (str) — programming language (e.g. ``"python"``).
                * ``workflow_id`` (str) — correlation ID linking to the parent workflow.
                * ``timestamp`` (str) — ISO-8601 creation timestamp; defaults to *now*.
                * ``validated`` (bool) — whether the file has passed SFE review.

                Any additional keys are stored verbatim in the ``extra`` sub-dict.

        Returns:
            None.

        Raises:
            Does not raise.
        """
        if not self._initialized:
            await self.initialize()

        t0 = time.monotonic()
        with self._tracer.start_as_current_span(
            "file_provenance.register_generated_file"
        ) as span:
            span.set_attribute("file.path", path)
            span.set_attribute("language", metadata.get("language", "unknown"))
            span.set_attribute("generator_id", metadata.get("generator_id", ""))

            entry = self._build_entry(path, metadata)

            async with self._lock:
                is_update = path in self._cache
                self._cache[path] = entry
                size = len(self._cache)

            _PROV_REGISTRY_SIZE.set(size)  # type: ignore[union-attr]
            _PROV_REGISTRATIONS.labels(  # type: ignore[union-attr]
                language=metadata.get("language", "unknown")
            ).inc()

            # Fire-and-forget persistence
            asyncio.ensure_future(self._safe_persist(entry))

            elapsed = time.monotonic() - t0
            _PROV_OPERATION_DURATION.labels(  # type: ignore[union-attr]
                operation="register"
            ).observe(elapsed)
            span.add_event("registered", {"update": is_update, "duration_ms": elapsed * 1000})

            _logger.debug(
                f"[FileProvenance] {'Updated' if is_update else 'Registered'} "
                f"generated file: {path}",
                extra={"component": "FileProvenanceRegistry", "path": path},
            )

    async def is_generated(self, path: str) -> bool:
        """Return ``True`` if *path* was produced by the Generator.

        This is a hot-path read used by the SFE before every quality-check
        decision.  It reads only from the in-memory cache to avoid I/O.

        Args:
            path: File path to query.

        Returns:
            ``True`` when the path is in the provenance registry; ``False``
            otherwise.
        """
        if not self._initialized:
            await self.initialize()
        result = path in self._cache
        _PROV_LOOKUPS.labels(hit=str(result).lower()).inc()  # type: ignore[union-attr]
        return result

    async def get_provenance(self, path: str) -> Optional[Dict[str, Any]]:
        """Return the full provenance record for *path*, or ``None``.

        Args:
            path: File path to query.

        Returns:
            Provenance dict with at minimum the keys ``file_path``,
            ``generator_id``, ``language``, ``workflow_id``,
            ``registered_at``, ``validated``, and ``extra``.  Returns
            ``None`` when *path* is not tracked.
        """
        if not self._initialized:
            await self.initialize()
        record = self._cache.get(path)
        _PROV_LOOKUPS.labels(  # type: ignore[union-attr]
            hit=str(record is not None).lower()
        ).inc()
        return record

    async def list_generated_files(self) -> List[Dict[str, Any]]:
        """Return a snapshot of all registered generated-file records.

        Returns:
            List of provenance record dicts.  May be empty when no files
            have been registered (e.g. on a cold start).
        """
        if not self._initialized:
            await self.initialize()
        return list(self._cache.values())

    async def mark_validated(self, path: str) -> bool:
        """Record that the SFE has validated (or auto-fixed) *path*.

        Args:
            path: File path to mark as validated.

        Returns:
            ``True`` when the path was found and updated; ``False`` when it
            was not present in the registry (no-op, not an error).
        """
        if not self._initialized:
            await self.initialize()

        t0 = time.monotonic()
        with self._tracer.start_as_current_span(
            "file_provenance.mark_validated"
        ) as span:
            span.set_attribute("file.path", path)

            async with self._lock:
                if path not in self._cache:
                    _logger.debug(
                        f"[FileProvenance] mark_validated: '{path}' not in registry"
                    )
                    span.set_attribute("found", False)
                    return False
                self._cache[path] = {**self._cache[path], "validated": True}

            _PROV_VALIDATIONS.inc()  # type: ignore[union-attr]
            asyncio.ensure_future(self._safe_update_validated(path))

            elapsed = time.monotonic() - t0
            _PROV_OPERATION_DURATION.labels(  # type: ignore[union-attr]
                operation="mark_validated"
            ).observe(elapsed)
            span.set_attribute("found", True)

            _logger.info(
                f"[FileProvenance] Marked as SFE-validated: {path}",
                extra={"component": "FileProvenanceRegistry", "path": path},
            )
            return True

    async def get_generated_files_needing_review(self) -> List[Dict[str, Any]]:
        """Return generated files that have **not** yet been SFE-validated.

        Used by the SFE prioritisation logic to build its work queue of
        generated files that have not yet undergone a quality check.

        Returns:
            List of provenance record dicts where ``validated`` is ``False``.
        """
        if not self._initialized:
            await self.initialize()
        return [
            record
            for record in self._cache.values()
            if not record.get("validated", False)
        ]

    async def get_summary(self) -> Dict[str, Any]:
        """Return a human-readable summary of registry state.

        Useful for health-check endpoints and monitoring dashboards.

        Returns:
            Dict with keys ``total``, ``validated``, ``pending_review``,
            ``backend``, and ``provenance_path``.
        """
        if not self._initialized:
            await self.initialize()
        records = list(self._cache.values())
        validated_count = sum(1 for r in records if r.get("validated", False))
        return {
            "total": len(records),
            "validated": validated_count,
            "pending_review": len(records) - validated_count,
            "backend": self._persistence_backend,
            "provenance_path": str(self._provenance_path),
        }

    # ------------------------------------------------------------------
    # Internal helpers — record construction
    # ------------------------------------------------------------------

    @staticmethod
    def _build_entry(path: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Construct a canonical provenance record from raw metadata.

        Args:
            path: Normalised file path (used as primary key).
            metadata: Raw metadata dict from the caller.

        Returns:
            Normalised provenance record dict.
        """
        _known = {"generator_id", "language", "workflow_id", "timestamp", "validated"}
        return {
            "file_path": path,
            "generator_id": metadata.get("generator_id"),
            "language": metadata.get("language"),
            "workflow_id": metadata.get("workflow_id"),
            "registered_at": metadata.get(
                "timestamp", datetime.now(timezone.utc).isoformat()
            ),
            "validated": bool(metadata.get("validated", False)),
            "content_hash": metadata.get("content_hash") or _hash_path(path),
            "extra": {k: v for k, v in metadata.items() if k not in _known},
        }

    # ------------------------------------------------------------------
    # Internal helpers — persistence (DB)
    # ------------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    async def _persist_to_db(self, entry: Dict[str, Any]) -> None:
        """Upsert a single provenance entry into the DB.

        Uses a dialect-agnostic select-then-insert/update pattern so this
        works across both PostgreSQL and SQLite without dialect-specific syntax.

        Args:
            entry: Canonical provenance record dict from :meth:`_build_entry`.
        """
        reg_at = entry.get("registered_at")
        if isinstance(reg_at, str):
            try:
                reg_at = datetime.fromisoformat(reg_at)
            except ValueError:
                reg_at = datetime.now(timezone.utc)

        extra_json_str = json.dumps(
            {
                "extra": entry.get("extra") or {},
                "content_hash": entry.get("content_hash"),
            },
            default=str,
        )

        async with self._db_engine.begin() as conn:
            result = await conn.execute(
                select(_ProvenanceRecord).where(
                    _ProvenanceRecord.file_path == entry["file_path"]
                )
            )
            existing = result.fetchone()
            if existing:
                await conn.execute(
                    sa_update(_ProvenanceRecord)
                    .where(_ProvenanceRecord.file_path == entry["file_path"])
                    .values(
                        generator_id=entry.get("generator_id"),
                        language=entry.get("language"),
                        workflow_id=entry.get("workflow_id"),
                        validated="true" if entry.get("validated") else "false",
                        extra_json=extra_json_str,
                    )
                )
            else:
                await conn.execute(
                    _ProvenanceRecord.__table__.insert().values(
                        file_path=entry["file_path"],
                        generator_id=entry.get("generator_id"),
                        language=entry.get("language"),
                        workflow_id=entry.get("workflow_id"),
                        registered_at=reg_at,
                        validated="true" if entry.get("validated") else "false",
                        extra_json=extra_json_str,
                    )
                )

    async def _update_validated_in_db(self, path: str) -> None:
        """Flip the ``validated`` flag to ``"true"`` for *path* in the DB.

        Args:
            path: Primary key of the record to update.
        """
        async with self._db_engine.begin() as conn:
            stmt = (
                sa_update(_ProvenanceRecord)
                .where(_ProvenanceRecord.file_path == path)
                .values(validated="true")
            )
            await conn.execute(stmt)

    async def _load_from_db(self) -> None:
        """Warm the in-memory cache from all rows in the DB table."""
        async with self._db_engine.connect() as conn:
            result = await conn.execute(select(_ProvenanceRecord))
            rows = result.fetchall()
        for row in rows:
            extra_blob = {}
            try:
                extra_blob = json.loads(row.extra_json or "{}")
            except (json.JSONDecodeError, TypeError):
                pass
            self._cache[row.file_path] = {
                "file_path": row.file_path,
                "generator_id": row.generator_id,
                "language": row.language,
                "workflow_id": row.workflow_id,
                "registered_at": row.registered_at.isoformat()
                if row.registered_at
                else None,
                "validated": row.validated == "true",
                "content_hash": extra_blob.get("content_hash"),
                "extra": extra_blob.get("extra", {}),
            }

    # ------------------------------------------------------------------
    # Internal helpers — persistence (JSON)
    # ------------------------------------------------------------------

    async def _load_from_json(self) -> None:
        """Populate the in-memory cache from the JSON fallback file."""
        if not self._provenance_path.exists():
            return
        try:
            if _AIOFILES_AVAILABLE:
                import aiofiles
                async with aiofiles.open(
                    str(self._provenance_path), "r", encoding="utf-8"
                ) as fh:
                    raw = await fh.read()
            else:
                loop = asyncio.get_running_loop()
                raw = await loop.run_in_executor(
                    None, self._provenance_path.read_text, "utf-8"
                )
            records: List[Dict[str, Any]] = json.loads(raw)
            for record in records:
                fp = record.get("file_path", "")
                if fp:
                    self._cache[fp] = record
            _logger.info(
                f"[FileProvenance] Loaded {len(self._cache)} record(s) from JSON"
            )
        except Exception as exc:
            _logger.warning(
                f"[FileProvenance] Could not load JSON provenance file "
                f"({self._provenance_path}): {exc!r}"
            )

    async def _persist_to_json(self) -> None:
        """Write the full cache to the JSON file using an atomic rename."""
        records = list(self._cache.values())
        serialised = json.dumps(records, default=str, indent=2, ensure_ascii=False)
        parent = self._provenance_path.parent

        try:
            os.makedirs(parent, exist_ok=True)
            # Atomic write: write to tmp then rename so a crash mid-write
            # never produces a corrupt file.
            fd, tmp_path = tempfile.mkstemp(dir=parent, suffix=".tmp")
            try:
                if _AIOFILES_AVAILABLE:
                    import aiofiles
                    async with aiofiles.open(tmp_path, "w", encoding="utf-8") as fh:
                        await fh.write(serialised)
                else:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(
                        None,
                        lambda: Path(tmp_path).write_text(serialised, encoding="utf-8"),
                    )
                os.replace(tmp_path, self._provenance_path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
            finally:
                try:
                    os.close(fd)
                except OSError:
                    pass
        except Exception as exc:
            _logger.warning(
                f"[FileProvenance] Could not write JSON provenance file: {exc!r}"
            )
            raise

    # ------------------------------------------------------------------
    # Internal helpers — fire-and-forget persistence wrappers
    # ------------------------------------------------------------------

    async def _safe_persist(self, entry: Dict[str, Any]) -> None:
        """Best-effort persistence of *entry*; errors are absorbed."""
        try:
            if self._db_engine is not None and _SA_AVAILABLE:
                await self._persist_to_db(entry)
            elif self._persistence_backend != "memory":
                await self._persist_to_json()
        except Exception as exc:
            backend = "db" if self._db_engine else "json"
            _PROV_PERSIST_ERRORS.labels(backend=backend).inc()  # type: ignore[union-attr]
            _logger.warning(
                f"[FileProvenance] Persistence error for '{entry.get('file_path')}' "
                f"(backend={backend}): {exc!r}"
            )

    async def _safe_update_validated(self, path: str) -> None:
        """Best-effort DB / JSON update of the validated flag; errors absorbed."""
        try:
            if self._db_engine is not None and _SA_AVAILABLE:
                await self._update_validated_in_db(path)
            elif self._persistence_backend != "memory":
                await self._persist_to_json()
        except Exception as exc:
            backend = "db" if self._db_engine else "json"
            _PROV_PERSIST_ERRORS.labels(backend=backend).inc()  # type: ignore[union-attr]
            _logger.warning(
                f"[FileProvenance] Failed to persist validated flag for '{path}' "
                f"(backend={backend}): {exc!r}"
            )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _hash_path(path: str) -> str:
    """Return a short stable hash of *path* usable as a content-identity hint.

    This is a *path* hash only (not a file-content hash); it is recorded in
    the provenance entry so downstream tools can detect path renames without
    re-reading disk state.

    Args:
        path: File system path string.

    Returns:
        Hex-encoded first 16 bytes of the SHA-256 digest.
    """
    return hashlib.sha256(path.encode("utf-8")).hexdigest()[:32]


__all__ = ["FileProvenanceRegistry"]
