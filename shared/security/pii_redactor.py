# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Unified PII redaction for the Code Factory platform.

Problem
-------
Four independent ``redact_pii`` implementations existed in the codebase:

- ``generator/agents/generator_plugin_wrapper.py``              — EMAIL, PHONE, SSN only
- ``generator/runner/runner_security_utils.py``                 — Presidio + custom patterns
- ``self_fixing_engineer/arbiter/bug_manager/utils.py``         — comprehensive regex + keywords
- ``self_fixing_engineer/arbiter/explainable_reasoner/utils.py`` — basic EMAIL, PHONE

Each version had a different set of recognised patterns, a different
recursion depth limit, and different handling of dict values, creating
security gaps when the wrong version was imported.

Solution
--------
This module provides a single, production-quality implementation with:

* **Pre-compiled pattern registry** — patterns are compiled once at
  import time as :class:`collections.namedtuple` entries; runtime cost is
  linear in string length, not pattern count.
* **Thread-safe pattern registry** — a ``threading.Lock`` allows safe
  extension of patterns at runtime without restarting the process.
* **Iterative BFS dict traversal** — no recursion depth limit; deeply
  nested structures are safe.
* **Prometheus counter** — emitted via :func:`shared.noop_metrics.safe_metric`
  with graceful degradation when ``prometheus_client`` is absent.
* **Settings protocol** — callers may pass any object exposing
  ``PII_SENSITIVE_KEYWORDS``, ``PII_CUSTOM_REGEXES``, ``PII_MASK_LEVEL``.

Architecture
------------
::

    redact_pii(data, settings=None, mask_level="full")
         │
         ├── str  ──► _redact_string(value, patterns, mask_level)
         │                  │
         │                  ├── pattern.search(value)
         │                  │       │ match → sub with replacement_tag
         │                  │       │         (or partial mask)
         │                  │       └ no match → unchanged
         │                  └── return redacted string
         │
         ├── dict ──► BFS queue (deque)
         │                  │
         │                  ├── key in sensitive_keywords? → "[REDACTED]"
         │                  ├── value is str?              → _redact_string
         │                  ├── value is dict|list?        → enqueue child
         │                  └── other                      → copy as-is
         │
         └── other ──► return unchanged

Usage
-----
::

    from shared.security.pii_redactor import redact_pii

    clean = redact_pii("Contact us at test@example.com")
    # → "Contact us at [REDACTED_EMAIL]"

    clean_dict = redact_pii({"email": "user@example.com", "count": 42})
    # → {"email": "[REDACTED]", "count": 42}

Industry Standards Applied
--------------------------
* **GDPR / CCPA** — covers email, phone, SSN, IP address, JWT, UUID,
  inline key=value secrets.
* **OWASP Logging Cheat Sheet** — sensitive fields are masked before
  any log write.
* **PEP 484** — full type annotations on all public symbols.
* **PEP 517 / 518** — zero mandatory runtime dependencies.
"""

from __future__ import annotations

import logging
import re
import threading
from collections import deque, namedtuple
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy Prometheus imports
# ---------------------------------------------------------------------------

try:
    from prometheus_client import Counter as _Counter  # type: ignore[import]
except ImportError:
    _Counter = None  # type: ignore[assignment]

from shared.noop_metrics import safe_metric

_REDACTION_EVENTS = safe_metric(
    _Counter,
    "pii_redaction_events_total",
    "Total PII redaction events performed",
    labelnames=["pattern"],
)

# ---------------------------------------------------------------------------
# Pattern registry
# ---------------------------------------------------------------------------

_PiiPattern = namedtuple("_PiiPattern", ["pattern", "replacement_tag"])

_pattern_lock: threading.Lock = threading.Lock()

_PATTERNS: Dict[str, _PiiPattern] = {
    "EMAIL": _PiiPattern(
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        "[REDACTED_EMAIL]",
    ),
    "PHONE": _PiiPattern(
        re.compile(
            r"\b(?:\+?(\d{1,3}))?[-. (]*(\d{3})[-. )]*(\d{3})[-. ]*(\d{4})\b"
        ),
        "[REDACTED_PHONE]",
    ),
    "SSN": _PiiPattern(
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "[REDACTED_SSN]",
    ),
    "IP": _PiiPattern(
        re.compile(
            r"\b(?:\d{1,3}\.){3}\d{1,3}\b"
            r"|\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b"
        ),
        "[REDACTED_IP]",
    ),
    "SECRET": _PiiPattern(
        re.compile(r"(?i)(?:token|key|secret|password)\s*[:=]\s*[\w.\-]+"),
        "[REDACTED_SECRET]",
    ),
    "UUID": _PiiPattern(
        re.compile(
            r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
        ),
        "[REDACTED_UUID]",
    ),
    "JWT": _PiiPattern(
        re.compile(
            r"eyJ[A-Za-z0-9\-_=]+\.[A-Za-z0-9\-_=]+\.?[A-Za-z0-9\-_.+/=]*"
        ),
        "[REDACTED_JWT]",
    ),
}

# ---------------------------------------------------------------------------
# Default sensitive keywords
# ---------------------------------------------------------------------------

_DEFAULT_SENSITIVE_KEYWORDS: Set[str] = {
    "token", "key", "password", "secret", "api_key", "webhook_url",
    "routing_key", "address", "phone", "email", "ssn", "credit_card",
    "account_number", "dob", "username", "ip_address", "geolocation",
    "auth_header", "bearer",
}


# ---------------------------------------------------------------------------
# Settings protocol (documents expected attributes; not enforced at runtime)
# ---------------------------------------------------------------------------


class SettingsProtocol:
    """Protocol documenting the settings attributes consumed by :func:`redact_pii`.

    Callers may pass any object that exposes a subset of these attributes;
    missing attributes fall back to the module defaults.

    Attributes
    ----------
    PII_SENSITIVE_KEYWORDS : set[str]
        Dict keys whose values should be wholesale replaced with
        ``"[REDACTED]"`` regardless of their content.
    PII_CUSTOM_REGEXES : list[str]
        Additional regex pattern strings compiled at call time and added to
        the active pattern set.
    PII_MASK_LEVEL : str
        ``"full"`` (default) — entire match is replaced with the tag.
        ``"partial"`` — first and last four characters are kept.
    """

    PII_SENSITIVE_KEYWORDS: Set[str] = _DEFAULT_SENSITIVE_KEYWORDS
    PII_CUSTOM_REGEXES: List[str] = []
    PII_MASK_LEVEL: str = "full"

    def __repr__(self) -> str:  # pragma: no cover
        return "SettingsProtocol()"


# ---------------------------------------------------------------------------
# Public API: extend the pattern registry at runtime
# ---------------------------------------------------------------------------


def register_pattern(name: str, regex: str, replacement_tag: str) -> None:
    """Add a custom PII pattern to the global registry.

    Thread-safe; safe to call at any time (even concurrently with
    :func:`redact_pii`).

    Parameters
    ----------
    name : str
        Unique pattern identifier (upper-case by convention).
    regex : str
        Regular expression source string.
    replacement_tag : str
        Text substituted for every match (e.g. ``"[REDACTED_CUSTOM]"``).

    Examples
    --------
    ::

        register_pattern("POSTCODE", r"\\b[A-Z]{1,2}\\d{1,2}[A-Z]?\\s?\\d[A-Z]{2}\\b",
                         "[REDACTED_POSTCODE]")
    """
    with _pattern_lock:
        _PATTERNS[name] = _PiiPattern(re.compile(regex), replacement_tag)
    logger.debug("pii_redactor: registered custom pattern %r", name)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _redact_string(
    value: str,
    patterns: Dict[str, _PiiPattern],
    mask_level: str,
) -> str:
    """Apply all PII patterns to *value* and return the redacted string."""
    for name, pii in patterns.items():
        if pii.pattern.search(value):
            if mask_level == "partial":
                def _partial(m: re.Match) -> str:  # type: ignore[type-arg]
                    s = m.group(0)
                    return s[:4] + "[...REDACTED...]" + s[-4:] if len(s) > 8 else "[REDACTED]"
                value = pii.pattern.sub(_partial, value)
            else:
                value = pii.pattern.sub(pii.replacement_tag, value)
            try:
                _REDACTION_EVENTS.labels(pattern=name).inc()
            except Exception:  # pragma: no cover
                pass
    return value


# ---------------------------------------------------------------------------
# Public redact_pii function
# ---------------------------------------------------------------------------


def redact_pii(
    data: Any,
    settings: Optional[Any] = None,
    mask_level: str = "full",
) -> Any:
    """Redact PII from *data*.

    Parameters
    ----------
    data : Any
        A ``str``, ``dict``, or any other type.

        * **str** — patterns are applied and the redacted string is returned.
        * **dict** — BFS iterative traversal: keys matching
          ``sensitive_keywords`` are replaced with ``"[REDACTED]"``; string
          values are passed through pattern matching; nested dicts and lists
          are traversed without recursion.
        * **other** — returned unchanged.
    settings : Any | None
        Optional settings object exposing ``PII_SENSITIVE_KEYWORDS``,
        ``PII_CUSTOM_REGEXES``, and/or ``PII_MASK_LEVEL``.
    mask_level : str
        ``"full"`` (default) — entire match replaced with the tag.
        ``"partial"`` — first/last four characters kept.
        Overridden by ``settings.PII_MASK_LEVEL`` when present.

    Returns
    -------
    Any
        The redacted value (same type as *data* for ``str`` and ``dict``).

    Examples
    --------
    ::

        from shared.security.pii_redactor import redact_pii

        assert "REDACTED" in redact_pii("test@example.com")
        assert redact_pii({"password": "s3cr3t"}) == {"password": "[REDACTED]"}
        assert redact_pii(42) == 42
    """
    sensitive_keywords: Set[str] = getattr(
        settings, "PII_SENSITIVE_KEYWORDS", _DEFAULT_SENSITIVE_KEYWORDS
    )
    custom_regex_strings: List[str] = getattr(settings, "PII_CUSTOM_REGEXES", [])
    effective_mask: str = getattr(settings, "PII_MASK_LEVEL", mask_level)

    # Build active pattern dict (thread-safe snapshot + caller customs)
    with _pattern_lock:
        active_patterns: Dict[str, _PiiPattern] = dict(_PATTERNS)
    for i, pat_str in enumerate(custom_regex_strings):
        active_patterns[f"CUSTOM_{i}"] = _PiiPattern(
            re.compile(pat_str), "[REDACTED_CUSTOM]"
        )

    # ── String ───────────────────────────────────────────────────────────────
    if isinstance(data, str):
        return _redact_string(data, active_patterns, effective_mask)

    # ── Non-dict / non-string ─────────────────────────────────────────────────
    if not isinstance(data, dict):
        return data

    # ── Dict: iterative BFS ───────────────────────────────────────────────────
    redacted_root: Dict[str, Any] = {}
    queue: deque = deque([(data, redacted_root)])

    while queue:
        original_obj, redacted_parent = queue.popleft()

        if isinstance(original_obj, dict):
            for k, v in original_obj.items():
                if any(s in k.lower() for s in sensitive_keywords):
                    redacted_parent[k] = "[REDACTED]"
                elif isinstance(v, dict):
                    redacted_parent[k] = {}
                    queue.append((v, redacted_parent[k]))
                elif isinstance(v, list):
                    redacted_parent[k] = []
                    queue.append((v, redacted_parent[k]))
                elif isinstance(v, str):
                    redacted_parent[k] = _redact_string(
                        v, active_patterns, effective_mask
                    )
                else:
                    redacted_parent[k] = v
        elif isinstance(original_obj, list):
            for item in original_obj:
                if isinstance(item, dict):
                    new_dict: Dict[str, Any] = {}
                    redacted_parent.append(new_dict)
                    queue.append((item, new_dict))
                elif isinstance(item, list):
                    new_list: List[Any] = []
                    redacted_parent.append(new_list)
                    queue.append((item, new_list))
                elif isinstance(item, str):
                    redacted_parent.append(
                        _redact_string(item, active_patterns, effective_mask)
                    )
                else:
                    redacted_parent.append(item)

    return redacted_root


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "SettingsProtocol",
    "register_pattern",
    "redact_pii",
]
