# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Post-Quantum Cryptography (PQC) Signing Module.

Provides a production-grade :class:`PQCSigner` that wraps NIST PQC algorithms
when ``pqcrypto`` (CRYSTALS-Dilithium2) or ``liboqs-python`` (Dilithium3) is
available, and gracefully degrades to HMAC-SHA-256 via the ``cryptography``
package when those optional libraries are not installed.

Architecture
------------
::

    ┌──────────────────────────────────────────────────┐
    │                  PQCSigner                       │
    │                                                  │
    │  sign(data) ──► _backend ──► pqcrypto  (PQC)    │
    │                          ──► liboqs    (PQC)    │
    │                          ──► hmac      (fallback)│
    │  verify(data, sig) ◄── same dispatch            │
    └──────────────────────────────────────────────────┘

Environment Variables
---------------------
USE_PQC_SIGNING
    Set to ``"true"`` to enable PQC library lookup and signing.
    Defaults to ``"false"``.  When ``"false"`` the signer always uses the
    HMAC-SHA-256 fallback regardless of which libraries are installed.

ARBITER_ENCRYPTION_KEY
    Raw string used as the HMAC key in fallback mode.  If absent, a
    deterministic development key is derived at import time.

Observability
-------------
*Prometheus* counters/histograms track every sign/verify operation and
their outcomes.  All metrics are prefixed ``pqc_signer_``.

*OpenTelemetry* spans are created for each :meth:`sign` and :meth:`verify`
call when a running tracer provider is detected.

Graceful Degradation
--------------------
If a PQC library is not installed *and* ``USE_PQC_SIGNING=true``, a
``WARNING`` is logged and the backend silently reverts to HMAC-SHA-256
so that the platform continues to operate without interruption.

Usage
-----
::

    from self_fixing_engineer.arbiter.arbiter_growth.pqc_signing import get_signer

    signer = get_signer()
    sig = signer.sign(b"audit log payload")
    assert signer.verify(b"audit log payload", sig)
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import threading
import time
from typing import Optional

# ---------------------------------------------------------------------------
# Observability — Prometheus (graceful no-op when not installed)
# ---------------------------------------------------------------------------

try:
    from prometheus_client import Counter, Histogram

    _PQC_SIGN_TOTAL = Counter(
        "pqc_signer_sign_total",
        "Total sign() invocations by backend",
        ["backend"],
    )
    _PQC_VERIFY_TOTAL = Counter(
        "pqc_signer_verify_total",
        "Total verify() invocations by backend and result",
        ["backend", "result"],
    )
    _PQC_SIGN_LATENCY = Histogram(
        "pqc_signer_sign_latency_seconds",
        "Latency of sign() operations",
        ["backend"],
        buckets=(0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0),
    )
    _PROMETHEUS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PROMETHEUS_AVAILABLE = False

    class _NoOpCounter:
        def labels(self, **_):
            return self

        def inc(self, *_):
            pass

    class _NoOpHistogram:
        def labels(self, **_):
            return self

        def observe(self, *_):
            pass

    _PQC_SIGN_TOTAL = _NoOpCounter()  # type: ignore[assignment]
    _PQC_VERIFY_TOTAL = _NoOpCounter()  # type: ignore[assignment]
    _PQC_SIGN_LATENCY = _NoOpHistogram()  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Observability — OpenTelemetry (graceful no-op when not installed)
# ---------------------------------------------------------------------------

try:
    from self_fixing_engineer.arbiter.otel_config import get_tracer_safe as _get_tracer_safe

    _tracer = _get_tracer_safe(__name__)
    _OTEL_AVAILABLE = True
except Exception:  # pragma: no cover — OTel unavailable in minimal envs
    _OTEL_AVAILABLE = False

    class _NoOpSpan:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def set_attribute(self, *_):
            pass

        def record_exception(self, *_):
            pass

    class _NoOpTracer:
        def start_as_current_span(self, *_, **__):
            return _NoOpSpan()

    _tracer = _NoOpTracer()  # type: ignore[assignment]


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional PQC library detection
# ---------------------------------------------------------------------------
# Libraries are detected exactly once at module import time so that repeated
# get_signer() calls pay no detection overhead.

_pqcrypto_fns: Optional[tuple] = None  # (generate_keypair, sign, verify)
_liboqs_mod: Optional[object] = None

_PQC_LIB: str = "none"

if os.environ.get("USE_PQC_SIGNING", "false").lower() == "true":
    # Attempt 1: pqcrypto — CRYSTALS-Dilithium2 (NIST PQC finalist)
    try:
        from pqcrypto.sign.dilithium2 import (  # type: ignore[import]
            generate_keypair,
            sign,
            verify,
        )

        _pqcrypto_fns = (generate_keypair, sign, verify)
        _PQC_LIB = "pqcrypto"
        logger.info(
            "PQC signing enabled via pqcrypto (CRYSTALS-Dilithium2). "
            "backend=%s",
            _PQC_LIB,
        )
    except ImportError:
        logger.debug("pqcrypto not available; will attempt liboqs next.")

    if _PQC_LIB == "none":
        # Attempt 2: liboqs-python — Open Quantum Safe Dilithium3
        try:
            import oqs  # type: ignore[import]

            _liboqs_mod = oqs
            _PQC_LIB = "liboqs"
            logger.info(
                "PQC signing enabled via liboqs (Dilithium3). backend=%s",
                _PQC_LIB,
            )
        except ImportError:
            logger.debug("liboqs-python not available.")

    if _PQC_LIB == "none":
        logger.warning(
            "USE_PQC_SIGNING=true but neither pqcrypto nor liboqs-python is "
            "installed.  Falling back to HMAC-SHA-256.  "
            "Install one of: pqcrypto>=0.1.3 OR liboqs-python>=0.12.0 "
            "(see requirements-pqc.txt)."
        )


# ---------------------------------------------------------------------------
# PQCSigner
# ---------------------------------------------------------------------------


class PQCSigner:
    """
    Quantum-resistant (or HMAC-SHA-256 fallback) signing interface.

    The active backend is chosen once at module import time based on the
    ``USE_PQC_SIGNING`` environment variable and installed libraries:

    * **pqcrypto** – CRYSTALS-Dilithium2 (NIST PQC finalist). Stateful:
      a keypair is generated per instance at construction time.
    * **liboqs** – Dilithium3 via the Open Quantum Safe project.  Stateful:
      same lifecycle as pqcrypto.
    * **hmac** – HMAC-SHA-256 (always available, stateless per key).

    All backends expose the same :meth:`sign` / :meth:`verify` interface so
    callers are fully decoupled from the underlying cryptographic primitive.

    Thread Safety
    ~~~~~~~~~~~~~
    Instance construction is *not* thread-safe.  Use :func:`get_signer` which
    returns a module-level singleton protected by a :class:`threading.Lock`.

    Prometheus Metrics
    ~~~~~~~~~~~~~~~~~~
    * ``pqc_signer_sign_total`` — counter, labelled by ``backend``.
    * ``pqc_signer_verify_total`` — counter, labelled by ``backend`` and
      ``result`` (``"ok"`` | ``"invalid"``).
    * ``pqc_signer_sign_latency_seconds`` — histogram, labelled by
      ``backend``.

    Args:
        hmac_key: Raw bytes used as the HMAC key when operating in fallback
            mode.  Defaults to ``ARBITER_ENCRYPTION_KEY`` env-var value, or a
            deterministic development key when that variable is absent.
    """

    def __init__(self, hmac_key: Optional[bytes] = None) -> None:
        self._hmac_key: bytes = hmac_key or self._default_hmac_key()

        # PQC key material — initialised by backend-specific _init_* methods.
        self._pk: Optional[bytes] = None
        self._sk: Optional[bytes] = None
        self._liboqs_signer: Optional[object] = None  # liboqs.Signature instance

        # Resolve backend: use the best available PQC library, or fall back.
        if _PQC_LIB in {"pqcrypto", "liboqs"}:
            self._backend: str = _PQC_LIB
        else:
            self._backend = "hmac"

        if self._backend == "pqcrypto":
            self._init_pqcrypto()
        elif self._backend == "liboqs":
            self._init_liboqs()

        logger.debug(
            "PQCSigner initialised. backend=%s prometheus=%s otel=%s",
            self._backend,
            _PROMETHEUS_AVAILABLE,
            _OTEL_AVAILABLE,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def sign(self, data: bytes) -> bytes:
        """
        Create a cryptographic signature over *data*.

        The exact signature encoding depends on the active backend:

        * **pqcrypto / liboqs** — raw PQC signature bytes (variable length).
        * **hmac** — HMAC-SHA-256 digest encoded as 64 lowercase ASCII hex
          characters.

        Args:
            data: Arbitrary byte string to sign.  Must not be empty for
                meaningful security guarantees.

        Returns:
            Signature as a raw :class:`bytes` object.

        Raises:
            TypeError: If *data* is not a :class:`bytes` instance.
            RuntimeError: If the PQC key pair has not been initialised
                (should never happen under normal use).
        """
        if not isinstance(data, bytes):
            raise TypeError(f"sign() expects bytes, got {type(data).__name__}")

        t0 = time.perf_counter()
        with _tracer.start_as_current_span(
            "pqc_signer.sign",
            attributes={"pqc.backend": self._backend, "pqc.data_len": len(data)},
        ):
            result = self._dispatch_sign(data)

        latency = time.perf_counter() - t0
        _PQC_SIGN_TOTAL.labels(backend=self._backend).inc()
        _PQC_SIGN_LATENCY.labels(backend=self._backend).observe(latency)
        return result

    def verify(self, data: bytes, sig: bytes) -> bool:
        """
        Verify that *sig* is a valid signature over *data*.

        This method is constant-time for the HMAC backend (via
        :func:`hmac.compare_digest`) and delegates to library-specific
        constant-time routines for PQC backends.

        Args:
            data: The original byte string that was signed.
            sig: The signature previously returned by :meth:`sign`.

        Returns:
            ``True`` if the signature is valid, ``False`` otherwise.
            This method *never* raises for invalid or malformed signatures;
            all exceptions are caught and logged, and ``False`` is returned.
        """
        if not isinstance(data, bytes) or not isinstance(sig, bytes):
            return False

        with _tracer.start_as_current_span(
            "pqc_signer.verify",
            attributes={"pqc.backend": self._backend, "pqc.data_len": len(data)},
        ):
            ok = self._dispatch_verify(data, sig)

        result_label = "ok" if ok else "invalid"
        _PQC_VERIFY_TOTAL.labels(backend=self._backend, result=result_label).inc()
        return ok

    @property
    def backend(self) -> str:
        """Name of the active backend: ``"pqcrypto"``, ``"liboqs"``, or ``"hmac"``."""
        return self._backend

    # ------------------------------------------------------------------
    # Internal dispatch
    # ------------------------------------------------------------------

    def _dispatch_sign(self, data: bytes) -> bytes:
        if self._backend == "pqcrypto":
            return self._sign_pqcrypto(data)
        if self._backend == "liboqs":
            return self._sign_liboqs(data)
        return self._sign_hmac(data)

    def _dispatch_verify(self, data: bytes, sig: bytes) -> bool:
        if self._backend == "pqcrypto":
            return self._verify_pqcrypto(data, sig)
        if self._backend == "liboqs":
            return self._verify_liboqs(data, sig)
        return self._verify_hmac(data, sig)

    # ------------------------------------------------------------------
    # pqcrypto backend
    # ------------------------------------------------------------------

    def _init_pqcrypto(self) -> None:
        """Generate a Dilithium2 keypair via pqcrypto."""
        assert _pqcrypto_fns is not None, "pqcrypto functions not loaded"
        generate_keypair, _, _ = _pqcrypto_fns
        self._pk, self._sk = generate_keypair()
        logger.debug("PQCSigner: Dilithium2 keypair generated.")

    def _sign_pqcrypto(self, data: bytes) -> bytes:
        assert _pqcrypto_fns is not None
        assert self._sk is not None, "pqcrypto secret key not initialised"
        _, sign_fn, _ = _pqcrypto_fns
        return sign_fn(data, self._sk)

    def _verify_pqcrypto(self, data: bytes, sig: bytes) -> bool:
        assert _pqcrypto_fns is not None
        assert self._pk is not None, "pqcrypto public key not initialised"
        _, _, verify_fn = _pqcrypto_fns
        try:
            verify_fn(data, sig, self._pk)
            return True
        except Exception as exc:
            logger.debug("pqcrypto verify failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # liboqs backend
    # ------------------------------------------------------------------

    def _init_liboqs(self) -> None:
        """Generate a Dilithium3 keypair via liboqs."""
        assert _liboqs_mod is not None, "liboqs module not loaded"
        self._liboqs_signer = _liboqs_mod.Signature("Dilithium3")  # type: ignore[attr-defined]
        self._pk = self._liboqs_signer.generate_keypair()  # type: ignore[union-attr]
        logger.debug("PQCSigner: Dilithium3 keypair generated via liboqs.")

    def _sign_liboqs(self, data: bytes) -> bytes:
        assert self._liboqs_signer is not None, "liboqs signer not initialised"
        return self._liboqs_signer.sign(data)  # type: ignore[attr-defined]

    def _verify_liboqs(self, data: bytes, sig: bytes) -> bool:
        assert self._liboqs_signer is not None, "liboqs signer not initialised"
        assert self._pk is not None, "liboqs public key not initialised"
        try:
            return bool(self._liboqs_signer.verify(data, sig, self._pk))  # type: ignore[attr-defined]
        except Exception as exc:
            logger.debug("liboqs verify failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # HMAC-SHA-256 fallback
    # ------------------------------------------------------------------

    def _sign_hmac(self, data: bytes) -> bytes:
        """Return HMAC-SHA-256(key, data) encoded as 64 ASCII hex bytes."""
        digest = hmac.new(self._hmac_key, data, hashlib.sha256).hexdigest()
        return digest.encode("ascii")

    def _verify_hmac(self, data: bytes, sig: bytes) -> bool:
        """Constant-time comparison of expected vs. provided HMAC."""
        expected = self._sign_hmac(data)
        return hmac.compare_digest(expected, sig)

    # ------------------------------------------------------------------
    # Key derivation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _default_hmac_key() -> bytes:
        """
        Derive the HMAC key from the environment or a deterministic dev key.

        Priority:
        1. ``ARBITER_ENCRYPTION_KEY`` environment variable (production).
        2. Deterministic SHA-256 of a fixed constant (development only).
           This MUST NOT be used in production and is logged as a warning.
        """
        raw = os.environ.get("ARBITER_ENCRYPTION_KEY", "")
        if raw:
            return raw.encode("utf-8")
        logger.warning(
            "PQCSigner: ARBITER_ENCRYPTION_KEY is not set. "
            "Using deterministic dev key — NOT suitable for production."
        )
        return hashlib.sha256(b"pqc-signing-dev-key-novatrax-2025").digest()


# ---------------------------------------------------------------------------
# Module-level singleton — thread-safe lazy initialisation
# ---------------------------------------------------------------------------

_singleton: Optional[PQCSigner] = None
_singleton_lock: threading.Lock = threading.Lock()


def get_signer() -> PQCSigner:
    """
    Return the module-level :class:`PQCSigner` singleton.

    The instance is created lazily on the first call and then cached for
    the lifetime of the process.  A :class:`threading.Lock` ensures that
    concurrent first-calls do not create duplicate instances.

    Returns:
        The shared :class:`PQCSigner` instance.

    Example::

        signer = get_signer()
        sig = signer.sign(b"audit record")
        assert signer.verify(b"audit record", sig)
    """
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:  # double-checked locking
                _singleton = PQCSigner()
                logger.info(
                    "PQCSigner singleton created. backend=%s", _singleton.backend
                )
    return _singleton
