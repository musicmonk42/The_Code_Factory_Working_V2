import os
import re
import logging
import threading
import json as _json
from typing import Any, Callable, Optional, Dict, Set, List
from pathlib import Path

# NOTE: 'dotenv' is an optional dependency, imported only when used.

__all__ = ["SecretsManager", "SECRETS_MANAGER", "cast_bool_strict"]

# Valid env var names: UPPERCASE letters, digits, underscore; must start with a letter
_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _cast_to_bool(value: str) -> bool:
    """
    Casts a string to a boolean, ignoring leading/trailing whitespace.
    Recognized 'True' values (case-insensitive): 'true', '1', 't', 'y', 'yes'.
    All other values are considered False.
    """
    return str(value).strip().lower() in ('true', '1', 't', 'y', 'yes')


def cast_bool_strict(value: str) -> bool:
    """
    Casts a string to a boolean with strict validation.

    Raises:
        TypeError: If the value is not a recognized boolean representation.
    """
    v = str(value).strip().lower()
    true_set = {'true', '1', 't', 'y', 'yes', 'on'}
    false_set = {'false', '0', 'f', 'n', 'no', 'off'}
    if v in true_set:
        return True
    if v in false_set:
        return False
    raise TypeError(f"Invalid boolean: {value!r}")


class SecretsManager:
    """
    A thread-safe secrets manager for loading and accessing environment variables.

    This class implements a thread-safe singleton pattern to ensure a single,
    application-wide source for secrets. It supports optional loading of variables
    from a `.env` file, caching values for performance, and providing robust error
    handling with type casting. By default, it treats blank secrets as missing
    to prevent common production errors with empty credentials or endpoints.
    """
    _instance = None
    _class_lock = threading.Lock()  # Lock for singleton creation only

    def __new__(cls, *args, **kwargs):
        """Implements a thread-safe singleton pattern."""
        with cls._class_lock:
            if cls._instance is None:
                cls._instance = super(SecretsManager, cls).__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(self, env_file: str = ".env", logger: Optional[logging.Logger] = None, allow_dotenv: Optional[bool] = None):
        """
        Initializes the SecretsManager. This is called only once per instance.
        """
        if getattr(self, "_initialized", False):
            return
        with type(self)._class_lock:
            if getattr(self, "_initialized", False):
                return

            self._lock = threading.RLock()
            self._cache: Dict[str, Optional[str]] = {}
            self._env_file = Path(env_file)

            # Library-safe logger
            self._logger = logger or logging.getLogger(__name__)
            if not self._logger.handlers:
                self._logger.addHandler(logging.NullHandler())

            env = os.getenv("ENVIRONMENT", "").strip().lower()
            allow = allow_dotenv if allow_dotenv is not None else (not env.startswith("prod"))
            if env.startswith("prod"):
                override = os.getenv("SECRETS_ALLOW_DOTENV_IN_PROD", "")
                if override.strip().lower() in {"1", "true", "yes", "on"}:
                    allow = True
                    self._logger.warning(
                        "SECRETS_ALLOW_DOTENV_IN_PROD override detected; enabling .env loading in production."
                    )

            # Stricter name validation by default in prod
            strict_names_default = env.startswith("prod")
            self._strict_names = os.getenv("SECRETS_STRICT_NAMES", "1" if strict_names_default else "0").strip().lower() in {
                "1", "true", "yes", "on"
            }

            self._allow_dotenv = bool(allow)
            if self._allow_dotenv:
                self._load_env()
            else:
                self._logger.info("Skipping .env file load: disabled for this environment.")

            self._initialized = True

    def _load_env(self) -> None:
        if not self._env_file.exists():
            self._logger.debug("No .env file found at %s", self._env_file)
            return
        try:
            from dotenv import load_dotenv
        except ImportError:
            self._logger.warning(
                "python-dotenv not installed; cannot load %s. To install, run 'pip install python-dotenv'.",
                self._env_file
            )
            return

        self._logger.info("Loading environment variables from %s", self._env_file)
        try:
            load_dotenv(dotenv_path=self._env_file, override=False)
        except Exception as e:
            self._logger.error("Failed to load .env file %s: %s", self._env_file, e)
            raise RuntimeError(f"Failed to load .env file {self._env_file}: {e}") from e

    def _validate_name(self, name: str, *, strict: Optional[bool] = None) -> None:
        if not name or not isinstance(name, str) or "=" in name:
            raise ValueError("Secret name must be a non-empty string and cannot contain '='")
        if any(ch.isspace() for ch in name):
            raise ValueError("Secret name cannot contain whitespace")

        use_strict = self._strict_names if strict is None else strict
        if use_strict and not _ENV_NAME_RE.match(name):
            raise ValueError(f"Invalid secret name format: {name!r}. Must match {_ENV_NAME_RE.pattern}")

    def get_secret(
        self,
        name: str,
        required: bool = False,
        default: Any = None,
        type_cast: Optional[Callable[[str], Any]] = None,
        blank_ok: bool = False,
        strict_name: Optional[bool] = None,
    ) -> Any:
        self._validate_name(name, strict=strict_name)
        if type_cast is bool:
            type_cast = _cast_to_bool

        with self._lock:
            if name in self._cache:
                value = self._cache[name]
                self._logger.debug("Retrieved secret '%s' from cache", name)
            else:
                value = os.environ.get(name)
                self._cache[name] = value
                self._logger.debug("Retrieved secret '%s' from environment", name)

        is_missing = (value is None) or (not blank_ok and str(value).strip() == "")
        if is_missing:
            if required:
                self._logger.error("Required secret '%s' is missing or blank", name)
                raise RuntimeError(f"Required secret '{name}' is missing or blank.")
            return default

        if type_cast:
            try:
                val_str = str(value).strip() if type_cast in (int, float, _cast_to_bool, cast_bool_strict) else str(value)
                return type_cast(val_str)
            except (ValueError, TypeError) as e:
                type_name = getattr(type_cast, '__name__', 'custom type')
                msg = f"Failed to cast secret '{name}' to type '{type_name}': {e}"
                self._logger.error(msg)
                raise TypeError(msg) from e

        return value

    def get_required(self, name: str, **kwargs) -> Any:
        """Convenience wrapper for get_secret(required=True)."""
        return self.get_secret(name, required=True, **kwargs)

    def get_with_fallback(self, names: List[str], **kwargs) -> Any:
        """
        Tries a list of secret names in order and returns the first one found.
        All other arguments are passed to the underlying `get_secret` call for each name.
        If no names are found, returns the `default` from kwargs, or None.
        """
        if not names:
            return kwargs.get("default")
        internal_kwargs = kwargs.copy()
        internal_kwargs["required"] = False

        for i, name in enumerate(names):
            is_last = (i == len(names) - 1)
            val = self.get_secret(name, **(kwargs if is_last else internal_kwargs))
            if val is not None:
                return val
        return kwargs.get("default")

    def reload(self) -> None:
        with self._lock:
            old_cache = dict(self._cache)
            try:
                self._cache.clear()
                if self._allow_dotenv:
                    self._load_env()
                self._logger.info("Secrets cache cleared and environment reloaded")
            except Exception as e:
                self._cache = old_cache
                self._logger.error("Reload failed; restored previous cache: %s", e)
                raise

    def set_secret(self, name: str, value: Any) -> str:
        if value is None:
            raise ValueError("Cannot set None for secret; pass a string or use a dedicated method to remove the key.")
        self._validate_name(name)
        with self._lock:
            s_value = str(value)
            os.environ[name] = s_value
            self._cache[name] = s_value
            self._logger.debug("Set secret '%s' in environment and cache", name)
        return s_value

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()
            self._logger.debug("Secrets cache cleared")

    def clear_cache_key(self, name: str) -> None:
        self._validate_name(name)
        with self._lock:
            if self._cache.pop(name, None) is not None:
                self._logger.debug("Cleared cache for secret '%s'", name)

    # --- Typed Getters & Helpers ---

    def get_choice(
        self,
        name: str,
        choices: Set[str],
        *,
        casefold: bool = True,
        return_normalized: bool = False,
        **kwargs
    ) -> Optional[str]:
        """
        Retrieves a secret and validates it against a set of allowed choices.
        """
        val = self.get_secret(name, **kwargs)
        if val is None:
            return None
        s_val = str(val)
        norm_val = s_val.casefold() if casefold else s_val
        valid_map = {(c.casefold() if casefold else c): c for c in choices}
        if norm_val not in valid_map:
            raise TypeError(f"Secret '{name}' must be one of {sorted(choices)}; got {s_val!r}")
        return valid_map[norm_val] if return_normalized else s_val

    def get_json(self, name: str, **kwargs) -> Any:
        raw = self.get_secret(name, blank_ok=False, **kwargs)
        if raw is None:
            return kwargs.get("default")
        try:
            return _json.loads(raw)
        except _json.JSONDecodeError as e:
            raise TypeError(f"Invalid JSON format for secret '{name}': {e}") from e

    def get_list(self, name: str, sep: str = ',', **kwargs) -> List[str]:
        raw = self.get_secret(name, blank_ok=False, **kwargs)
        if raw is None:
            return kwargs.get("default", [])
        items = str(raw).split(sep)
        return [i.strip() for i in items] if kwargs.get('strip', True) else items

    def get_int(self, name: str, **kwargs) -> Optional[int]:
        return self.get_secret(name, type_cast=int, **kwargs)

    def get_float(self, name: str, **kwargs) -> Optional[float]:
        return self.get_secret(name, type_cast=float, **kwargs)

    def get_bool(self, name: str, **kwargs) -> Optional[bool]:
        caster = cast_bool_strict if kwargs.get('strict') else _cast_to_bool
        return self.get_secret(name, type_cast=caster, **kwargs)

    def get_path(self, name: str, **kwargs) -> Optional[Path]:
        value = self.get_secret(name, **kwargs)
        if value is None:
            return None
        return Path(os.path.expanduser(os.path.expandvars(str(value)))).resolve(strict=False)

    def get_int_in_range(self, name: str, *, min_val: int, max_val: int, **kwargs) -> Optional[int]:
        """Retrieves an integer secret, validating it's within a given range."""
        v = self.get_int(name, **kwargs)
        if v is not None and not (min_val <= v <= max_val):
            raise TypeError(f"Secret '{name}' must be in range [{min_val}, {max_val}]; got {v}")
        return v

    def get_bytes(self, name: str, **kwargs) -> Optional[int]:
        """
        Parses a size string (e.g., "256M", "1g") into an integer number of bytes.
        Supports K, M, G, T suffixes (case-insensitive, power of 1024).
        """
        val = self.get_secret(name, **kwargs)
        if val is None:
            return None
        s = str(val).strip().lower()
        multipliers = {'k': 1024, 'm': 1024**2, 'g': 1024**3, 't': 1024**4}
        try:
            if s[-1] in multipliers:
                return int(float(s[:-1]) * multipliers[s[-1]])
            return int(s)
        except (ValueError, KeyError) as e:
            raise TypeError(f"Invalid byte size format for '{name}': {val!r}") from e

    def get_duration(self, name: str, **kwargs) -> Optional[float]:
        """
        Parses a duration string (e.g., "30s", "5m") into seconds.
        Supports ms, s, m, h, d suffixes (case-insensitive).
        """
        val = self.get_secret(name, **kwargs)
        if val is None:
            return None
        s = str(val).strip().lower()
        multipliers = {'ms': 0.001, 's': 1, 'm': 60, 'h': 3600, 'd': 86400}
        try:
            for suffix, mult in multipliers.items():
                if s.endswith(suffix):
                    return float(s[:-len(suffix)]) * mult
            return float(s)
        except ValueError as e:
            raise TypeError(f"Invalid duration format for '{name}': {e}") from e

    # --- Diagnostics ---

    def snapshot(self, keys: Set[str]) -> Dict[str, str]:
        """
        Returns a dictionary indicating the status ('set' or 'missing') of a
        given set of secret keys without revealing their values.
        """
        with self._lock:
            cached = {k: self._cache.get(k) for k in keys if k in self._cache}
            uncached = {k: os.environ.get(k) for k in keys if k not in self._cache}
            all_vals = {**cached, **uncached}
            return {k: ("set" if all_vals.get(k) not in (None, "") else "missing") for k in keys}


SECRETS_MANAGER = SecretsManager()
