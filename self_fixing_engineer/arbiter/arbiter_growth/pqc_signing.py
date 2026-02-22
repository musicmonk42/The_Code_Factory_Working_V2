# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Post-Quantum Cryptography (PQC) Signing Module.

Provides a ``PQCSigner`` class that wraps NIST PQC algorithms when the
``pqcrypto`` or ``liboqs`` library is available, and gracefully falls back
to HMAC-SHA-256 via the ``cryptography`` package when those optional
libraries are not installed.

Environment Variables:
    USE_PQC_SIGNING (str): Set to ``"true"`` to enable PQC signing.
        Defaults to ``"false"``.  When ``false``, the signer always uses
        the HMAC-SHA-256 fallback regardless of which libraries are installed.

Usage::

    from self_fixing_engineer.arbiter.arbiter_growth.pqc_signing import get_signer

    signer = get_signer()
    sig = signer.sign(b"my audit data")
    assert signer.verify(b"my audit data", sig)
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional PQC library detection
# ---------------------------------------------------------------------------

_pqcrypto_sign: Optional[object] = None
_liboqs_sig: Optional[object] = None

_PQC_LIB: str = "none"

if os.environ.get("USE_PQC_SIGNING", "false").lower() == "true":
    # Try pqcrypto first (CRYSTALS-Dilithium)
    try:
        from pqcrypto.sign.dilithium2 import generate_keypair, sign, verify  # type: ignore[import]

        _pqcrypto_sign = (generate_keypair, sign, verify)
        _PQC_LIB = "pqcrypto"
        logger.info("PQC signing enabled via pqcrypto (Dilithium2).")
    except ImportError:
        pass

    if _PQC_LIB == "none":
        # Try liboqs-python (OQS-Dilithium3)
        try:
            import oqs  # type: ignore[import]

            _liboqs_sig = oqs
            _PQC_LIB = "liboqs"
            logger.info("PQC signing enabled via liboqs (Dilithium3).")
        except ImportError:
            pass

    if _PQC_LIB == "none":
        logger.warning(
            "USE_PQC_SIGNING=true but neither pqcrypto nor liboqs-python is installed. "
            "Falling back to HMAC-SHA-256.  Install pqcrypto>=0.1.3 or liboqs-python "
            "to enable quantum-resistant signing."
        )


# ---------------------------------------------------------------------------
# PQCSigner
# ---------------------------------------------------------------------------


class PQCSigner:
    """
    Quantum-resistant (or HMAC fallback) signing interface.

    Exposes :meth:`sign` and :meth:`verify` regardless of which backend is
    active so that callers do not need to know which library is in use.

    The active backend is determined once at module import time based on
    ``USE_PQC_SIGNING`` and library availability:

    * **pqcrypto** – CRYSTALS-Dilithium2 (NIST PQC finalist).
    * **liboqs** – Dilithium3 via the Open Quantum Safe project.
    * **hmac** – HMAC-SHA-256 fallback (always available).

    Args:
        hmac_key: Raw bytes used as the HMAC key when operating in fallback
            mode.  Ignored when a PQC library is active.  Defaults to a
            deterministic development key if *None*.
    """

    def __init__(self, hmac_key: Optional[bytes] = None) -> None:
        self._hmac_key: bytes = hmac_key or self._default_hmac_key()

        # PQC key pairs are generated lazily and cached per-instance.
        self._pk: Optional[bytes] = None
        self._sk: Optional[bytes] = None

        # Resolve backend: use the best available PQC library, or fall back to HMAC.
        if _PQC_LIB in {"pqcrypto", "liboqs"}:
            self._backend: str = _PQC_LIB
        else:
            self._backend = "hmac"

        if self._backend == "pqcrypto":
            self._init_pqcrypto()
        elif self._backend == "liboqs":
            self._init_liboqs()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def sign(self, data: bytes) -> bytes:
        """
        Create a signature over *data*.

        Args:
            data: Arbitrary byte string to sign.

        Returns:
            A signature as a raw :class:`bytes` object.  The exact format
            depends on the active backend (PQC signature bytes or a
            HMAC-SHA-256 hex digest encoded as ASCII bytes).
        """
        if self._backend == "pqcrypto":
            return self._sign_pqcrypto(data)
        if self._backend == "liboqs":
            return self._sign_liboqs(data)
        return self._sign_hmac(data)

    def verify(self, data: bytes, sig: bytes) -> bool:
        """
        Verify that *sig* is a valid signature over *data*.

        Args:
            data: The original byte string that was signed.
            sig: The signature previously returned by :meth:`sign`.

        Returns:
            ``True`` if the signature is valid, ``False`` otherwise.
            Never raises an exception for invalid signatures.
        """
        if self._backend == "pqcrypto":
            return self._verify_pqcrypto(data, sig)
        if self._backend == "liboqs":
            return self._verify_liboqs(data, sig)
        return self._verify_hmac(data, sig)

    @property
    def backend(self) -> str:
        """Name of the active backend: ``"pqcrypto"``, ``"liboqs"``, or ``"hmac"``."""
        return self._backend

    # ------------------------------------------------------------------
    # pqcrypto backend
    # ------------------------------------------------------------------

    def _init_pqcrypto(self) -> None:
        assert _pqcrypto_sign is not None
        generate_keypair, _, _ = _pqcrypto_sign  # type: ignore[misc]
        self._pk, self._sk = generate_keypair()

    def _sign_pqcrypto(self, data: bytes) -> bytes:
        assert _pqcrypto_sign is not None
        assert self._sk is not None
        _, sign_fn, _ = _pqcrypto_sign  # type: ignore[misc]
        return sign_fn(data, self._sk)

    def _verify_pqcrypto(self, data: bytes, sig: bytes) -> bool:
        assert _pqcrypto_sign is not None
        assert self._pk is not None
        _, _, verify_fn = _pqcrypto_sign  # type: ignore[misc]
        try:
            verify_fn(data, sig, self._pk)
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # liboqs backend
    # ------------------------------------------------------------------

    def _init_liboqs(self) -> None:
        assert _liboqs_sig is not None
        oqs = _liboqs_sig
        self._liboqs_signer = oqs.Signature("Dilithium3")
        self._pk = self._liboqs_signer.generate_keypair()

    def _sign_liboqs(self, data: bytes) -> bytes:
        return self._liboqs_signer.sign(data)

    def _verify_liboqs(self, data: bytes, sig: bytes) -> bool:
        assert self._pk is not None
        try:
            return bool(self._liboqs_signer.verify(data, sig, self._pk))
        except Exception:
            return False

    # ------------------------------------------------------------------
    # HMAC-SHA-256 fallback
    # ------------------------------------------------------------------

    def _sign_hmac(self, data: bytes) -> bytes:
        digest = hmac.new(self._hmac_key, data, hashlib.sha256).hexdigest()
        return digest.encode("ascii")

    def _verify_hmac(self, data: bytes, sig: bytes) -> bool:
        expected = self._sign_hmac(data)
        return hmac.compare_digest(expected, sig)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _default_hmac_key() -> bytes:
        """Return the HMAC key from env or a deterministic development key."""
        raw = os.environ.get("ARBITER_ENCRYPTION_KEY", "")
        if raw:
            return raw.encode("utf-8")
        # Deterministic dev key derived from a fixed constant — NOT for production.
        return hashlib.sha256(b"pqc-signing-dev-key-novatrax-2025").digest()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_singleton: Optional[PQCSigner] = None


def get_signer() -> PQCSigner:
    """
    Return a module-level singleton :class:`PQCSigner`.

    The singleton is created lazily on first call.  This avoids generating
    PQC key pairs on every import, which can be expensive.

    Returns:
        The shared :class:`PQCSigner` instance.
    """
    global _singleton
    if _singleton is None:
        _singleton = PQCSigner()
    return _singleton
