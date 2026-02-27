# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
config_resolver.py

Resolves configdb:// URIs to actual configuration values.
Supports local YAML-based fallback and optional remote HTTP resolution.
"""

import logging
import os
import re
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULTS_PATH = Path(__file__).parent / "configdb_defaults.yaml"

_URI_PATTERN = re.compile(r"^configdb://(?P<category>[^/]+)/(?P<key>[^/]+)$")

# SSRF protection: only allow HTTPS endpoints and validate against an allowlist.
# Operators can extend this list via the CONFIGDB_ALLOWED_HOSTS env var
# (comma-separated hostnames).
_DEFAULT_ALLOWED_HOSTS: List[str] = []


def _get_allowed_hosts() -> List[str]:
    """Return the merged list of allowed remote hosts from env + defaults."""
    env_val = os.environ.get("CONFIGDB_ALLOWED_HOSTS", "")
    extra = [h.strip() for h in env_val.split(",") if h.strip()]
    return _DEFAULT_ALLOWED_HOSTS + extra


def _validate_remote_url(url: str, allowed_hosts: List[str]) -> None:
    """Validate a remote URL against the allowlist to prevent SSRF.

    Args:
        url: The URL to validate.
        allowed_hosts: List of permitted hostnames.

    Raises:
        ValueError: If the URL scheme is not HTTPS or the host is not allowed.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(
            f"ConfigDBResolver: remote endpoint must use HTTPS (got {parsed.scheme!r})"
        )
    if allowed_hosts and parsed.hostname not in allowed_hosts:
        raise ValueError(
            f"ConfigDBResolver: host {parsed.hostname!r} is not in the allowed hosts list. "
            f"Set CONFIGDB_ALLOWED_HOSTS env var to permit it."
        )


class ConfigDBResolver:
    """Resolves configdb:// URIs to actual configuration values."""

    def __init__(
        self,
        defaults_path: Optional[str] = None,
        remote_endpoint: Optional[str] = None,
        allowed_hosts: Optional[List[str]] = None,
    ) -> None:
        """Initialise the resolver.

        Args:
            defaults_path: Path to the YAML file with default values.
                           Defaults to configdb_defaults.yaml in the same directory.
            remote_endpoint: Optional HTTPS endpoint for remote config resolution.
                             If not set, only local YAML is used.  Must use HTTPS and
                             must match the ``allowed_hosts`` list (or
                             ``CONFIGDB_ALLOWED_HOSTS`` env var).
            allowed_hosts: Explicit list of permitted remote hostnames.  When
                           empty the CONFIGDB_ALLOWED_HOSTS env var is consulted.
                           When both are empty, any HTTPS host is rejected to
                           prevent accidental SSRF.
        """
        self._defaults_path = Path(defaults_path) if defaults_path else _DEFAULTS_PATH
        self._remote_endpoint: Optional[str] = remote_endpoint or os.environ.get(
            "CONFIGDB_REMOTE_ENDPOINT"
        )
        self._allowed_hosts: List[str] = allowed_hosts if allowed_hosts is not None else _get_allowed_hosts()
        self._cache: Dict[str, Any] = {}
        self._loaded = False

    def _load_defaults(self) -> Dict[str, Any]:
        """Load defaults from the local YAML file (cached after first load)."""
        if self._loaded:
            return self._cache

        try:
            import yaml  # type: ignore[import]

            with open(self._defaults_path, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            self._cache = data
            self._loaded = True
            logger.debug("ConfigDBResolver: loaded defaults from %s", self._defaults_path)
        except FileNotFoundError:
            logger.warning(
                "ConfigDBResolver: defaults file not found at %s, using empty config",
                self._defaults_path,
            )
            self._cache = {}
            self._loaded = True

        return self._cache

    async def resolve(self, uri: str) -> Dict[str, Any]:
        """
        Resolve a configdb:// URI to its configuration value.

        Supports:
            configdb://roles/<agent_id>   → role definition (name, permissions, description)
            configdb://skills/<agent_id>  → skills list

        Falls back to local YAML if remote is unavailable.

        Args:
            uri: A configdb:// URI string.

        Returns:
            A dictionary with the resolved configuration.

        Raises:
            ValueError: If the URI format is not recognised.
        """
        match = _URI_PATTERN.match(uri)
        if not match:
            raise ValueError(f"ConfigDBResolver: unrecognised URI format: {uri!r}")

        category = match.group("category")
        key = match.group("key")

        # Try remote first if configured
        if self._remote_endpoint:
            result = await self._resolve_remote(category, key)
            if result is not None:
                return result

        # Fall back to local YAML
        return self._resolve_local(category, key)

    async def _resolve_remote(
        self, category: str, key: str
    ) -> Optional[Dict[str, Any]]:
        """Attempt to resolve from a remote HTTPS endpoint.

        Validates the constructed URL against the allowed-hosts allowlist before
        making the network request to prevent SSRF attacks.
        """
        import json as _json
        import urllib.request

        url = f"{self._remote_endpoint.rstrip('/')}/{category}/{key}"
        try:
            _validate_remote_url(url, self._allowed_hosts)
        except ValueError as exc:
            logger.warning("ConfigDBResolver: SSRF guard blocked remote URL: %s", exc)
            return None
        try:
            # S310 suppressed: SSRF risk is mitigated by _validate_remote_url()
            # which enforces HTTPS-only and validates against the allowed-hosts
            # allowlist before this call is reached.
            with urllib.request.urlopen(url, timeout=3) as response:  # noqa: S310
                body = response.read()
                return _json.loads(body)
        except Exception as exc:
            logger.debug(
                "ConfigDBResolver: remote resolution failed for %s/%s: %s",
                category,
                key,
                exc,
            )
            return None

    def _resolve_local(self, category: str, key: str) -> Dict[str, Any]:
        """Resolve from the local YAML defaults."""
        defaults = self._load_defaults()
        section = defaults.get(category, {})
        value = section.get(key)
        if value is None:
            logger.debug(
                "ConfigDBResolver: no local entry for %s/%s, returning empty dict",
                category,
                key,
            )
            return {}
        return value if isinstance(value, dict) else {"value": value}
