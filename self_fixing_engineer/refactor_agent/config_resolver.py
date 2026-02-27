# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
config_resolver.py

Resolves configdb:// URIs to actual configuration values.
Supports local YAML-based fallback and optional remote HTTP resolution.
"""

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_DEFAULTS_PATH = Path(__file__).parent / "configdb_defaults.yaml"

_URI_PATTERN = re.compile(r"^configdb://(?P<category>[^/]+)/(?P<key>[^/]+)$")


class ConfigDBResolver:
    """Resolves configdb:// URIs to actual configuration values."""

    def __init__(
        self,
        defaults_path: Optional[str] = None,
        remote_endpoint: Optional[str] = None,
    ) -> None:
        """
        Initialise the resolver.

        Args:
            defaults_path: Path to the YAML file with default values.
                           Defaults to configdb_defaults.yaml in the same directory.
            remote_endpoint: Optional HTTP endpoint for remote config resolution.
                             If not set, only local YAML is used.
        """
        self._defaults_path = Path(defaults_path) if defaults_path else _DEFAULTS_PATH
        self._remote_endpoint = remote_endpoint or os.environ.get(
            "CONFIGDB_REMOTE_ENDPOINT"
        )
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
        """Attempt to resolve from a remote HTTP endpoint."""
        try:
            import urllib.request
            import json as _json

            url = f"{self._remote_endpoint.rstrip('/')}/{category}/{key}"
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
