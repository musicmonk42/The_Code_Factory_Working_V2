# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Unified cryptographic hashing utilities for the Code Factory platform.

Problem
-------
Five independent hash-computation implementations existed in the codebase:

- ``generator/runner/runner_audit.py``                          — SHA-256 fallback stub
- ``generator/runner/runner_logging.py``                        — SHA-256 fallback stub
- ``generator/audit_log/audit_utils.py``                        — configurable via registry
- ``generator/audit_log/audit_crypto/audit_crypto_ops.py``      — SHA-256 + async streaming
- ``self_fixing_engineer/simulation/utils.py``                  — multi-algo with LRU caching

The stubs each used slightly different error-handling; the streaming version
used a different buffering strategy; the multi-algo version cached without
cache-invalidation on file modification.

Solution
--------
This module provides a single, production-quality implementation with:

* **:func:`compute_hash`** — deterministic hash of ``bytes``, ``str``, or
  ``dict`` (JSON-serialised, keys sorted).
* **:func:`stream_compute_hash`** — async streaming hash over
  ``AsyncIterable[bytes]``.
* **:func:`hash_file`** — file hash with LRU cache that is automatically
  invalidated when ``mtime_ns`` or ``size`` changes.
* **:func:`_hash_key`** — internal cache-invalidation helper (also exported
  for testing).
* **Prometheus counter** — emitted via :func:`shared.noop_metrics.safe_metric`
  with graceful degradation when ``prometheus_client`` is absent.
* **Zero external dependencies at import time** — only stdlib modules.

Architecture
------------
::

    compute_hash(data, algo)            stream_compute_hash(chunks, algo)
         │                                        │
         │ json.dumps / encode                    │ async for chunk
         │                                        │
         ▼                                        ▼
    hashlib.new(algo)              hashlib.new(algo).update(chunk)
         │                                        │
         └──────────────┬─────────────────────────┘
                        │ .hexdigest()
                        ▼
                    hex string

    hash_file(path, algos, chunk_size)
         │
         ├── _hash_key(path) → (mtime_ns, size)   ← cache key
         │
         └── _compute_hash_cached(path, algo, chunk_size, mtime_ns, size)
                   │
                   └── lru_cache(maxsize=128)

Usage
-----
::

    from shared.security.hashing import compute_hash, hash_file, stream_compute_hash

    digest = compute_hash(b"hello world")
    assert len(digest) == 64  # SHA-256 hex

    file_digest = hash_file("/path/to/file.txt")

    async def example():
        async def chunks():
            yield b"hello "
            yield b"world"
        h = await stream_compute_hash(chunks())

Industry Standards Applied
--------------------------
* **NIST FIPS 180-4** — SHA-2 family algorithms (sha256, sha512, etc.).
* **PEP 484** — full type annotations on all public symbols.
* **PEP 517 / 518** — zero mandatory runtime dependencies.
"""

from __future__ import annotations

import hashlib
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, AsyncIterable, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy Prometheus imports
# ---------------------------------------------------------------------------

try:
    from prometheus_client import Counter as _Counter  # type: ignore[import]
except ImportError:
    _Counter = None  # type: ignore[assignment]

from shared.noop_metrics import safe_metric

_HASH_OPS = safe_metric(
    _Counter,
    "hash_operations_total",
    "Total hash operations performed",
    labelnames=["algo", "kind"],
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DEFAULT_CHUNK_SIZE: int = int(os.getenv("HASH_CHUNK_SIZE", "65536"))  # 64 KiB


# ---------------------------------------------------------------------------
# compute_hash — simple bytes/str/dict → hex
# ---------------------------------------------------------------------------


def compute_hash(
    data: Union[str, bytes, Dict[str, Any]], algo: str = "sha256"
) -> str:
    """Compute a cryptographic hash of *data*.

    Parameters
    ----------
    data : str | bytes | dict
        * ``bytes`` — hashed directly.
        * ``str``   — encoded to UTF-8 before hashing.
        * ``dict``  — JSON-serialised with keys sorted for determinism.
    algo : str
        Any algorithm name accepted by :func:`hashlib.new` (default: ``"sha256"``).

    Returns
    -------
    str
        Hexadecimal digest string.

    Raises
    ------
    TypeError
        If *data* is not ``bytes``, ``str``, or ``dict``.
    ValueError
        If *algo* is not a supported algorithm.

    Examples
    --------
    ::

        from shared.security.hashing import compute_hash

        h = compute_hash(b"hello")
        assert len(h) == 64  # SHA-256 produces 32 bytes → 64 hex chars

        h2 = compute_hash({"b": 2, "a": 1})  # keys sorted → deterministic
        assert h2 == compute_hash({"a": 1, "b": 2})
    """
    import json  # local import — keeps top-level dependency-free

    if isinstance(data, dict):
        data_bytes: bytes = json.dumps(data, sort_keys=True).encode("utf-8")
    elif isinstance(data, str):
        data_bytes = data.encode("utf-8")
    elif isinstance(data, bytes):
        data_bytes = data
    else:
        raise TypeError(
            f"compute_hash: unsupported data type {type(data).__name__!r}. "
            "Expected bytes, str, or dict."
        )

    try:
        h = hashlib.new(algo)
    except ValueError as exc:
        raise ValueError(
            f"compute_hash: unsupported algorithm {algo!r}"
        ) from exc

    h.update(data_bytes)
    digest = h.hexdigest()
    try:
        _HASH_OPS.labels(algo=algo, kind="compute").inc()
    except Exception:  # pragma: no cover
        pass
    return digest


# ---------------------------------------------------------------------------
# stream_compute_hash — async streaming hash
# ---------------------------------------------------------------------------


async def stream_compute_hash(
    data_chunks: AsyncIterable[bytes], algo: str = "sha256"
) -> str:
    """Compute a hash over an async iterable of byte chunks.

    Parameters
    ----------
    data_chunks : AsyncIterable[bytes]
        Yields successive byte chunks; the chunks are hashed in order.
    algo : str
        Hash algorithm name (default: ``"sha256"``).

    Returns
    -------
    str
        Hexadecimal digest string.

    Raises
    ------
    ValueError
        If *algo* is not a supported algorithm.

    Examples
    --------
    ::

        async def chunks():
            yield b"hello "
            yield b"world"

        import asyncio
        h = asyncio.run(stream_compute_hash(chunks()))
        assert len(h) == 64
    """
    try:
        h = hashlib.new(algo)
    except ValueError as exc:
        raise ValueError(
            f"stream_compute_hash: unsupported algorithm {algo!r}"
        ) from exc

    async for chunk in data_chunks:
        h.update(chunk)

    digest = h.hexdigest()
    try:
        _HASH_OPS.labels(algo=algo, kind="stream").inc()
    except Exception:  # pragma: no cover
        pass
    return digest


# ---------------------------------------------------------------------------
# hash_file — file hashing with LRU cache
# ---------------------------------------------------------------------------


def _hash_key(path: str) -> Tuple[int, int]:
    """Return ``(mtime_ns, size)`` for *path*, used as LRU cache discriminator.

    When either the modification time or size of the file changes, the tuple
    changes and ``_compute_hash_cached`` is called afresh rather than serving
    the stale cached digest.

    Parameters
    ----------
    path : str
        Absolute path to the file (already resolved by :func:`hash_file`).

    Returns
    -------
    tuple[int, int]
        ``(st_mtime_ns, st_size)`` from :func:`os.stat`.
    """
    st = os.stat(path)
    return (st.st_mtime_ns, st.st_size)


@lru_cache(maxsize=128)
def _compute_hash_cached(
    path: str,
    algo: str,
    chunk_size: int,
    mtime_ns: int,  # noqa: ARG001  — used as cache key only
    size: int,      # noqa: ARG001  — used as cache key only
) -> str:
    """Cached file-hash worker; ``mtime_ns`` and ``size`` act as cache keys."""
    p = Path(path)
    try:
        h = hashlib.new(algo)
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(chunk_size), b""):
                h.update(chunk)
        return h.hexdigest()
    except PermissionError:
        logger.error("Permission denied when accessing file: %s", p)
        raise
    except OSError as exc:
        logger.error("OS error while hashing %s: %s", p, exc)
        raise


def hash_file(
    path: Union[str, Path],
    algos: Union[str, List[str]] = "sha256",
    chunk_size: Optional[int] = None,
) -> Union[str, Dict[str, str]]:
    """Compute the hash(es) of a file with LRU caching.

    Results are cached per ``(path, algo, chunk_size, mtime_ns, size)`` tuple.
    The cache is automatically invalidated when the file's modification time or
    size changes.

    Parameters
    ----------
    path : str | Path
        Path to the file to hash.
    algos : str | list[str]
        A single algorithm name or a list of names.  When a ``list`` is given
        a ``dict`` mapping ``{algo: digest}`` is returned.
    chunk_size : int | None
        Read buffer size in bytes.  Defaults to the ``HASH_CHUNK_SIZE``
        environment variable (or 65536 bytes if unset).

    Returns
    -------
    str | dict[str, str]
        A single hex-digest when *algos* is a ``str``; a mapping when *algos*
        is a ``list``.

    Raises
    ------
    FileNotFoundError
        If *path* does not exist or is not a regular file.
    ValueError
        If *chunk_size* is not a positive integer or *algos* contains an
        unsupported algorithm.

    Examples
    --------
    ::

        from shared.security.hashing import hash_file

        digest = hash_file("/etc/hostname")
        assert isinstance(digest, str)

        digests = hash_file("/etc/hostname", algos=["sha256", "md5"])
        assert "sha256" in digests and "md5" in digests
    """
    if chunk_size is None:
        chunk_size = _DEFAULT_CHUNK_SIZE
    if not isinstance(chunk_size, int) or chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer")

    p = Path(path).resolve()
    if not p.is_file():
        raise FileNotFoundError(f"File not found: {p}")

    algorithms: List[str] = [algos] if isinstance(algos, str) else list(algos)
    for algo in algorithms:
        try:
            hashlib.new(algo)
        except ValueError as exc:
            raise ValueError(f"Invalid hashing algorithm: {algo!r}") from exc

    mtime_ns, size = _hash_key(str(p))
    path_str = str(p)

    if isinstance(algos, str):
        digest = _compute_hash_cached(path_str, algos, chunk_size, mtime_ns, size)
        logger.debug("Computed %s hash for %s", algos, p)
        try:
            _HASH_OPS.labels(algo=algos, kind="file").inc()
        except Exception:  # pragma: no cover
            pass
        return digest

    result: Dict[str, str] = {
        algo: _compute_hash_cached(path_str, algo, chunk_size, mtime_ns, size)
        for algo in algorithms
    }
    logger.debug("Computed multiple hashes for %s: %s", p, list(result.keys()))
    for algo in algorithms:
        try:
            _HASH_OPS.labels(algo=algo, kind="file").inc()
        except Exception:  # pragma: no cover
            pass
    return result


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "compute_hash",
    "stream_compute_hash",
    "hash_file",
    "_hash_key",
]
