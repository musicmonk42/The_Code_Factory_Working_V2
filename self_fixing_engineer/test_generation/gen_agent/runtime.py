from __future__ import annotations

import asyncio
import contextlib
import functools
import importlib
import importlib.metadata
import importlib.util
import json
import logging
import logging.handlers
import os
import re
import subprocess
import sys
import tempfile
import time
import types
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional, TypedDict

import tenacity
from pydantic import BaseModel

logger = logging.getLogger(__name__)

try:
    import colorama

    _COLORAMA_AVAILABLE = True
except ImportError:
    _COLORAMA_AVAILABLE = False

# Note: The check for psutil is moved into _load_and_check_deps for consistency.

#
# Elevated Configuration System
#
Dynaconf = None
BaseSettings = None
config: Any = None
_config_source: str = "SimpleNamespace"

# FIX: Prefer Pydantic due to Dynaconf Pylance issues, and it's a more modern approach.
# Pydantic preferred due to Dynaconf Pylance issues.
with contextlib.suppress(ImportError):
    from pydantic_settings import BaseSettings, Field, SettingsConfigDict

    class TestAgentConfig(BaseSettings):
        model_config = SettingsConfigDict(
            env_file=".env",
            extra="ignore",
            env_file_encoding="utf-8",
            env_prefix="TEST_AGENT_",
        )
        sessions_dir: Path = Field(
            default=Path(__file__).parent.parent.resolve() / "sessions"
        )
        tests_output_dir: Path = Field(
            default=Path(__file__).parent.parent.resolve() / "generated_tests"
        )
        llm_provider: str = Field(default="openai")
        llm_model: str = Field(default="gpt-4o")
        openai_api_key: Optional[str] = Field(default=None)
        gemini_api_key: Optional[str] = Field(default=None)
        test_run_timeout: int = Field(default=120)
        max_repairs: int = Field(default=3)
        refine_threshold: int = Field(default=80)
        anthropic_api_key: Optional[str] = Field(default=None)
        ollama_base_url: Optional[str] = Field(default="http://localhost:11434")

    config = TestAgentConfig()
    _config_source = "Pydantic"

if not config:
    with contextlib.suppress(ImportError):
        from dynaconf import Dynaconf

        config = Dynaconf(
            settings_files=["settings.toml", ".secrets.toml"],
            environments=True,
            envvar_prefix="TEST_AGENT",
            default_env="default",
        )
        _config_source = "Dynaconf"

if not config:
    logging.warning(
        "Pydantic-settings and Dynaconf not found. Using a simple config class."
    )
    config = types.SimpleNamespace(
        llm_provider=os.getenv("TEST_AGENT_LLM_PROVIDER", "openai"),
        llm_model=os.getenv("TEST_AGENT_LLM_MODEL", "gpt-4o"),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        gemini_api_key=os.getenv("GEMINI_API_KEY"),
        sessions_dir=Path(
            os.getenv(
                "TEST_AGENT_SESSIONS_DIR",
                os.path.join(os.path.dirname(os.path.dirname(__file__)), "sessions"),
            )
        ).resolve(),
        tests_output_dir=Path(
            os.getenv(
                "TEST_AGENT_TESTS_OUTPUT_DIR",
                os.path.join(
                    os.path.dirname(os.path.dirname(__file__)), "generated_tests"
                ),
            )
        ).resolve(),
        test_run_timeout=int(os.getenv("TEST_AGENT_TEST_RUN_TIMEOUT", "120")),
        max_repairs=int(os.getenv("TEST_AGENT_MAX_REPAIRS", "3")),
        refine_threshold=int(os.getenv("TEST_AGENT_REFINE_THRESHOLD", "80")),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
    )

logging.info(f"Using {_config_source} for configuration.")


def _coerce(v: str):
    """Coerce a string value to an int, float, or leave as a string."""
    try:
        return int(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            return v


def _parse_kv_string(s: str) -> dict:
    """Parses a comma-separated key=value string into a dictionary."""
    out = {}
    for part in filter(None, (p.strip() for p in s.split(","))):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = _coerce(v.strip())
    return out


@lru_cache(maxsize=1)
def _load_config(config_file: Optional[str] = None) -> Dict[str, Any]:
    """
    Load config in priority order:
      1) Dynaconf (if importable)
      2) Pydantic BaseSettings (v1-style) if available
      3) JSON file at `config_file` (if provided)
      4) {}
    Then overlay environment variables prefixed with ATCO_.
    The result is cached (tests rely on this).
    """
    loaded: Dict[str, Any] = {}

    # 1) Try Dynaconf
    try:
        from dynaconf import Dynaconf as _Dynaconf  # type: ignore
    except Exception:
        _Dynaconf = None  # type: ignore

    if _Dynaconf:
        try:
            dc = _Dynaconf(
                settings_files=[config_file] if config_file else None, environments=True
            )
            if hasattr(dc, "as_dict"):
                loaded = dict(dc.as_dict())  # type: ignore
        except Exception:
            loaded = {}
    else:
        # 2) Try Pydantic BaseSettings (v1 API lives on `pydantic.BaseSettings`)
        BaseSettings = None
        try:
            import pydantic as _pyd  # type: ignore

            BaseSettings = getattr(_pyd, "BaseSettings", None)
        except Exception:
            BaseSettings = None

        if BaseSettings is not None:
            try:
                s = BaseSettings()  # tests patch this to return a dict with {"B": 2}
                if hasattr(s, "dict"):
                    loaded = dict(s.model_dump())
                elif hasattr(s, "model_dump"):
                    loaded = dict(s.model_dump())
            except Exception:
                loaded = {}

        # 3) Fallback to JSON file
        if not loaded and config_file:
            try:
                with open(config_file, "r", encoding="utf-8") as fh:
                    loaded = json.load(fh)
            except Exception:
                loaded = {}

    # 4) Overlay ATCO_* environment variables (strip prefix, lowercase keys)
    if os.environ:
        existing_keys = list(loaded.keys())
        for k, v in os.environ.items():
            if not k.startswith("ATCO_"):
                continue
            raw_key = k[5:]  # keep original casing from the env var (after prefix)
            # Find an existing key whose lowercase matches the env key lowercase
            match = next(
                (ek for ek in existing_keys if ek.lower() == raw_key.lower()), None
            )
            target_key = match if match is not None else raw_key
            # Avoid logging secrets verbatim
            redacted = (
                "***"
                if any(
                    s in target_key.lower() for s in ("api_key", "token", "password")
                )
                else v
            )
            logger.info("Overriding config from env: %s=%s", target_key, redacted)
            loaded[target_key] = v

    return loaded


# Type hints for dynamic module loading

# Dependency checking flags
AIOFILES_AVAILABLE = False
FILELOCK_AVAILABLE = False
FLASK_AVAILABLE = False
PYTEST_AVAILABLE = False
COVERAGE_AVAILABLE = False
BANDIT_AVAILABLE = False
LOCUST_AVAILABLE = False
AUDIT_LOGGER_AVAILABLE = False
PSUTIL_AVAILABLE = False
audit_logger: Optional[Any] = None

# Prometheus metrics for health checks and dependency checks
try:
    import prometheus_client
    from prometheus_client import Counter, Gauge, registry

    try:
        dependency_status = Gauge(
            "dependency_status", "Status of optional dependencies", ["package"]
        )
    except ValueError:

        class _DG:  # tiny no-op
            def labels(self, **_: str):
                return self

            def set(self, *_: Any):
                pass

        dependency_status = _DG()
    try:
        health_check_status = Gauge("health_check_status", "Application health status")
    except ValueError:

        class _HG:
            def set(self, *_: Any):
                pass

        health_check_status = _HG()
    try:
        dependency_check_total = Counter(
            "dependency_check_total", "Total number of dependency checks"
        )
    except ValueError:

        class _DC:
            def inc(self, *_: Any, **__: Any):
                pass

        dependency_check_total = _DC()
except ImportError:

    class DummyGauge:
        def labels(self, **kwargs):
            return self

        def set(self, value):
            pass

    class DummyCounter:
        def inc(self, *args, **kwargs):
            pass

    dependency_status = DummyGauge()
    health_check_status = DummyGauge()
    dependency_check_total = DummyCounter()

_default_sessions_dir = Path(__file__).parent.parent.resolve() / "sessions"
_default_tests_output_dir = Path(__file__).parent.parent.resolve() / "generated_tests"

SESSIONS_DIR = Path(getattr(config, "sessions_dir", _default_sessions_dir))
TESTS_OUTPUT_DIR = Path(getattr(config, "tests_output_dir", _default_tests_output_dir))


# Type definition
class TestAgentState(TypedDict, total=False):
    spec: str
    spec_format: str
    language: str
    framework: str
    plan: Dict[str, Any]
    test_code: str
    review: Dict[str, Any]
    execution_results: Dict[str, Any]
    security_report: str
    performance_script: str
    repair_attempts: int
    artifacts: Dict[str, str]
    code_under_test: str
    code_path: str


# Module-level constant for allowed values
ALLOWED_LANGUAGES = {"python", "javascript", "typescript", "java", "rust", "go"}
ALLOWED_FRAMEWORKS = {"pytest", "jest", "junit", "cargo", "go test", "unittest"}
_optional_packages = {
    "pytest": "PYTEST_AVAILABLE",
    "coverage": "COVERAGE_AVAILABLE",
    "bandit": "BANDIT_AVAILABLE",
    "locust": "LOCUST_AVAILABLE",
    "flask": "FLASK_AVAILABLE",
    "aiofiles": "AIOFILES_AVAILABLE",
    "filelock": "FILELOCK_AVAILABLE",
    "dynaconf": Dynaconf,
    "pydantic_settings": BaseSettings,
    "ollama": "ollama_available",  # New
}

_LOGGING_CONFIGURED = False
_CUSTOM_HANDLERS = []


def _check_module(name: str) -> bool:
    """Checks for a module's existence, handling bad test mocks gracefully."""
    try:
        return importlib.util.find_spec(name) is not None
    except ValueError:
        return name in sys.modules


@lru_cache(maxsize=1)
def _load_and_check_deps() -> None:
    """Dynamically loads optional dependencies and updates flags."""
    global AIOFILES_AVAILABLE, FILELOCK_AVAILABLE, FLASK_AVAILABLE, PYTEST_AVAILABLE, COVERAGE_AVAILABLE, BANDIT_AVAILABLE, LOCUST_AVAILABLE, AUDIT_LOGGER_AVAILABLE, audit_logger, PSUTIL_AVAILABLE

    AIOFILES_AVAILABLE = _check_module("aiofiles")
    FILELOCK_AVAILABLE = _check_module("filelock")
    FLASK_AVAILABLE = _check_module("flask")
    PYTEST_AVAILABLE = _check_module("pytest")
    COVERAGE_AVAILABLE = _check_module("coverage")
    BANDIT_AVAILABLE = _check_module("bandit")
    LOCUST_AVAILABLE = _check_module("locust")
    PSUTIL_AVAILABLE = _check_module("psutil")

    try:
        from self_fixing_engineer.arbiter.audit_log import audit_logger as al

        audit_logger = al
        AUDIT_LOGGER_AVAILABLE = True
    except ImportError:

        class DummyAuditLogger:
            async def log_event(self, *args: Any, **kwargs: Any) -> None:
                await asyncio.sleep(0)

        audit_logger = DummyAuditLogger()
        AUDIT_LOGGER_AVAILABLE = False


def redact_sensitive(data: Any, extra_keys: Optional[set[str]] = None) -> Any:
    """Redacts sensitive information from a dictionary or string."""
    sensitive_key_names = {
        "api_key",
        "secret",
        "password",
        "token",
        "auth",
        "authorization",
        "key",
    }
    if extra_keys:
        sensitive_key_names.update({k.lower() for k in extra_keys})
    redaction_regex = re.compile(
        r"(AKIA[0-9A-Z]{16}|sk_|pk_|xoxp-|[a-zA-Z0-9_-]{32,}|[A-Za-z0-9+/]{32,}[=]{0,2}|[a-f0-9]{64})",
        re.IGNORECASE,
    )
    SENSITIVE_PATTERNS = [r"api_key=\w+", r"password=\w+"]
    SENSITIVE_KEYS = {"api_key", "password"}
    if isinstance(data, str):
        redacted_str = data
        for pattern in SENSITIVE_PATTERNS:
            redacted_str = re.sub(pattern, "[REDACTED]", redacted_str)
        redacted_str = re.sub(
            r'("Authorization"\s*:\s*)"Bearer [^"]+"', r'\1"[REDACTED]"', redacted_str
        )
        redacted_str = re.sub(
            r'("type"\s*:\s*"service_account")', r'\1"[REDACTED]"', redacted_str
        )
        return redacted_str
    if isinstance(data, dict):
        redacted_dict = {}
        for k, v in data.items():
            if (
                any(sk in k.lower() for sk in sensitive_key_names)
                or k.lower() in SENSITIVE_KEYS
            ):
                redacted_dict[k] = "[REDACTED]"
            elif isinstance(v, str):
                redacted_dict[k] = redaction_regex.sub("[REDACTED]", v)
            else:
                redacted_dict[k] = v
        return redacted_dict
    return data


def setup_logging(
    is_ci: bool = False,
    log_level: int = logging.INFO,
    log_file_path: str = "test_gen_agent.log",
    enable_file: bool = True,
) -> None:
    """Configures logging for both console and file with an improved format."""
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return
    root_logger = logging.getLogger()
    for h in _CUSTOM_HANDLERS:
        root_logger.removeHandler(h)
    _CUSTOM_HANDLERS.clear()
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    if enable_file:
        file_handler = logging.handlers.RotatingFileHandler(
            Path(log_file_path).resolve(),
            maxBytes=1024 * 1024 * 5,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter(log_format))
        file_handler.setLevel(log_level)
        root_logger.addHandler(file_handler)
        _CUSTOM_HANDLERS.append(file_handler)
    console_handler = logging.StreamHandler(sys.stdout)
    if is_ci:
        console_formatter = logging.Formatter("[%(levelname)s] %(message)s")
    else:
        if _COLORAMA_AVAILABLE:
            import colorama

            colorama.init()
        console_formatter = logging.Formatter(
            "\033[1;34m[%(levelname)s]\033[0m %(message)s"
        )
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(log_level)
    root_logger.addHandler(console_handler)
    _CUSTOM_HANDLERS.append(console_handler)
    root_logger.setLevel(log_level)
    logger.info(f"Logging set up with level {logging.getLevelName(log_level)}.")
    _LOGGING_CONFIGURED = True


def is_ci_environment() -> bool:
    """Detects if running in a common CI environment."""
    return any(
        os.environ.get(key)
        for key in [
            "CI",
            "GITHUB_ACTIONS",
            "JENKINS_URL",
            "GITLAB_CI",
            "TF_BUILD",
            "BUILDKITE",
            "TRAVIS",
            "CIRCLECI",
            "TEAMCITY_VERSION",
            "APPVEYOR",
        ]
    )


def install_package(package_name: str) -> bool:
    """Installs a Python package using pip."""
    logging.info(f"Attempting to install missing package: {package_name}")
    try:
        env = os.environ.copy()
        env["PYTHONWARNINGS"] = "ignore"
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", package_name], timeout=300, env=env
        )
        version_output = subprocess.check_output(
            [sys.executable, "-m", "pip", "show", package_name]
        ).decode()
        version_line = next(
            (
                line
                for line in version_output.splitlines()
                if line.startswith("Version:")
            ),
            None,
        )
        version = version_line.split(":")[1].strip() if version_line else "unknown"
        logging.info(f"Successfully installed {package_name} (version: {version})")
        _load_and_check_deps.cache_clear()
        _load_and_check_deps()
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        logging.error(f"Failed to install package {package_name}: {e}")
        return False


def _normalize_pkg_name(pkg_name: str) -> str:
    """Normalizes package names for importlib check."""
    return pkg_name


async def run_dependency_check(provider: Optional[str] = None, is_ci: bool = False):
    """
    Verifies that the minimal client library needed for the provider is installed.
    Raises ImportError with a clear pip hint if missing.
    """
    provider = (provider or "").lower()
    SUPPORTED_PROVIDERS = {"openai", "gemini", "anthropic", "ollama"}
    if provider and provider not in SUPPORTED_PROVIDERS:
        raise ValueError(
            f"Unsupported LLM provider: '{provider}'. Supported: {', '.join(sorted(SUPPORTED_PROVIDERS))}"
        )
    try:
        if provider == "openai":
            importlib.metadata.version("openai")
        elif provider == "gemini":
            importlib.metadata.version("google-generativeai")
        elif provider == "anthropic":
            importlib.metadata.version("langchain-anthropic")
        elif provider == "ollama":
            importlib.metadata.version("langchain-community")
    except importlib.metadata.PackageNotFoundError as e:
        hints = {
            "openai": "pip install openai",
            "gemini": "pip install google-generativeai",
            "anthropic": "pip install langchain-anthropic",
            "ollama": "pip install langchain-community",
        }
        raise ImportError(
            f"Missing package for '{provider}'. Install with: {hints.get(provider, 'pip install <package>')}"
        ) from e


async def ensure_package(package_name: str, is_ci: bool) -> bool:
    """Securely checks if a package is installed. If not, attempts to install or logs a critical error."""
    if importlib.util.find_spec(_normalize_pkg_name(package_name)) is None:
        logging.error(f"Required package '{package_name}' not found.")
        if is_ci:
            logging.critical(
                f"In CI mode, cannot auto-install. Please add '{package_name}' to your project dependencies."
            )
            return False
        else:
            if install_package(package_name):
                return (
                    importlib.util.find_spec(_normalize_pkg_name(package_name))
                    is not None
                )
            else:
                return False
    return True


def ensure_package_sync(package_name: str, is_ci: bool) -> bool:
    """Synchronous wrapper for ensure_package."""
    return asyncio.run(ensure_package(package_name, is_ci))


async def run_dependency_check_async(is_ci: bool) -> None:
    """Runs the self-healing dependency check for all optional packages."""
    dependency_check_total.inc()
    _load_and_check_deps.cache_clear()
    check_tasks = [
        ensure_package(pkg, is_ci)
        for pkg in _optional_packages.keys()
        if importlib.util.find_spec(_normalize_pkg_name(pkg)) is None
    ]
    await asyncio.gather(*check_tasks)
    _load_and_check_deps.cache_clear()
    _load_and_check_deps()
    all_ok = True
    summary_messages = []
    for pkg, _ in _optional_packages.items():
        is_installed = importlib.util.find_spec(_normalize_pkg_name(pkg)) is not None
        dependency_status.labels(package=pkg).set(1 if is_installed else 0)
        if is_ci:
            status = "Enabled" if is_installed else "Disabled"
            summary_messages.append(f"- {pkg.capitalize()} features: {status}")
        else:
            status = "Enabled" if is_installed else "Disabled"
            color = "\033[92m" if is_installed else "\033[91m"
            summary_messages.append(
                f"- {pkg.capitalize()} features: {color}{status}\033[0m"
            )
        if not is_installed:
            logging.warning(
                f"Required feature dependency '{pkg}' is missing. Functionality will be disabled."
            )
            if is_ci:
                logging.critical("CI mode requires all dependencies. Aborting.")
                sys.exit(1)
            all_ok = False
        else:
            logging.info(f"Dependency '{pkg}' is available.")
    console_stream = None
    for h in logging.getLogger().handlers:
        if isinstance(h, logging.StreamHandler):
            stream = getattr(h, "stream", None)
            if stream and hasattr(stream, "write"):
                console_stream = stream
                break
    if console_stream:
        console_stream.write("\n--- Feature Availability Summary ---\n")
        console_stream.write("\n".join(summary_messages) + "\n")
        console_stream.write("------------------------------------\n\n")
    if not all_ok:
        health_check_status.set(0)
    else:
        health_check_status.set(1)


def health_check() -> Dict[str, Any]:
    """Performs a health check of the application."""
    _load_and_check_deps.cache_clear()
    _load_and_check_deps()
    valid_config_types = tuple(
        t for t in (Dynaconf, BaseSettings, types.SimpleNamespace) if t is not None
    )
    health_status = {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "python_version": sys.version,
        "platform": sys.platform,
        "dependencies": {
            "aiofiles": bool(AIOFILES_AVAILABLE),
            "filelock": bool(FILELOCK_AVAILABLE),
            "flask": bool(FLASK_AVAILABLE),
            "pytest": bool(PYTEST_AVAILABLE),
            "coverage": bool(COVERAGE_AVAILABLE),
            "bandit": bool(BANDIT_AVAILABLE),
            "locust": bool(LOCUST_AVAILABLE),
            "audit_logger": bool(AUDIT_LOGGER_AVAILABLE),
            "config_system": isinstance(config, valid_config_types),
        },
    }
    if PSUTIL_AVAILABLE:
        import psutil

        health_status["system_info"] = {
            "cpu_count": psutil.cpu_count(),
            "memory_usage": psutil.virtual_memory().percent,
            "disk_usage": psutil.disk_usage("/").percent,
        }
    if not all(health_status["dependencies"].values()):
        health_status["status"] = "degraded"
    return health_status


async def interactive_session_creator(session_name: str) -> Optional[Dict[str, Any]]:
    """Interactive flow to create a session file in development mode."""
    if "/" in session_name or "\\" in session_name or ".." in session_name:
        raise ValueError("Invalid session name: path traversal attempt.")
    print("\n--- Session Bootstrapper ---")
    print(f"No session file '{session_name}.json' found. Let's create one.")
    spec_format = (
        input("Enter spec format (e.g., gherkin, openapi, user_story): ").strip()
        or "gherkin"
    )
    print(
        "Paste your specification below. Press Ctrl+D (Unix) or Ctrl+Z then Enter (Windows) when done."
    )
    try:
        spec_content = sys.stdin.read()
    except KeyboardInterrupt:
        logging.error("Session creation interrupted. Aborting.")
        return None
    if not spec_content or not spec_content.strip():
        logging.error("No specification content provided. Aborting.")
        return None
    session_data = {
        "spec": spec_content,
        "spec_format": spec_format,
        "created_at": datetime.now().isoformat(),
    }
    session_path = SESSIONS_DIR / f"{session_name}.json"
    try:
        session_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, encoding="utf-8", dir=str(session_path.parent)
        ) as tmp_file:
            json.dump(session_data, tmp_file, indent=2)
            temp_path = Path(tmp_file.name)
        for _ in range(3):
            try:
                temp_path.replace(session_path)
                break
            except PermissionError:
                logging.warning(
                    "Permission error on replacing file. Retrying in 0.5s..."
                )
                time.sleep(0.5)
        session_path.chmod(0o600)
        logging.info(f"Successfully created new session file at {session_path}")
        return session_data
    except IOError as e:
        logging.error(f"Failed to write session file to {session_path}: {e}")
        return None


async def ensure_session_file(
    session_name: str, is_ci: bool
) -> Optional[Dict[str, Any]]:
    """Ensures a session file exists. If not, handles creation based on mode."""
    if "/" in session_name or "\\" in session_name or ".." in session_name:
        raise ValueError("Invalid session name: path traversal attempt.")
    session_path = SESSIONS_DIR / f"{session_name}.json"
    if session_path.exists():
        try:
            if AIOFILES_AVAILABLE:
                import aiofiles

                async with aiofiles.open(session_path, "r", encoding="utf-8") as f:
                    session_data = json.loads(await f.read())
            else:
                with open(session_path, "r", encoding="utf-8") as f:
                    session_data = json.load(f)
            return session_data
        except (IOError, json.JSONDecodeError) as e:
            logging.critical(
                f"CRITICAL: Error reading session file {session_path}: {e}. Aborting."
            )
            if is_ci:
                sys.exit(1)
            else:
                response = input(
                    f"Failed to read session file '{session_path}'. Overwrite? [y/N]: "
                ).lower()
                if response == "y":
                    return await interactive_session_creator(session_name)
                else:
                    sys.exit(1)
    logging.warning(f"Session file not found: {session_path}")
    if is_ci:
        logging.critical(
            "CRITICAL: Cannot create session in non-interactive CI mode. Aborting."
        )
        sys.exit(1)
    else:
        return await interactive_session_creator(session_name)


def ensure_session_file_sync(
    session_name: str, is_ci: bool
) -> Optional[Dict[str, Any]]:
    """Synchronous wrapper for ensure_session_file."""
    return asyncio.run(ensure_session_file(session_name, is_ci))


async def load_or_create_session_spec(
    session_path: str, language: str, environment: str
):
    """Minimal wrapper used by tests; delegates to ensure_session_file()."""
    is_ci = str(environment).lower() in ("ci", "true", "1")
    session_name = Path(session_path).stem
    data = await ensure_session_file(session_name, is_ci=is_ci)
    return data or {"requirements": ""}


def validate_session_inputs(session_name: str, language: str, framework: str) -> None:
    """Validates user-provided inputs to prevent security vulnerabilities and errors."""
    if not session_name or not re.match(r"^[a-zA-Z0-9_-]+$", session_name):
        raise ValueError(f"Invalid session_name: {session_name}")
    language = language.strip().lower()
    if language not in ALLOWED_LANGUAGES:
        raise ValueError(f"Unsupported language: {language}")
    framework = framework.strip().lower()
    if framework not in ALLOWED_FRAMEWORKS:
        raise ValueError(f"Unsupported framework: {framework}")


def _validate_llm_session_inputs(provider: str, model: str) -> None:
    """
    Ensures the provider is supported and a model name is present.
    """
    SUPPORTED_PROVIDERS = {"openai", "gemini", "anthropic", "ollama"}
    provider = (provider or "").lower().strip()
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(
            f"Unsupported LLM provider: '{provider}'. Supported: {', '.join(sorted(SUPPORTED_PROVIDERS))}"
        )
    if not model or not str(model).strip():
        raise ValueError("A model must be specified for the LLM session.")


@functools.lru_cache(maxsize=1)
def init_llm(provider: Optional[str] = None, model: Optional[str] = None) -> Any:
    """Initializes the LLM based on provider and model settings from the config."""

    def _cfg(key: str, env: str) -> str | None:
        val = getattr(config, key, None)
        return val or os.getenv(env)

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(2), wait=tenacity.wait_fixed(2), reraise=True
    )
    def _init_llm_with_retry(provider: str, model: str) -> Any:
        llm_instance = None
        provider = (provider or "").lower()

        if provider == "openai":
            try:
                from langchain_openai import ChatOpenAI
            except ImportError as e:
                raise ImportError(
                    "Missing 'langchain-openai'. Install with: pip install langchain-openai"
                ) from e
            api_key = _cfg("openai_api_key", "OPENAI_API_KEY")
            if not api_key:
                raise ValueError(
                    "OpenAI API key not found in CONFIG['openai_api_key'] or OPENAI_API_KEY."
                )
            llm_instance = ChatOpenAI(model=model, api_key=api_key)

        elif provider == "gemini":
            try:
                from langchain_google_genai import ChatGoogleGenerativeAI
            except ImportError as e:
                raise ImportError(
                    "Missing 'langchain-google-genai'. Install with: pip install langchain-google-genai"
                ) from e
            api_key = _cfg("gemini_api_key", "GEMINI_API_KEY")
            if not api_key:
                raise ValueError(
                    "Gemini API key not found in CONFIG['gemini_api_key'] or GEMINI_API_KEY."
                )
            llm_instance = ChatGoogleGenerativeAI(model=model, google_api_key=api_key)

        elif provider == "anthropic":
            try:
                from langchain_anthropic import ChatAnthropic
            except ImportError as e:
                raise ImportError(
                    "Missing 'langchain-anthropic'. Install with: pip install langchain-anthropic"
                ) from e
            api_key = _cfg("anthropic_api_key", "ANTHROPIC_API_KEY")
            if not api_key:
                raise ValueError(
                    "Anthropic API key not found in CONFIG['anthropic_api_key'] or ANTHROPIC_API_KEY."
                )
            llm_instance = ChatAnthropic(model=model, anthropic_api_key=api_key)

        elif provider == "ollama":
            try:
                from langchain_community.chat_models import ChatOllama
            except ImportError as e:
                raise ImportError(
                    "Missing 'langchain-community'. Install with: pip install langchain-community"
                ) from e
            base_url = (
                _cfg("ollama_base_url", "OLLAMA_BASE_URL") or "http://localhost:11434"
            )
            llm_instance = ChatOllama(model=model, base_url=base_url)

        if llm_instance is None:
            raise ImportError(
                f"Failed to initialize LLM with provider '{provider}' and model '{model}'."
            )

        logger.info(f"Initialized LLM with provider='{provider}', model='{model}'")
        return llm_instance

    start_time = time.perf_counter()
    llm_instance = None
    try:
        provider = (provider or getattr(config, "llm_provider", "openai")).lower()
        model = model or getattr(config, "llm_model", "gpt-4o")
        _validate_llm_session_inputs(provider, model)
        llm_instance = _init_llm_with_retry(provider, model)
    except Exception as e:
        logger.error(f"Failed to initialize LLM after retries: {e}", exc_info=True)
    if llm_instance is None:

        class _Mock:
            async def ainvoke(self, *_a: Any, **_k: Any) -> Any:
                class _R:
                    content = "Mock LLM response. The real LLM client could not be initialized."

                return _R()

            def invoke(self, *_a: Any, **_k: Any) -> Any:
                class _R:
                    content = "Mock LLM response. The real LLM client could not be initialized."

                return _R()

        llm_instance = _Mock()
        logger.warning(
            "No LLM provider could be initialized. Falling back to a mock LLM."
        )
    elapsed_time = time.perf_counter() - start_time
    logger.debug(f"LLM initialization took {elapsed_time:.4f} seconds.")
    return llm_instance


def model_defaults_to_env(model_cls: type[BaseModel]) -> dict[str, Any]:
    """
    Returns a dictionary of model defaults, mapping keys to environment variable names.
    This is useful for propagating defaults from a Pydantic model to a non-Pydantic config system.
    """
    inst = model_cls()
    return {k.upper(): v for k, v in inst.model_dump().items()}
