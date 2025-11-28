import ast  # For parsing imports
import asyncio  # For running async methods
import difflib  # For diffs in audit log
import functools
import hashlib
import json
import logging
import os
import shutil  # For path validation
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, DefaultDict, Dict, List, Optional, Set, Tuple

import tomli_w  # For writing pyproject.toml

# Prefer stdlib tomllib when available
try:
    import tomllib as tomli
except ImportError:
    import tomli

# Harden the optional Redis dependency at import time
try:
    import redis.asyncio as redis

    _redis_available = True
except ImportError:
    redis = None
    _redis_available = False


# Optional: runtime probe to improve mapping
try:
    from importlib.metadata import packages_distributions

    _PKG_DIST = packages_distributions()
except Exception:
    _PKG_DIST = {}


# --- Global Flags ---
PRODUCTION_MODE = os.getenv("PRODUCTION_MODE", "false").lower() == "true"
HEAL_METRICS = os.getenv("HEAL_METRICS", "false").lower() == "true"
_metrics: DefaultDict[str, int] = defaultdict(int)
_EXTRA_SKIP_DIRS: Set[str] = set()
_CONTAINER_DIRS: Set[str] = {"src", "python", "py", "lib", "libs", "packages"}
# Allow env override/extension (comma-separated)
_CONTAINER_DIRS |= {s for s in os.getenv("HEALER_CONTAINER_DIRS", "").split(",") if s}

logger = logging.getLogger(__name__)

# --- Centralized Utilities (replacing placeholders) ---
_core_utils_loaded = False
try:
    from self_fixing_engineer.self_healing_import_fixer.analyzer.core_audit import (
        audit_logger,
    )
    from self_fixing_engineer.self_healing_import_fixer.analyzer.core_utils import (
        alert_operator,
        scrub_secrets,
    )

    _core_utils_loaded = True
except ImportError as e:
    # Fallback to relative import for when running within the package
    try:
        from self_healing_import_fixer.analyzer.core_audit import audit_logger
        from self_healing_import_fixer.analyzer.core_utils import (
            alert_operator,
            scrub_secrets,
        )

        _core_utils_loaded = True
    except ImportError:
        logger.warning(f"Core utilities not loaded (optional): {e}.")
        _core_utils_loaded = False


def _alert_operator_or_log(message: str, level: str = "CRITICAL"):
    """
    A safe wrapper around alert_operator that falls back to logging if the core module is not loaded.
    """
    if _core_utils_loaded:
        alert_operator(message, level)
    else:
        logger.critical(f"[OPS ALERT - {level}] {message}")


# --- Custom Exception Hierarchy ---
class HealerError(RuntimeError):
    """Base exception for the dependency healer module."""

    pass


class ConfigError(HealerError):
    """Raised for critical configuration errors that should halt execution."""

    pass


class SecurityViolationError(HealerError):
    """Raised when a potential security violation, like path traversal, is detected."""

    def __init__(self, message: str, path: str, whitelist: List[str]):
        super().__init__(message)
        self.path = path
        self.whitelist = whitelist
        if _core_utils_loaded:
            audit_logger.log_event(
                "security_violation",
                type="path_traversal_attempt",
                path=path,
                whitelisted_paths=whitelist,
                message=message,
            )
            _alert_operator_or_log(
                f"CRITICAL: Security violation: {message}", level="CRITICAL"
            )


class FilesystemAccessError(HealerError):
    """Raised for file-related issues like read/write permissions."""

    def __init__(self, message: str, path: str):
        super().__init__(message)
        self.path = path
        if _core_utils_loaded:
            _alert_operator_or_log(
                f"CRITICAL: Filesystem access error: {message}", level="CRITICAL"
            )


class HealerNonCriticalError(HealerError):
    """
    Custom exception for recoverable issues that should be logged but not halt execution.
    """

    pass


# --- Atomic Write Helper ---
def _atomic_write_text(path: Path, data: str) -> None:
    """Writes text data to a file atomically."""
    d = path.parent
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=d, delete=False
    ) as tf:
        tf.write(data)
    os.replace(tf.name, path)


# --- Dependency and Toolchain Verification ---
@functools.lru_cache(maxsize=8)  # Set maxsize > 1 to cache multiple Python versions
def _get_stdlib_set(python_version: str) -> Set[str]:
    """
    Safely gets the standard library set, with fallbacks.
    """
    try:
        from stdlib_list import stdlib_list

        logger.debug(f"Loaded stdlib for Python {python_version}.")
        return set(stdlib_list(python_version))
    except (ImportError, KeyError) as e:
        if PRODUCTION_MODE:
            raise ConfigError(
                f"stdlib_list for Python {python_version} not found. Accurate external dependency detection is critical in PRODUCTION_MODE."
            ) from e
        else:
            logger.warning(
                f"Python 'stdlib_list' library not found or version {python_version} is unsupported. External dependency detection will be limited. Falling back to sys.stdlib_module_names if available."
            )
            if _core_utils_loaded:
                audit_logger.log_event(
                    "dependency_healing_warning",
                    reason="stdlib_list_fallback",
                    python_version=python_version,
                )
                _alert_operator_or_log(
                    f"WARNING: stdlib_list for Python {python_version} missing. Dependency detection less accurate.",
                    level="WARNING",
                )
            if sys.version_info >= (3, 10):
                return set(sys.stdlib_module_names)
            else:
                # A hardcoded fallback for older Python versions
                logger.warning("Using a conservative, hardcoded stdlib list fallback.")
                return {
                    "os",
                    "sys",
                    "re",
                    "math",
                    "json",
                    "logging",
                    "typing",
                    "collections",
                    "asyncio",
                    "shutil",
                    "tempfile",
                    "pathlib",
                    "functools",
                    "hashlib",
                    "difflib",
                    "importlib",
                    "time",
                    "datetime",
                    "io",
                    "abc",
                    "argparse",
                    "inspect",
                }
    except Exception as e:
        raise ConfigError(f"Unexpected error loading stdlib_list: {e}.") from e


# --- Caching: Redis Client Initialization with File Fallback ---
_redis_client_instance = None
_file_cache_dir = None


async def _get_cache_client():
    """Lazily initializes and returns the cache client (Redis or file-based)."""
    global _redis_client_instance, _file_cache_dir

    if _redis_client_instance:
        return _redis_client_instance

    # Try Redis first
    if _redis_available:
        try:
            client = redis.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", 6379)),
                db=0,
                decode_responses=True,
            )
            await client.ping()  # Probe the connection
            _redis_client_instance = client
            logger.info("Connected to Redis for caching.")
            return _redis_client_instance
        except Exception as e:
            logger.warning(
                f"Failed to connect to Redis for caching: {e}. Falling back to file cache."
            )
            if _core_utils_loaded:
                _alert_operator_or_log(
                    f"WARNING: Redis cache failed: {e}. Using file cache.",
                    level="WARNING",
                )

    # Fallback to file cache
    if not _file_cache_dir:
        primary_root = (
            Path(os.path.abspath(_whitelisted_project_paths[0]))
            if _whitelisted_project_paths
            else None
        )
        if not primary_root:
            logger.error(
                "Cannot initialize file cache without a whitelisted root path."
            )
            return None
        _file_cache_dir = primary_root / ".healer_cache"
        _file_cache_dir.mkdir(exist_ok=True)
        logger.info(f"Using file cache at {_file_cache_dir}")

    class FileCacheClient:
        def __init__(self, cache_dir):
            self.cache_dir = cache_dir

        async def get(self, key):
            file_path = (
                self.cache_dir / f"{hashlib.sha256(key.encode()).hexdigest()}.json"
            )
            if file_path.exists():
                try:
                    raw = file_path.read_text("utf-8")
                    try:
                        payload = json.loads(raw)
                        # honor TTL if present
                        exp = payload.get("exp")
                        if exp is not None and time.time() >= float(exp):
                            try:
                                file_path.unlink(missing_ok=True)
                            except Exception:
                                pass
                            return None
                        return payload.get("v", raw)
                    except Exception:
                        # backwards compatibility: pre-TTL content was bare string
                        return raw
                except Exception as e:
                    logger.error(f"Failed to read from file cache {file_path}: {e}")
            return None

        async def setex(self, key, expiry, value):
            file_path = (
                self.cache_dir / f"{hashlib.sha256(key.encode()).hexdigest()}.json"
            )
            try:
                payload = {
                    "v": value,
                    "exp": time.time() + float(expiry) if expiry else None,
                }
                with file_path.open("w", encoding="utf-8") as f:
                    json.dump(payload, f)
            except Exception as e:
                logger.error(f"Failed to write to file cache {file_path}: {e}")

    _redis_client_instance = FileCacheClient(cache_dir=_file_cache_dir)
    return _redis_client_instance


# --- Filesystem Controls: Whitelisted locations ---
_whitelisted_project_paths: List[str] = []


def _within_whitelist(path: str, wl: List[str]) -> bool:
    """Checks if a given path is within any of the whitelisted paths using commonpath equality."""

    def _norm(p: str) -> str:
        return os.path.normcase(os.path.realpath(p))

    try:
        rp = _norm(path)
        for w in wl:
            ww = _norm(w)
            if os.path.commonpath([rp, ww]) == ww:
                return True
    except (ValueError, OSError) as e:
        logger.error("Whitelist check error for %s: %s", path, e)
    return False


def init_dependency_healing_module(whitelisted_paths: List[str]):
    """Initializes the dependency healing module with whitelisted paths."""
    global _whitelisted_project_paths
    if not whitelisted_paths:
        raise ConfigError("Whitelisted project paths cannot be empty.")

    _whitelisted_project_paths = [os.path.abspath(p) for p in whitelisted_paths]
    logger.info(
        f"Dependency healing module initialized with whitelisted paths: {_whitelisted_project_paths}"
    )


# --- Concurrency Semaphore ---
_parse_concurrency_sem = None


def _get_parse_sem(workers: Optional[int] = None) -> asyncio.Semaphore:
    """Lazily initializes and returns the concurrency semaphore, with a CPU-aware default."""
    global _parse_concurrency_sem
    if _parse_concurrency_sem is None:
        if workers is not None:
            val = workers
        else:
            try:
                env = os.getenv("HEALER_PARSE_CONCURRENCY")
                if env is not None:
                    val = int(env)
                else:
                    cores = os.cpu_count() or 4
                    val = min(64, max(4, cores * 4))
            except (ValueError, TypeError):
                logger.error("Invalid HEALER_PARSE_CONCURRENCY; using default.")
                val = 32

        if val < 1:
            val = 1

        _parse_concurrency_sem = asyncio.Semaphore(val)
        logger.info(f"Using a parsing concurrency of {val}.")
    return _parse_concurrency_sem


def _skip_dirs() -> Set[str]:
    """Returns the set of directories to skip, including configurable ones."""
    base = {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        "__pycache__",
        "build",
        "dist",
        ".tox",
        ".ruff_cache",
        ".coverage",
        "htmlcov",
        ".eggs",
        "vendor",
        "third_party",
        "site-packages",
        "docs/_build",
        "node_modules",
        ".idea",
        ".vscode",
    }
    env_skips = {s for s in os.getenv("HEALER_SKIP_DIRS", "").split(",") if s}
    return base | _EXTRA_SKIP_DIRS | env_skips


# --- Dependency Scanning Utilities ---
def _get_py_files(roots: List[str]) -> List[str]:
    """
    Recursively finds all Python (.py) files within the specified project roots,
    respecting whitelisted paths.
    """
    SKIP_DIRS = _skip_dirs()
    py_files = set()
    for root in roots:
        if not _within_whitelist(root, _whitelisted_project_paths):
            raise SecurityViolationError(
                f"Attempted to scan root '{root}' which is outside whitelisted paths.",
                path=root,
                whitelist=_whitelisted_project_paths,
            )

        if not os.path.isdir(root):
            logger.warning(f"Root directory not found: {root}. Skipping.")
            continue

        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            # prune noisy dirs in-place for perf
            dirnames[:] = [
                d
                for d in dirnames
                if d not in SKIP_DIRS and not os.path.islink(os.path.join(dirpath, d))
            ]
            if not _within_whitelist(dirpath, _whitelisted_project_paths):
                logger.debug(f"Skipping directory outside whitelisted paths: {dirpath}")
                continue

            for f in filenames:
                if f.endswith(".py"):
                    file_path = os.path.join(dirpath, f)
                    if not os.access(file_path, os.R_OK):
                        raise FilesystemAccessError(
                            f"No read access to file: {file_path}. Aborting.",
                            path=file_path,
                        )
                    py_files.add(file_path)
    logger.debug(f"Found {len(py_files)} Python files across roots: {roots}")
    if HEAL_METRICS:
        _metrics["files_scanned"] += len(py_files)
    return sorted(list(py_files))


def _get_module_map_sync(
    roots: List[str],
) -> Tuple[Dict[str, List[str]], Dict[str, str]]:
    """
    Creates a mapping from top-level module names to their full module paths,
    and from file paths to their module names. (Synchronous version)
    """
    SKIP_DIRS = _skip_dirs()
    module_map = defaultdict(list)
    file_to_mod = {}

    for root in roots:
        if not _within_whitelist(root, _whitelisted_project_paths):
            raise SecurityViolationError(
                f"Attempted to map modules in root '{root}' which is outside whitelisted paths.",
                path=root,
                whitelist=_whitelisted_project_paths,
            )

        if not os.path.isdir(root):
            continue

        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = [
                d
                for d in dirnames
                if d not in SKIP_DIRS and not os.path.islink(os.path.join(dirpath, d))
            ]
            if not _within_whitelist(dirpath, _whitelisted_project_paths):
                logger.debug(f"Skipping directory outside whitelisted paths: {dirpath}")
                continue

            rel_dir = os.path.relpath(dirpath, root)
            rel_dir_mod_path = rel_dir.replace(os.sep, ".")
            if rel_dir_mod_path == ".":
                rel_dir_mod_path = ""

            for f in filenames:
                if f.endswith(".py"):
                    full_path = os.path.join(dirpath, f)

                    if f == "__init__.py":
                        # Only use the actual module path under the root; avoid inventing a name
                        # from the root directory (which may contain hyphens).
                        if not rel_dir_mod_path:
                            continue
                        mod_name = rel_dir_mod_path
                    else:
                        mod_base = f[:-3]
                        mod_name = ".".join(filter(None, [rel_dir_mod_path, mod_base]))

                    top_level_package = mod_name.split(".")[0].replace("-", "_")
                    if top_level_package not in module_map:
                        module_map[top_level_package] = []
                    if mod_name not in module_map[top_level_package]:
                        module_map[top_level_package].append(mod_name)
                    file_to_mod[full_path] = mod_name

    return dict(module_map), file_to_mod


async def _get_module_map(
    roots: List[str],
) -> Tuple[Dict[str, List[str]], Dict[str, str]]:
    """
    Creates a mapping from top-level module names to their full module paths,
    and from file paths to their module names with caching.
    """
    cache_client = await _get_cache_client()

    # Normalize paths for a stable cache key
    norm_roots = [os.path.normcase(os.path.realpath(p)) for p in roots]
    cache_key = (
        "module_map:"
        + hashlib.sha256(
            json.dumps(sorted(norm_roots), separators=(",", ":")).encode()
        ).hexdigest()
    )

    if cache_client:
        try:
            cached_data = await cache_client.get(cache_key)
            if cached_data:
                module_map_data, file_to_mod_data = json.loads(cached_data)
                logger.debug("Loaded module map from cache.")
                return module_map_data, file_to_mod_data
        except Exception as e:
            logger.warning(f"Failed to retrieve cached module map: {e}")

    module_map, file_to_mod = await asyncio.to_thread(_get_module_map_sync, roots)

    if cache_client:
        try:
            await cache_client.setex(
                cache_key, 86400, json.dumps((module_map, file_to_mod))
            )
            logger.debug("Module map cached successfully.")
        except Exception as e:
            logger.warning(f"Failed to cache module map: {e}")

    logger.debug(f"Module map generated. Total files mapped: {len(file_to_mod)}")
    return module_map, file_to_mod


def _discover_local_top_levels(
    roots: List[str], file_to_mod: Dict[str, str]
) -> Set[str]:
    """
    Determine local top-level packages robustly, including src/ and similar layouts.
    - From module names, take first component; if it's a container dir, also take the second.
    - From filesystem, look one level under each root and under each container dir.
    """
    local: Set[str] = set()
    # From module map (covers namespace packages too)
    for mod in file_to_mod.values():
        parts = [p for p in mod.split(".") if p]
        if not parts:
            continue
        local.add(parts[0].replace("-", "_"))
        if parts[0] in _CONTAINER_DIRS and len(parts) >= 2:
            local.add(parts[1].replace("-", "_"))

    def _scan(parent: str):
        if not os.path.isdir(parent):
            return
        try:
            for entry in os.listdir(parent):
                p = os.path.join(parent, entry)
                if not os.path.isdir(p):
                    continue
                try:
                    if os.path.isfile(os.path.join(p, "__init__.py")) or any(
                        fn.endswith(".py") for fn in os.listdir(p)
                    ):
                        local.add(entry.replace("-", "_"))
                except OSError:
                    continue
        except OSError:
            pass

    for root in roots:
        _scan(root)
        for cd in _CONTAINER_DIRS:
            _scan(os.path.join(root, cd))
    return local


def _is_type_checking_test(test: ast.AST) -> bool:
    # matches `if TYPE_CHECKING:` or `if typing.TYPE_CHECKING:`
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute)
        and isinstance(test.value, ast.Name)
        and test.value.id in {"typing", "typing_extensions"}
        and test.attr == "TYPE_CHECKING"
    )


class ImportCollector(ast.NodeVisitor):
    def __init__(self, file_imports, file_path):
        self.file_imports = file_imports
        self.file_path = file_path
        self.in_tc = 0  # depth in TYPE_CHECKING block

    def visit_If(self, node: ast.If):
        if _is_type_checking_test(node.test):
            self.in_tc += 1
            for n in node.body:
                self.visit(n)
            self.in_tc -= 1
            # If there's an `else` block, visit it, as it's not a type-checking block
            for n in node.orelse:
                self.visit(n)
        else:
            self.generic_visit(node)

    def visit_Import(self, node: ast.Import):
        if self.in_tc:
            return
        for alias in node.names:
            full = alias.name
            parts = full.split(".")
            top = parts[0]
            self.file_imports[top].append(f"{self.file_path}:{node.lineno}")
            if len(parts) >= 2:
                two = ".".join(parts[:2])
                self.file_imports[two].append(f"{self.file_path}:{node.lineno}")

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if self.in_tc or not node.module:
            return
        parts = node.module.split(".")
        top = parts[0]
        self.file_imports[top].append(f"{self.file_path}:{node.lineno}")
        if len(parts) >= 2:
            two = ".".join(parts[:2])
            self.file_imports[two].append(f"{self.file_path}:{node.lineno}")


@functools.lru_cache(maxsize=10000)
def _parse_file_imports_cached(path: str, mtime: float) -> Dict[str, List[str]]:
    file_imports = defaultdict(list)
    with open(path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    ImportCollector(file_imports, path).visit(tree)
    return dict(file_imports)


def _parse_file_imports(file_path: str) -> Dict[str, List[str]]:
    """
    Parses a single Python file to extract its top-level import names and their locations. (Synchronous)
    """
    if not _within_whitelist(file_path, _whitelisted_project_paths):
        raise SecurityViolationError(
            f"Attempted to parse imports in file '{file_path}' which is outside whitelisted paths.",
            path=file_path,
            whitelist=_whitelisted_project_paths,
        )

    try:
        try:
            mtime = os.path.getmtime(file_path)
        except OSError:
            mtime = 0.0
        return _parse_file_imports_cached(file_path, mtime)
    except SyntaxError as e:
        logger.error(
            f"Syntax error in {file_path}: {e}. Skipping import parsing for this file."
        )
        if _core_utils_loaded:
            audit_logger.log_event(
                "dependency_scan_failure",
                reason="syntax_error",
                file=file_path,
                error=str(e),
            )
            _alert_operator_or_log(
                f"ERROR: Syntax error in {file_path} during dependency scan. Review file.",
                level="ERROR",
            )
        raise HealerNonCriticalError(f"Syntax error in {file_path}: {e}. Skipping.")
    except Exception as e:
        logger.error(f"Error parsing imports from {file_path}: {e}", exc_info=True)
        if _core_utils_loaded:
            audit_logger.log_event(
                "dependency_scan_failure",
                reason="parsing_error",
                file=file_path,
                error=str(e),
            )
            _alert_operator_or_log(
                f"ERROR: Unexpected error parsing {file_path} during dependency scan. Review file.",
                level="ERROR",
            )
        raise
    # Should be unreachable due to re-raise
    return {}


async def _get_all_imports_async(
    py_files: List[str], workers: Optional[int] = None
) -> Dict[str, List[str]]:
    """
    Parses all Python files to extract all top-level import names and their locations asynchronously,
    with backpressure.
    """
    sem = _get_parse_sem(workers)

    async def _bounded_parse(file_path: str):
        async with sem:
            return await asyncio.to_thread(_parse_file_imports, file_path)

    tasks = [_bounded_parse(f) for f in py_files]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_imports = defaultdict(list)
    for result in results:
        if isinstance(result, Exception):
            if isinstance(result, HealerNonCriticalError):
                if HEAL_METRICS:
                    _metrics["errors_noncritical"] += 1
                continue
            else:
                if HEAL_METRICS:
                    _metrics["errors_critical"] += 1
                raise result

        for dep, locations in result.items():
            all_imports[dep].extend(locations)

    if HEAL_METRICS:
        _metrics["imports_parsed"] += len(all_imports)
    return dict(all_imports)


# Common remaps (extend as needed)
_IMPORT_TO_DIST = {
    "PIL": "Pillow",
    "cv2": "opencv-python",
    "yaml": "PyYAML",
    "ruamel.yaml": "ruamel.yaml",
    "ruamel": "ruamel.yaml",
    "skimage": "scikit-image",
    "bs4": "beautifulsoup4",
    "sklearn": "scikit-learn",
    "OpenSSL": "pyOpenSSL",
    "Crypto": "pycryptodome",
    "Crypto.Cipher": "pycryptodome",
    "dateutil": "python-dateutil",
    "dotenv": "python-dotenv",
    "jinja2": "Jinja2",
    "lxml": "lxml",
    "ujson": "ujson",
    "orjson": "orjson",
    "google.protobuf": "protobuf",
    "boto3": "boto3",
    "botocore": "botocore",
}


def _normalize_dep_name(name: str) -> str:
    """Normalizes a dependency name for comparison (e.g., 'Flask-SQLAlchemy' -> 'flask-sqlalchemy')."""
    return (
        name.split("[")[0]
        .split(">")[0]
        .split("<")[0]
        .split("=")[0]
        .strip()
        .lower()
        .replace("_", "-")
        .replace(".", "-")
    )


def _import_to_distribution(name: str) -> str:
    """Best-effort mapping from import path to PyPI distribution (prefer specific → generic)."""
    parts = name.split(".")
    candidates = []
    if len(parts) >= 2:
        candidates.append(".".join(parts[:2]))  # two-level (e.g., google.protobuf)
    candidates.append(parts[0])  # top-level

    # 1) curated remaps, then 2) runtime hints (deterministic)
    for cand in candidates:
        if cand in _IMPORT_TO_DIST:
            return _IMPORT_TO_DIST[cand]
        if cand in _PKG_DIST:
            dists = sorted(_PKG_DIST.get(cand, []))
            if dists:
                return dists[0]

    # 2.5) heuristics for common vendor namespaces
    if parts[0] == "google" and len(parts) >= 3 and parts[1] == "cloud":
        # google.cloud.storage -> google-cloud-storage
        return "google-cloud-" + parts[2].replace("_", "-")
    if parts[0] == "azure" and len(parts) >= 2:
        # azure.storage.blob -> azure-storage-blob, azure.identity -> azure-identity, etc.
        if len(parts) > 2:
            return "azure-" + "-".join(parts[1:])
        return "azure-" + parts[1].replace("_", "-")

    # 3) fallback to normalized top-level
    logger.debug(
        f"dist map fallback: import '{name}' → '{_normalize_dep_name(parts[0])}'"
    )
    return _normalize_dep_name(parts[0])


def _get_pyproject_deps(pyproject_data: Dict) -> Set[str]:
    """Extracts and returns a normalized set of dependency names from pyproject.toml."""
    deps = set()
    if "project" in pyproject_data and "dependencies" in pyproject_data["project"]:
        deps.update(
            _normalize_dep_name(d) for d in pyproject_data["project"]["dependencies"]
        )
    # Also check optional-dependencies to avoid false positives for missing dependencies
    for _extra, _deps in (
        pyproject_data.get("project", {}).get("optional-dependencies", {}).items()
    ):
        for _d in _deps or []:
            deps.add(_normalize_dep_name(_d))

    return deps


def _is_test_path(p: str) -> bool:
    """Heuristic to check if a path is likely part of a test suite."""
    bn = os.path.basename(p)
    return (
        "/tests/" in p.replace("\\", "/")
        or bn.startswith("test_")
        or bn.endswith("_test.py")
    )


# --- Dependency File Healing Logic ---
async def heal_dependencies(
    project_roots: List[str],
    dry_run: bool,
    python_version: str,
    prune_unused: bool = False,
    fail_on_diff: bool = False,
    workers: Optional[int] = None,
    sync_reqs: bool = False,
    dev_extra: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Scans for external Python dependencies used in the codebase and synchronizes
    `pyproject.toml` and `requirements.txt` files.
    Identifies missing and unused dependencies.

    Note: If importing this library directly, you must call init_dependency_healing_module()
    before calling this function. You must also set the PRODUCTION_MODE and HEAL_METRICS
    environment variables or globals before import to enable their respective behaviors.

    Args:
        project_roots (List[str]): List of root directories of the project/monorepo.
        dry_run (bool): If True, only report changes without modifying files.
        python_version (str): The Python version string (e.g., "3.9") for stdlib detection.
        prune_unused (bool): If True, removes unused dependencies from pyproject.toml.
        fail_on_diff (bool): If True and not a dry run, exits with a non-zero exit code if changes are needed.
        workers (Optional[int]): Number of concurrent workers for file parsing.
        sync_reqs (bool): Create or update requirements.txt to mirror pyproject dependencies.
        dev_extra (Optional[str]): If provided, dependencies only used in tests will be added
                                   to a [project.optional-dependencies.{name}] section.

    Returns:
        Dict[str, Any]: A dictionary detailing added and removed dependencies and proposed diffs.
    """
    if not _whitelisted_project_paths:
        raise ConfigError(
            "Dependency healer not initialized. "
            "Call init_dependency_healing_module([...]) with whitelisted project roots first."
        )

    _metrics.clear()

    logger.info("Healing external dependencies...")
    if _core_utils_loaded:
        audit_logger.log_event(
            "dependency_healing_start",
            project_roots=project_roots,
            dry_run=dry_run,
            python_version=python_version,
            prune_unused=prune_unused,
        )

    all_py_files = _get_py_files(project_roots)
    if not all_py_files:
        logger.info(
            "No Python files found in specified roots. Skipping dependency healing."
        )
        if _core_utils_loaded:
            audit_logger.log_event(
                "dependency_healing_skipped", reason="no_python_files_found"
            )
        return {
            "added": [],
            "removed": [],
            "pyproject_diff": "",
            "requirements_diff": "",
        }

    _, file_to_mod = await _get_module_map(project_roots)

    # Identify local (in-repo) top-level packages to exclude from "external" deps
    local_top_levels = _discover_local_top_levels(project_roots, file_to_mod)

    all_imports_with_locations = await _get_all_imports_async(
        all_py_files, workers=workers
    )

    _BUILD_TOOLS = {
        "pkg_resources",
        "setuptools",
        "pip",
        "distutils",
        "wheel",
        "build",
        "hatchling",
    }

    def _top(n: str) -> str:
        return n.split(".")[0]

    stdlib = _get_stdlib_set(python_version)
    external_deps_raw = {
        name
        for name in all_imports_with_locations.keys()
        if _top(name) not in stdlib
        and _top(name) not in local_top_levels
        and _top(name) not in _BUILD_TOOLS
    }

    # Create a mapping from import name to distribution name for consistent tracking
    import_to_dist = {imp: _import_to_distribution(imp) for imp in external_deps_raw}
    dist_to_imports = defaultdict(list)
    for imp, dist in import_to_dist.items():
        dist_to_imports[dist].append(imp)

    # Prefer concrete google.* providers over umbrella 'google'
    if "google" in dist_to_imports:
        has_specific_google = any(
            any(imp.startswith("google.") for imp in imps)
            for d, imps in dist_to_imports.items()
            if d != "google"
        )
        if has_specific_google:
            dist_to_imports.pop("google", None)

    # Normalize to *distribution* names so missing/unused agree
    imported_dists_normalized = {
        _normalize_dep_name(dist) for dist in dist_to_imports.keys()
    }

    primary_root = Path(project_roots[0])
    pyproject_path = primary_root / "pyproject.toml"
    requirements_path = primary_root / "requirements.txt"

    pyproject_data = {}
    pyproject_original_content = b""

    if not _within_whitelist(str(pyproject_path), _whitelisted_project_paths):
        raise SecurityViolationError(
            f"pyproject.toml path '{pyproject_path}' is outside whitelisted paths.",
            path=str(pyproject_path),
            whitelist=_whitelisted_project_paths,
        )

    if pyproject_path.exists():
        if not os.access(pyproject_path, os.R_OK):
            raise FilesystemAccessError(
                f"No read access to {pyproject_path}. Aborting.",
                path=str(pyproject_path),
            )
        try:
            pyproject_original_content = pyproject_path.read_bytes()
            pyproject_data = tomli.loads(pyproject_original_content.decode("utf-8"))
            if "tool" in pyproject_data and "poetry" in pyproject_data["tool"]:
                logger.warning(
                    "Poetry project detected. Modifying PEP 621 `project.dependencies` only."
                )

            logger.info(
                f"Loaded {len(_get_pyproject_deps(pyproject_data))} dependencies from pyproject.toml."
            )
        except Exception as e:
            if _core_utils_loaded:
                audit_logger.log_event(
                    "dependency_healing_failure",
                    reason="pyproject_read_error",
                    file=str(pyproject_path),
                    error=str(e),
                )
            raise FilesystemAccessError(
                f"Error reading pyproject.toml at {pyproject_path}: {e}. Aborting.",
                path=str(pyproject_path),
            ) from e

    current_pyproject_deps_normalized = _get_pyproject_deps(pyproject_data)
    preferred_dists = set(dist_to_imports.keys())
    missing_deps = [
        d
        for d in sorted(preferred_dists, key=_normalize_dep_name)
        if _normalize_dep_name(d) not in current_pyproject_deps_normalized
    ]

    log_missing = logger.info if dry_run else logger.warning
    if missing_deps:
        log_missing(f"Found missing dependencies: {missing_deps}")
        for dist in missing_deps:
            imps = dist_to_imports.get(dist, [])
            locs = []
            for imp in imps:
                locs.extend(all_imports_with_locations.get(imp, []))
            logger.info(
                "Dependency '%s' used in: %s",
                dist,
                ", ".join(locs) if locs else "Unknown location",
            )
    else:
        logger.info("No missing dependencies detected.")

    def _has_env_marker(spec: str) -> bool:
        return ";" in spec

    unused_deps = []
    for dep_full_spec in pyproject_data.get("project", {}).get("dependencies", []):
        dep_name_normalized = _normalize_dep_name(dep_full_spec)
        if (
            dep_name_normalized not in imported_dists_normalized
            and not _has_env_marker(dep_full_spec)
        ):
            unused_deps.append(dep_full_spec)

    log_unused = logger.info if dry_run else logger.warning
    if unused_deps:
        log_unused(
            f"Found potentially unused dependencies in pyproject.toml: {unused_deps}"
        )
    else:
        logger.info("No unused dependencies detected in pyproject.toml.")

    pyproject_diff_str = ""
    requirements_diff_str = ""

    # Generate proposed changes for diffing
    proposed_pyproject_data = pyproject_data.copy()
    if "project" not in proposed_pyproject_data:
        proposed_pyproject_data["project"] = {}
    if "dependencies" not in proposed_pyproject_data["project"]:
        proposed_pyproject_data["project"]["dependencies"] = []

    # Handle dev-only dependencies
    main_deps_to_add = []
    dev_deps_to_add = []
    if dev_extra:
        test_only_dists = set()
        for dist in missing_deps:
            imps = dist_to_imports.get(dist, [])
            locs = []
            for imp in imps:
                locs.extend(all_imports_with_locations.get(imp, []))

            if locs and all(_is_test_path(l.split(":", 1)[0]) for l in locs):
                test_only_dists.add(dist)

        for dep in missing_deps:
            if dep in test_only_dists:
                dev_deps_to_add.append(dep)
            else:
                main_deps_to_add.append(dep)
    else:
        main_deps_to_add = missing_deps

    # Add missing main dependencies
    for dep in main_deps_to_add:
        dep_norm = _normalize_dep_name(dep)
        if not any(
            _normalize_dep_name(d) == dep_norm
            for d in proposed_pyproject_data["project"]["dependencies"]
        ):
            proposed_pyproject_data["project"]["dependencies"].append(dep)

    # Add missing dev dependencies
    if dev_extra and dev_deps_to_add:
        opt_deps = proposed_pyproject_data["project"].setdefault(
            "optional-dependencies", {}
        )
        dev_deps = opt_deps.setdefault(dev_extra, [])
        for dep in dev_deps_to_add:
            dep_norm = _normalize_dep_name(dep)
            if not any(_normalize_dep_name(d) == dep_norm for d in dev_deps):
                dev_deps.append(dep)
        opt_deps[dev_extra] = sorted(list(set(dev_deps)), key=_normalize_dep_name)

    # Handle unused dependencies based on flag
    if prune_unused:
        new_pyproject_deps = [
            d
            for d in proposed_pyproject_data["project"]["dependencies"]
            if d not in unused_deps
        ]
        proposed_pyproject_data["project"]["dependencies"] = new_pyproject_deps

    proposed_pyproject_data["project"]["dependencies"] = sorted(
        list(set(proposed_pyproject_data["project"]["dependencies"])),
        key=lambda s: _normalize_dep_name(s),
    )

    original_pyproject_text = (
        pyproject_original_content.decode("utf-8") if pyproject_original_content else ""
    )
    pyproject_new_content = tomli_w.dumps(proposed_pyproject_data)
    if pyproject_new_content != original_pyproject_text:
        pyproject_diff = difflib.unified_diff(
            original_pyproject_text.splitlines(keepends=True),
            pyproject_new_content.splitlines(keepends=True),
            fromfile=f"{pyproject_path} (original)",
            tofile=f"{pyproject_path} (proposed)",
        )
        pyproject_diff_str = "".join(pyproject_diff)

    # Requirements.txt diff
    requirements_new_content = None
    if sync_reqs or requirements_path.exists():
        requirements_original_content = ""
        if requirements_path.exists():
            if not _within_whitelist(
                str(requirements_path), _whitelisted_project_paths
            ):
                raise SecurityViolationError(
                    f"requirements.txt path '{requirements_path}' is outside whitelisted paths.",
                    path=str(requirements_path),
                    whitelist=_whitelisted_project_paths,
                )
            if not os.access(requirements_path, os.R_OK):
                raise FilesystemAccessError(
                    f"No read access to {requirements_path}. Aborting.",
                    path=str(requirements_path),
                )
            requirements_original_content = requirements_path.read_text("utf-8")

        req_lines = sorted(
            list(set(proposed_pyproject_data["project"]["dependencies"])),
            key=lambda s: _normalize_dep_name(s),
        )
        requirements_new_content = "\n".join(req_lines) + ("\n" if req_lines else "")

        if (
            not requirements_path.exists()
            or requirements_new_content != requirements_original_content
        ):
            requirements_diff = difflib.unified_diff(
                requirements_original_content.splitlines(keepends=True),
                requirements_new_content.splitlines(keepends=True),
                fromfile=f"{requirements_path} (original)",
                tofile=f"{requirements_path} (proposed)",
            )
            requirements_diff_str = "".join(requirements_diff)

    # If dry run, we are done. Return the diffs.
    if dry_run:
        return {
            "added": missing_deps,
            "removed": unused_deps if prune_unused else [],
            "pyproject_diff": pyproject_diff_str,
            "requirements_diff": requirements_diff_str,
        }

    # Check for fail-on-diff condition
    if fail_on_diff and (pyproject_diff_str or requirements_diff_str):
        raise HealerError("Changes were detected and --fail-on-diff is set.")

    # --- Actual writes ---
    changes_made = missing_deps or (prune_unused and unused_deps)
    if changes_made:
        # Write to pyproject.toml
        try:
            if not os.access(pyproject_path.parent, os.W_OK):
                raise FilesystemAccessError(
                    f"No write access to directory {pyproject_path.parent}. Aborting.",
                    path=str(pyproject_path.parent),
                )

            if pyproject_path.exists():
                shutil.copy2(pyproject_path, pyproject_path.with_suffix(".toml.bak"))
                logger.info(
                    f"Backed up pyproject.toml to {pyproject_path.with_suffix('.toml.bak')}"
                )
                if _core_utils_loaded:
                    audit_logger.log_event(
                        "file_backup",
                        file=str(pyproject_path),
                        backup_path=str(pyproject_path.with_suffix(".toml.bak")),
                    )

            _atomic_write_text(pyproject_path, pyproject_new_content)
            logger.info(f"pyproject.toml has been updated at {pyproject_path}.")

            safe_py_diff = (
                scrub_secrets(pyproject_diff_str)
                if _core_utils_loaded
                else pyproject_diff_str
            )
            diff_hash = hashlib.sha256(safe_py_diff.encode()).hexdigest()
            if _core_utils_loaded:
                audit_logger.log_event(
                    "dependency_healing_pyproject_updated",
                    path=str(pyproject_path),
                    added=missing_deps,
                    removed=unused_deps if prune_unused else [],
                    diff_hash=diff_hash,
                    added_count=len(missing_deps),
                    removed_count=len(unused_deps if prune_unused else []),
                    hostname=os.uname().nodename if hasattr(os, "uname") else None,
                    pid=os.getpid(),
                )
        except Exception as e:
            if _core_utils_loaded:
                audit_logger.log_event(
                    "dependency_healing_failure",
                    reason="pyproject_write_error",
                    file=str(pyproject_path),
                    error=str(e),
                )
            raise FilesystemAccessError(
                f"Failed to write to pyproject.toml at {pyproject_path}: {e}. Aborting.",
                path=str(pyproject_path),
            ) from e

        # Write to requirements.txt
        if requirements_new_content is not None and (
            sync_reqs or requirements_path.exists()
        ):
            try:
                if not _within_whitelist(
                    str(requirements_path), _whitelisted_project_paths
                ):
                    raise SecurityViolationError(
                        f"requirements.txt path '{requirements_path}' is outside whitelisted paths.",
                        path=str(requirements_path),
                        whitelist=_whitelisted_project_paths,
                    )
                if not os.access(requirements_path.parent, os.W_OK):
                    raise FilesystemAccessError(
                        f"No write access to directory {requirements_path.parent}. Aborting.",
                        path=str(requirements_path.parent),
                    )

                if requirements_path.exists():
                    shutil.copy2(
                        requirements_path, requirements_path.with_suffix(".txt.bak")
                    )
                    logger.info(
                        f"Backed up requirements.txt to {requirements_path.with_suffix('.txt.bak')}"
                    )
                    if _core_utils_loaded:
                        audit_logger.log_event(
                            "file_backup",
                            file=str(requirements_path),
                            backup_path=str(requirements_path.with_suffix(".txt.bak")),
                        )

                _atomic_write_text(requirements_path, requirements_new_content)
                logger.info(
                    f"requirements.txt has been updated at {requirements_path}."
                )

                safe_req_diff = (
                    scrub_secrets(requirements_diff_str)
                    if _core_utils_loaded
                    else requirements_diff_str
                )
                diff_hash = hashlib.sha256(safe_req_diff.encode()).hexdigest()
                if _core_utils_loaded:
                    audit_logger.log_event(
                        "dependency_healing_requirements_updated",
                        path=str(requirements_path),
                        added=missing_deps,
                        removed=unused_deps if prune_unused else [],
                        diff_hash=diff_hash,
                        added_count=len(missing_deps),
                        removed_count=len(unused_deps if prune_unused else []),
                        hostname=os.uname().nodename if hasattr(os, "uname") else None,
                        pid=os.getpid(),
                    )
            except Exception as e:
                if _core_utils_loaded:
                    audit_logger.log_event(
                        "dependency_healing_failure",
                        reason="requirements_write_error",
                        file=str(requirements_path),
                        error=str(e),
                    )
                raise FilesystemAccessError(
                    f"Failed to write to requirements.txt at {requirements_path}: {e}. Aborting.",
                    path=str(requirements_path),
                ) from e
    else:
        log_info = logger.info if not dry_run else logger.debug
        log_info("No changes to dependency files were needed or applied.")

    return {
        "added": missing_deps,
        "removed": unused_deps if prune_unused else [],
        "pyproject_diff": pyproject_diff_str,
        "requirements_diff": requirements_diff_str,
    }


# --- CLI Wrapper ---
def main():
    """
    Command-line entry point for the dependency healer.
    """
    import argparse
    import time

    parser = argparse.ArgumentParser(
        description="Heal Python dependencies in a project."
    )
    parser.add_argument(
        "--roots",
        nargs="+",
        required=True,
        help="List of project root directories to scan.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only report changes without modifying files.",
    )
    parser.add_argument(
        "--python-version",
        default=f"{sys.version_info.major}.{sys.version_info.minor}",
        help="Python version string (e.g., '3.9') for stdlib detection.",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Prompts for confirmation before applying changes.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Automatically confirm all changes. Overrides --confirm.",
    )
    parser.add_argument(
        "--prune-unused",
        action="store_true",
        help="Removes unused dependencies from pyproject.toml and requirements.txt.",
    )
    parser.add_argument(
        "--prod",
        action="store_true",
        help="Run in production mode, which enforces stricter checks.",
    )
    parser.add_argument(
        "--metrics",
        action="store_true",
        help="Enable Prometheus-compatible metrics output.",
    )
    parser.add_argument(
        "--fail-on-diff",
        action="store_true",
        help="Exits with a non-zero status if changes are detected.",
    )
    parser.add_argument(
        "--json-output",
        action="store_true",
        help="Prints the final result as a JSON object for CI/CD consumption.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        help="Number of concurrent workers for file parsing. Overrides HEALER_PARSE_CONCURRENCY env var.",
    )
    parser.add_argument(
        "--sync-reqs",
        action="store_true",
        help="Create/update requirements.txt to mirror pyproject dependencies.",
    )
    parser.add_argument(
        "--dev-extra",
        type=str,
        help="Name of the optional-dependencies extra for test-only dependencies (e.g., 'dev').",
    )
    parser.add_argument(
        "-v", "--verbose", action="count", default=0, help="-v for INFO, -vv for DEBUG"
    )
    parser.add_argument(
        "--skip-dirs",
        default="",
        help="Comma-separated directory names to skip (extends HEALER_SKIP_DIRS).",
    )
    parser.add_argument(
        "--container-dirs",
        default="",
        help="Comma-separated packaging container dirs (e.g., 'src,python,lib') to treat as roots for local packages.",
    )

    args = parser.parse_args()

    level = logging.WARNING
    if args.verbose == 1:
        level = logging.INFO
    elif args.verbose >= 2:
        level = logging.DEBUG

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    global PRODUCTION_MODE, HEAL_METRICS, _EXTRA_SKIP_DIRS, _CONTAINER_DIRS
    PRODUCTION_MODE = args.prod
    HEAL_METRICS = args.metrics
    if args.skip_dirs:
        _EXTRA_SKIP_DIRS |= {s for s in args.skip_dirs.split(",") if s}
    if args.container_dirs:
        _CONTAINER_DIRS |= {s for s in args.container_dirs.split(",") if s}

    start_time = time.monotonic()

    try:
        init_dependency_healing_module(whitelisted_paths=args.roots)

        logger.info("Performing dry run to generate diffs...")
        dry_run_results = asyncio.run(
            heal_dependencies(
                project_roots=args.roots,
                dry_run=True,
                python_version=args.python_version,
                prune_unused=args.prune_unused,
                workers=args.workers,
                sync_reqs=args.sync_reqs,
                dev_extra=args.dev_extra,
            )
        )

        pyproject_diff = dry_run_results["pyproject_diff"]
        requirements_diff = dry_run_results["requirements_diff"]

        should_apply = False
        if args.dry_run:
            logger.info("Dry run finished. No files were modified.")
            should_apply = False
        elif pyproject_diff or requirements_diff:
            if args.fail_on_diff:
                logger.error(
                    "Changes were detected and --fail-on-diff is set. Exiting."
                )
                sys.exit(1)
            if args.confirm and not args.yes:
                print("\n--- Proposed changes to pyproject.toml ---")
                print(pyproject_diff)
                print("\n--- Proposed changes to requirements.txt ---")
                print(requirements_diff)
                response = input("\nApply these changes? (y/N) ").lower()
                if response == "y":
                    should_apply = True
                else:
                    logger.info("Changes declined by user. Aborting.")
                    should_apply = False
            elif args.yes:
                logger.info("Auto-confirming changes due to --yes flag.")
                should_apply = True
            else:
                logger.info("Applying changes directly (non-interactive mode).")
                should_apply = True
        else:
            logger.info("No changes to dependency files were needed or applied.")

        final_results = dry_run_results
        if should_apply:
            final_results = asyncio.run(
                heal_dependencies(
                    project_roots=args.roots,
                    dry_run=False,
                    python_version=args.python_version,
                    prune_unused=args.prune_unused,
                    workers=args.workers,
                    sync_reqs=args.sync_reqs,
                    dev_extra=args.dev_extra,
                )
            )

        if args.json_output:
            print(json.dumps(final_results, indent=2))

    except (ConfigError, SecurityViolationError, FilesystemAccessError) as e:
        logger.critical(f"A critical error occurred: {e}")
        sys.exit(1)
    except Exception:
        logger.exception("An unexpected error occurred during dependency healing.")
        sys.exit(1)
    finally:
        end_time = time.monotonic()
        if HEAL_METRICS:
            _metrics["total_seconds"] = end_time - start_time
            print("\n--- Metrics ---")
            for k, v in _metrics.items():
                print(f"# HELP fixer_dep_{k} {k.replace('_', ' ')}")
                print(f"# TYPE fixer_dep_{k} gauge")
                print(f"fixer_dep_{k} {v}")


if __name__ == "__main__":
    main()
