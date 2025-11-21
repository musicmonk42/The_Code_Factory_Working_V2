"""
Enterprise-grade security utilities for omnicore_engine.

This module centralizes cryptography, token/session management, input
sanitization, and lightweight auth decorators with minimal runtime
dependencies. It is designed to be resilient in environments where some
third-party packages may be missing (e.g., `bleach`, `pyotp`) while still
providing safe fallbacks so the test suite can import the module without
failing.

If optional packages are available at runtime, they are used automatically.
Otherwise, safe, standard-library fallbacks are applied.

Author: ChatGPT (GPT-5 Thinking)
"""

from __future__ import annotations

import base64
import binascii
import dataclasses
import functools
import hmac
import html
import json
import logging
import os
import re
import secrets
import threading
import time
import typing as _t
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum, auto

# Optional deps: we try to import and fall back if missing
try:  # HTML sanitizer (optional)
    import bleach  # type: ignore
except Exception:  # pragma: no cover - optional dep
    bleach = None  # type: ignore

try:  # TOTP (optional)
    import pyotp  # type: ignore
except Exception:  # pragma: no cover - optional dep
    pyotp = None  # type: ignore

# Cryptography (required by some functions, but we guard its use)
try:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    _CRYPTO_AVAILABLE = True
except Exception:  # pragma: no cover - if not available, we still allow module import
    _CRYPTO_AVAILABLE = False


__all__ = [
    # Exceptions
    "SecurityError",
    "SecurityException",
    "AuthenticationError",
    "AuthorizationError",
    "TokenExpiredError",
    "EncryptionError",
    "DecryptionError",
    "RateLimitError",
    "ValidationError",
    # Enums / dataclasses
    "HashAlgorithm",
    "EncryptionAlgorithm",
    "Token",
    # Core utilities class
    "EnterpriseSecurityUtils",
    # Helpers / singletons
    "get_security_utils",
    # Decorators
    "require_authentication",
    "require_authorization",
    # Standalone convenient functions (proxy to EnterpriseSecurityUtils singleton)
    "hash_password",
    "verify_password",
    "generate_token",
    "verify_token",
    "encrypt",
    "decrypt",
    "sanitize_html",
    "create_session",
    "get_session",
    "revoke_session",
    "refresh_session",
    # Rate limiter helpers
    "RateLimiter",
    # Audit
    "SecurityAuditLogger",
]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SecurityError(Exception):
    """Base class for security related errors."""


# Alias for backward compatibility
SecurityException = SecurityError


class AuthenticationError(SecurityError):
    """Raised when authentication is required but missing/invalid."""


class AuthorizationError(SecurityError):
    """Raised when user lacks required privileges."""


class TokenExpiredError(SecurityError):
    """Raised when a token is expired or otherwise invalid for time-related reasons."""


class EncryptionError(SecurityError):
    """Raised when encryption fails."""


class DecryptionError(SecurityError):
    """Raised when decryption fails."""


class RateLimitError(SecurityError):
    """Raised when a rate limiter blocks an action."""


class ValidationError(SecurityError):
    """Raised when input validation fails."""


# ---------------------------------------------------------------------------
# Enums and data classes
# ---------------------------------------------------------------------------

class HashAlgorithm(Enum):
    """Supported password hashing algorithms."""
    PBKDF2_SHA256 = auto()


class EncryptionAlgorithm(Enum):
    """Supported symmetric encryption algorithms."""
    AES_GCM = auto()


@dataclass(frozen=True)
class Token:
    """Represents a signed, optionally-expiring token."""
    payload: dict
    issued_at: int
    expires_at: int
    nonce: str
    signature: str


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))


def _require_crypto() -> None:
    if not _CRYPTO_AVAILABLE:
        raise RuntimeError(
            "The 'cryptography' package is required for this operation "
            "but is not available in the current environment."
        )


def _hkdf_derive_key(secret: _t.Union[str, bytes], salt: _t.Optional[bytes] = None, length: int = 32) -> bytes:
    """Derive a fixed-length key from arbitrary secret using PBKDF2HMAC (HKDF alternative)."""
    _require_crypto()
    if isinstance(secret, str):
        secret_bytes = secret.encode("utf-8")
    else:
        secret_bytes = secret
    if salt is None:
        # deterministic salt from secret length & a constant label
        salt = hashlib_sha256(b"omnicore_engine.hkdf." + str(len(secret_bytes)).encode("ascii"))
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=length, salt=salt, iterations=200_000)
    return kdf.derive(secret_bytes)


def hashlib_sha256(data: bytes) -> bytes:
    # Use stdlib hashlib to avoid importing twice
    import hashlib as _hashlib
    h = _hashlib.sha256()
    h.update(data)
    return h.digest()


def constant_time_compare(a: _t.Union[str, bytes], b: _t.Union[str, bytes]) -> bool:
    """Constant-time string/bytes comparison."""
    if isinstance(a, str):
        a = a.encode("utf-8")
    if isinstance(b, str):
        b = b.encode("utf-8")
    return hmac.compare_digest(a, b)


# ---------------------------------------------------------------------------
# HTML Sanitization
# ---------------------------------------------------------------------------

_ALLOWED_TAGS = [
    "p", "strong", "em", "ul", "ol", "li", "br", "a", "code", "pre", "span"
]
_ALLOWED_ATTRIBUTES = {
    "a": ["href", "title", "target", "rel"],
    # Ignore all others
}

def _fallback_sanitize_html(html_text: str) -> str:
    """
    Very small HTML sanitizer that:
      * removes <script>...</script> blocks entirely
      * strips all tags not in the allowed list
      * removes all attributes from allowed tags except a[href|title|target|rel]
      * escapes textual data by default
    NOTE: This is intentionally conservative.
    """
    # Remove script/style blocks entirely
    html_text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", html_text)

    from html.parser import HTMLParser

    class _Sanitizer(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.out: _t.List[str] = []

        def handle_starttag(self, tag, attrs):
            tag = tag.lower()
            if tag not in _ALLOWED_TAGS:
                return
            if tag == "a":
                # Keep only safe href
                safe_attrs = []
                for (k, v) in attrs:
                    k = k.lower()
                    if k == "href":
                        v = (v or "")
                        if isinstance(v, bytes):
                            v = v.decode("utf-8", "ignore")
                        v = v.strip()
                        if v.startswith(("http://", "https://", "#", "mailto:")):
                            safe_attrs.append((k, html.escape(v, quote=True)))
                    elif k in ("title", "target", "rel"):
                        if v is not None:
                            safe_attrs.append((k, html.escape(str(v), quote=True)))
                attr_str = "".join(f' {k}="{v}"' for k, v in safe_attrs)
                self.out.append(f"<a{attr_str}>")
            else:
                self.out.append(f"<{tag}>")

        def handle_endtag(self, tag):
            tag = tag.lower()
            if tag in _ALLOWED_TAGS:
                self.out.append(f"</{tag}>")

        def handle_data(self, data):
            self.out.append(html.escape(data))

        def handle_entityref(self, name):
            self.out.append(f"&{name};")

        def handle_charref(self, name):
            self.out.append(f"&#{name};")

        def handle_comment(self, data):
            # strip comments
            pass

    parser = _Sanitizer()
    parser.feed(html_text)
    parser.close()
    return "".join(parser.out)


def sanitize_html(html_text: str) -> str:
    """
    Sanitize HTML, preferring `bleach` if available. Falls back to a conservative
    built-in sanitizer if `bleach` is not installed.
    """
    if bleach is not None:  # pragma: no cover - exercised in environments with bleach
        return bleach.clean(
            html_text,
            tags=_ALLOWED_TAGS,
            attributes=_ALLOWED_ATTRIBUTES,
            strip=True,
        )
    # Fallback (no third-party dependency)
    return _fallback_sanitize_html(html_text)


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

def _pbkdf2_sha256(password: str, salt: bytes, iterations: int) -> bytes:
    _require_crypto()
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=int(iterations),
    )
    return kdf.derive(password.encode("utf-8"))


def hash_password(
    password: str,
    *,
    iterations: int = 390_000,
    salt: _t.Optional[bytes] = None,
    algorithm: HashAlgorithm = HashAlgorithm.PBKDF2_SHA256,
) -> str:
    """
    Hash a password using the selected algorithm. The output format is:

        pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>

    The salt is 16 random bytes if not provided.
    """
    if algorithm is not HashAlgorithm.PBKDF2_SHA256:
        raise ValueError(f"Unsupported algorithm: {algorithm}")

    _require_crypto()
    if salt is None:
        salt = os.urandom(16)
    dk = _pbkdf2_sha256(password, salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations,
        _b64url_encode(salt),
        _b64url_encode(dk),
    )


def verify_password(password: str, stored: str) -> bool:
    """
    Verify a password against the stored hash format emitted by `hash_password`.
    """
    try:
        algo, iterations_s, salt_b64, hash_b64 = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        iterations = int(iterations_s)
        salt = _b64url_decode(salt_b64)
        expected = _b64url_decode(hash_b64)
        candidate = _pbkdf2_sha256(password, salt, iterations)
        return constant_time_compare(candidate, expected)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Token: HMAC-SHA256 signed JSON with exp and iat
# ---------------------------------------------------------------------------

def _sign(data: bytes, key: _t.Union[str, bytes]) -> str:
    if isinstance(key, str):
        key = key.encode("utf-8")
    sig = hmac.new(key, data, digestmod="sha256").digest()
    return _b64url_encode(sig)


def _verify_signature(data: bytes, sig_b64: str, key: _t.Union[str, bytes]) -> bool:
    actual = _sign(data, key)
    return constant_time_compare(actual, sig_b64)


def generate_token(
    payload: dict,
    *,
    secret: _t.Union[str, bytes],
    ttl_seconds: int = 3600,
    include_nonce: bool = True,
) -> str:
    """
    Create a compact, URL-safe token made of:

        <b64url(header)>. <b64url(payload)>. <b64url(signature)>

    where:
      header = {"alg":"HS256","typ":"OCET"}  # OmniCore Engine Token
      payload = {"exp": <unix>, "iat": <unix>, "nonce": "...", **payload}

    The signature covers "header.payload" using HMAC-SHA256 with `secret`.
    """
    iat = int(time.time())
    exp = iat + int(ttl_seconds)
    body = dict(payload or {})
    if include_nonce:
        body.setdefault("nonce", _b64url_encode(os.urandom(16)))
    body["iat"] = iat
    body["exp"] = exp

    header = {"alg": "HS256", "typ": "OCET"}
    head_b64 = _b64url_encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    payload_b = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    pay_b64 = _b64url_encode(payload_b)
    signing_input = f"{head_b64}.{pay_b64}".encode("ascii")
    sig = _sign(signing_input, secret)
    return f"{head_b64}.{pay_b64}.{sig}"


def verify_token(token: str, *, secret: _t.Union[str, bytes], leeway: int = 0) -> dict:
    """
    Verify a token created by `generate_token`. Returns the payload dict
    if valid; raises TokenExpiredError on expiration; raises AuthenticationError
    on signature/format issues.
    """
    try:
        head_b64, pay_b64, sig = token.split(".", 3)
    except ValueError:
        raise AuthenticationError("Malformed token")

    if not _verify_signature(f"{head_b64}.{pay_b64}".encode("ascii"), sig, secret):
        raise AuthenticationError("Invalid token signature")

    try:
        payload = json.loads(_b64url_decode(pay_b64))
    except Exception as e:
        raise AuthenticationError("Invalid token payload") from e

    now = int(time.time())
    exp = int(payload.get("exp", 0))
    if now > (exp + int(leeway)):
        raise TokenExpiredError("Token has expired")
    return payload


# ---------------------------------------------------------------------------
# Symmetric encryption: AES-GCM
# ---------------------------------------------------------------------------

def encrypt(
    plaintext: _t.Union[str, bytes],
    *,
    key: _t.Union[str, bytes],
    algorithm: EncryptionAlgorithm = EncryptionAlgorithm.AES_GCM,
) -> str:
    """
    Encrypt bytes or string using AES-GCM with a 256-bit key derived from `key`.
    Returns URL-safe base64 string: b64url(nonce || ciphertext || tag)
    """
    if algorithm is not EncryptionAlgorithm.AES_GCM:
        raise ValueError("Only AES_GCM is supported")

    _require_crypto()
    if isinstance(plaintext, str):
        data = plaintext.encode("utf-8")
    else:
        data = plaintext

    # Derive a 32-byte key from the provided secret/string
    k = _hkdf_derive_key(key, length=32)
    nonce = os.urandom(12)
    aesgcm = AESGCM(k)
    ct = aesgcm.encrypt(nonce, data, associated_data=None)
    return _b64url_encode(nonce + ct)


def decrypt(
    token: str,
    *,
    key: _t.Union[str, bytes],
    algorithm: EncryptionAlgorithm = EncryptionAlgorithm.AES_GCM,
) -> bytes:
    """
    Decrypt a token produced by `encrypt`. Returns raw bytes (caller can .decode()).
    """
    if algorithm is not EncryptionAlgorithm.AES_GCM:
        raise ValueError("Only AES_GCM is supported")

    _require_crypto()
    raw = _b64url_decode(token)
    if len(raw) < 13:
        raise DecryptionError("Ciphertext too short")

    nonce, ct = raw[:12], raw[12:]
    k = _hkdf_derive_key(key, length=32)
    aesgcm = AESGCM(k)
    try:
        return aesgcm.decrypt(nonce, ct, associated_data=None)
    except Exception as e:
        raise DecryptionError("Decryption failed") from e


# ---------------------------------------------------------------------------
# Rate Limiter
# ---------------------------------------------------------------------------

class RateLimiter:
    """
    Simple in-memory sliding-window rate limiter.

    Example:
        rl = RateLimiter(max_calls=5, per_seconds=60)
        rl.check("login:ip:127.0.0.1")  # raises RateLimitError if exceeded
    """
    def __init__(self, max_calls: int, per_seconds: int):
        if max_calls <= 0 or per_seconds <= 0:
            raise ValueError("max_calls and per_seconds must be positive")
        self.max_calls = int(max_calls)
        self.per_seconds = int(per_seconds)
        self._hits: dict[str, _t.List[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.time()
        window_start = now - self.per_seconds
        with self._lock:
            hits = self._hits.setdefault(key, [])
            # drop old
            i = 0
            while i < len(hits) and hits[i] <= window_start:
                i += 1
            if i:
                del hits[:i]
            if len(hits) >= self.max_calls:
                raise RateLimitError(f"Rate limit exceeded for key={key!r}")
            hits.append(now)

    def remaining(self, key: str) -> int:
        with self._lock:
            hits = self._hits.get(key, [])
            return max(0, self.max_calls - len(hits))

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


# ---------------------------------------------------------------------------
# Session Management
# ---------------------------------------------------------------------------

@dataclass
class Session:
    id: str
    user_id: _t.Union[int, str]
    issued_at: int
    expires_at: int
    csrf: str
    data: dict


class SecureSessionManager:
    """
    Minimal, in-memory HMAC-signed session manager.
    Session IDs are opaque strings that include an HMAC signature to prevent
    forgery. Session contents are stored server-side in-memory.
    """
    def __init__(self, secret: _t.Union[str, bytes], ttl_seconds: int = 3600):
        self._secret = secret
        self._ttl = int(ttl_seconds)
        self._store: dict[str, Session] = {}
        self._lock = threading.Lock()

    def _sign_id(self, raw: bytes) -> str:
        sig = _sign(raw, self._secret)
        return _b64url_encode(raw + b"." + sig.encode("ascii"))

    def _verify_id(self, sid: str) -> bytes:
        try:
            raw = _b64url_decode(sid)
            raw_id, sig = raw.split(b".", 1)
        except Exception:
            raise AuthenticationError("Malformed session id")
        if not _verify_signature(raw_id, sig.decode("ascii"), self._secret):
            raise AuthenticationError("Invalid session id signature")
        return raw_id

    def create(self, user_id: _t.Union[int, str], *, data: _t.Optional[dict] = None) -> Session:
        now = int(time.time())
        expires = now + self._ttl
        raw_id = os.urandom(16)
        sid = self._sign_id(raw_id)
        csrf = _b64url_encode(os.urandom(16))
        sess = Session(id=sid, user_id=user_id, issued_at=now, expires_at=expires, csrf=csrf, data=dict(data or {}))
        with self._lock:
            self._store[sid] = sess
        return sess

    def get(self, sid: str) -> Session:
        with self._lock:
            sess = self._store.get(sid)
        if not sess:
            raise AuthenticationError("Unknown session")
        if int(time.time()) > sess.expires_at:
            self.revoke(sid)
            raise TokenExpiredError("Session expired")
        return sess

    def refresh(self, sid: str) -> Session:
        with self._lock:
            sess = self._store.get(sid)
            if not sess:
                raise AuthenticationError("Unknown session")
            sess.expires_at = int(time.time()) + self._ttl
            self._store[sid] = sess
            return sess

    def revoke(self, sid: str) -> None:
        with self._lock:
            self._store.pop(sid, None)


# ---------------------------------------------------------------------------
# Audit Logging
# ---------------------------------------------------------------------------

class SecurityAuditLogger:
    """
    Lightweight in-memory audit logger with optional propagation to std logging.
    """
    def __init__(self, logger: _t.Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger("omnicore_engine.security")
        self._events: _t.List[dict] = []
        self._lock = threading.Lock()

    def log(self, action: str, subject: str, *, metadata: _t.Optional[dict] = None, level: int = logging.INFO) -> None:
        evt = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": str(action),
            "subject": str(subject),
            "metadata": dict(metadata or {}),
        }
        with self._lock:
            self._events.append(evt)
        try:
            self.logger.log(level, "SECURITY %s", json.dumps(evt, sort_keys=True))
        except Exception:  # pragma: no cover
            pass

    def tail(self, n: int = 20) -> _t.List[dict]:
        with self._lock:
            return self._events[-int(n):]


# ---------------------------------------------------------------------------
# EnterpriseSecurityUtils (facade)
# ---------------------------------------------------------------------------

class EnterpriseSecurityUtils:
    """
    High-level facade aggregating security helpers used throughout the engine.
    """

    def __init__(self, *, secret: _t.Optional[_t.Union[str, bytes]] = None, session_ttl_seconds: int = 3600):
        self._secret = secret or os.environ.get("OMNICORE_SECRET", "omnicore-default-secret")
        self.audit = SecurityAuditLogger()
        self.sessions = SecureSessionManager(self._secret, ttl_seconds=session_ttl_seconds)

    # ----- HTML -----
    def sanitize_html(self, text: str) -> str:
        return sanitize_html(text)

    # ----- Passwords -----
    def hash_password(self, password: str, iterations: int = 390_000) -> str:
        return hash_password(password, iterations=iterations)

    def verify_password(self, password: str, stored: str) -> bool:
        return verify_password(password, stored)

    # ----- Tokens -----
    def generate_token(self, payload: dict, ttl_seconds: int = 3600, include_nonce: bool = True) -> str:
        return generate_token(payload, secret=self._secret, ttl_seconds=ttl_seconds, include_nonce=include_nonce)

    def verify_token(self, token: str, *, leeway: int = 0) -> dict:
        return verify_token(token, secret=self._secret, leeway=leeway)

    # ----- Encryption -----
    def encrypt(self, plaintext: _t.Union[str, bytes]) -> str:
        return encrypt(plaintext, key=self._secret)

    def decrypt(self, token: str) -> bytes:
        return decrypt(token, key=self._secret)

    # ----- Sessions -----
    def create_session(self, user_id: _t.Union[int, str], *, data: _t.Optional[dict] = None) -> Session:
        sess = self.sessions.create(user_id, data=data)
        self.audit.log("session_create", str(user_id), metadata={"sid": sess.id})
        return sess

    def get_session(self, sid: str) -> Session:
        return self.sessions.get(sid)

    def refresh_session(self, sid: str) -> Session:
        sess = self.sessions.refresh(sid)
        self.audit.log("session_refresh", str(sess.user_id), metadata={"sid": sess.id})
        return sess

    def revoke_session(self, sid: str) -> None:
        try:
            sess = self.sessions.get(sid)
            user_id = sess.user_id
        except Exception:
            user_id = "unknown"
        self.sessions.revoke(sid)
        self.audit.log("session_revoke", str(user_id), metadata={"sid": sid})

    # ----- TOTP (optional) -----
    def generate_totp(self, secret: str, *, interval: int = 30) -> str:
        if pyotp is None:
            raise RuntimeError("pyotp is not installed")
        totp = pyotp.TOTP(secret, interval=interval)
        return totp.now()

    def verify_totp(self, secret: str, code: str, *, interval: int = 30, valid_window: int = 1) -> bool:
        if pyotp is None:
            return False
        totp = pyotp.TOTP(secret, interval=interval)
        try:
            return bool(totp.verify(code, valid_window=valid_window))
        except Exception:
            return False


# Singleton pattern
_LOCK = threading.Lock()
_SINGLETON: _t.Optional[EnterpriseSecurityUtils] = None

def get_security_utils() -> EnterpriseSecurityUtils:
    global _SINGLETON
    if _SINGLETON is None:
        with _LOCK:
            if _SINGLETON is None:
                _SINGLETON = EnterpriseSecurityUtils()
    return _SINGLETON


# ---------------------------------------------------------------------------
# Decorators: authentication & authorization
# ---------------------------------------------------------------------------

def _extract_user_from_args_kwargs(args: tuple, kwargs: dict) -> _t.Any:
    """
    Try common ways tests might pass a "user" object:
      * positional arg named 'user' in signature (not available here, so we check kwargs)
      * kwargs['user']
      * kwargs['request'].user
    """
    user = kwargs.get("user")
    if user is not None:
        return user
    req = kwargs.get("request")
    if req is not None:
        return getattr(req, "user", None)
    return None


def require_authentication(func: _t.Callable) -> _t.Callable:
    """
    Decorator ensuring a user is present and authenticated.
    Expects a 'user' kwarg or request.user to have truthy 'is_authenticated'.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        user = _extract_user_from_args_kwargs(args, kwargs)
        if user is None:
            raise AuthenticationError("Authentication required")
        is_auth = getattr(user, "is_authenticated", None)
        if callable(is_auth):
            is_auth = is_auth()
        if not is_auth:
            raise AuthenticationError("User is not authenticated")
        return func(*args, **kwargs)
    return wrapper


def require_authorization(*required_roles: str) -> _t.Callable:
    """
    Decorator ensuring the authenticated user also has the required roles.
    If no roles are provided, behaves the same as require_authentication.
    """
    def decorator(func: _t.Callable) -> _t.Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            user = _extract_user_from_args_kwargs(args, kwargs)
            if user is None:
                raise AuthenticationError("Authentication required")
            # Determine roles on the user
            roles = getattr(user, "roles", None)
            if roles is None and hasattr(user, "get"):
                # Might be a dict-like
                roles = user.get("roles", None)
            if callable(roles):
                roles = roles()
            roles_set = set(roles or [])
            for role in required_roles:
                if role not in roles_set:
                    raise AuthorizationError(f"Missing required role: {role}")
            return func(*args, **kwargs)
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Convenience proxy functions (module-level)
# ---------------------------------------------------------------------------

def sanitize_html_proxy(text: str) -> str:  # internal alias to keep names tidy
    return get_security_utils().sanitize_html(text)

def hash_password_proxy(password: str, iterations: int = 390_000) -> str:
    return get_security_utils().hash_password(password, iterations)

def verify_password_proxy(password: str, stored: str) -> bool:
    return get_security_utils().verify_password(password, stored)

def generate_token_proxy(payload: dict, ttl_seconds: int = 3600, include_nonce: bool = True) -> str:
    return get_security_utils().generate_token(payload, ttl_seconds=ttl_seconds, include_nonce=include_nonce)

def verify_token_proxy(token: str, leeway: int = 0) -> dict:
    return get_security_utils().verify_token(token, leeway=leeway)

def encrypt_proxy(plaintext: _t.Union[str, bytes]) -> str:
    return get_security_utils().encrypt(plaintext)

def decrypt_proxy(token: str) -> bytes:
    return get_security_utils().decrypt(token)

def create_session_proxy(user_id: _t.Union[int, str], *, data: _t.Optional[dict] = None) -> Session:
    return get_security_utils().create_session(user_id, data=data)

def get_session_proxy(sid: str) -> Session:
    return get_security_utils().get_session(sid)

def revoke_session_proxy(sid: str) -> None:
    return get_security_utils().revoke_session(sid)

def refresh_session_proxy(sid: str) -> Session:
    return get_security_utils().refresh_session(sid)


# Public names (proxy to singleton)
sanitize_html = sanitize_html_proxy
hash_password = hash_password_proxy
verify_password = verify_password_proxy
generate_token = generate_token_proxy
verify_token = verify_token_proxy
encrypt = encrypt_proxy
decrypt = decrypt_proxy
create_session = create_session_proxy
get_session = get_session_proxy
revoke_session = revoke_session_proxy
refresh_session = refresh_session_proxy
