from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import shutil
import tempfile
import time
import uuid
from contextlib import contextmanager
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

# --- Logger Setup (initialize before anything that might log) ---
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    import sys

    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# --- Conditional Imports for GitPython ---
try:
    import git  # GitPython library
    from git.exc import GitCommandError, InvalidGitRepositoryError, NoSuchPathError
    from git.repo import Repo

    GITPYTHON_AVAILABLE = True
except ImportError:
    logger.warning(
        "GitPython library not found. Cross-repository refactoring functionality will be disabled."
    )
    git = None
    Repo = type("Repo", (object,), {})  # Dummy class
    GitCommandError = type("GitCommandError", (Exception,), {})
    InvalidGitRepositoryError = type("InvalidGitRepositoryError", (Exception,), {})
    NoSuchPathError = type("NoSuchPathError", (Exception,), {})
    GITPYTHON_AVAILABLE = False

# --- Conditional Imports for Tenacity (separate from GitPython) ---
try:
    from tenacity import (
        retry,
        retry_if_exception_type,
        stop_after_attempt,
        wait_exponential,
    )

    TENACITY_AVAILABLE = True
except ImportError:
    logger.warning("tenacity not found. Retries will be disabled.")

    def retry(*args, **kwargs):
        def wrap(f):
            return f

        return wrap

    def stop_after_attempt(n):
        return None

    def wait_exponential(*args, **kwargs):
        return None

    def retry_if_exception_type(e):
        return lambda x: False

    TENACITY_AVAILABLE = False


# --- Helpers for env booleans ---
def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "t", "yes", "y", "on")


# --- Prometheus Metrics (Idempotent Definition) ---
try:
    from prometheus_client import REGISTRY, Counter, Histogram
    from simulation.utils import get_or_create_metric

    # Use the safe metric creation function from simulation.utils
    def _safe_counter(name, doc, labelnames=()):
        return get_or_create_metric(Counter, name, doc, labelnames)

    def _safe_histogram(name, doc, labelnames=(), buckets=None):
        return get_or_create_metric(Histogram, name, doc, labelnames, buckets)

    # Low-cardinality metrics (no refactor_id label to avoid cardinality explosion)
    CROSS_REPO_REFACTOR_ATTEMPTS = _safe_counter(
        "cross_repo_refactor_attempts_total",
        "Total cross-repo refactor attempts",
        labelnames=("status",),
    )
    CROSS_REPO_REFACTOR_SUCCESS = _safe_counter(
        "cross_repo_refactor_success_total", "Total successful cross-repo refactors"
    )
    CROSS_REPO_REFACTOR_ERRORS = _safe_counter(
        "cross_repo_refactor_errors_total",
        "Total errors during cross-repo refactors",
        labelnames=("error_type",),
    )
    GIT_OPERATION_LATENCY_SECONDS = _safe_histogram(
        "cross_repo_git_op_latency_seconds",
        "Latency of Git operations",
        labelnames=("operation",),
    )
except ImportError:
    logger.warning(
        "Prometheus client not found. Metrics for cross-repo refactor plugin will be disabled."
    )

    class DummyMetric:
        # Add DEFAULT_BUCKETS to match Histogram.DEFAULT_BUCKETS
        DEFAULT_BUCKETS = (
            0.005,
            0.01,
            0.025,
            0.05,
            0.075,
            0.1,
            0.25,
            0.5,
            0.75,
            1.0,
            2.5,
            5.0,
            7.5,
            10.0,
            float("inf"),
        )

        def inc(self, amount: float = 1.0):
            pass

        def set(self, value: float):
            pass

        def observe(self, value: float):
            pass

        def labels(self, *args, **kwargs):
            return self

    CROSS_REPO_REFACTOR_ATTEMPTS = DummyMetric()
    CROSS_REPO_REFACTOR_SUCCESS = DummyMetric()
    CROSS_REPO_REFACTOR_ERRORS = DummyMetric()
    GIT_OPERATION_LATENCY_SECONDS = DummyMetric()

# --- PLUGIN MANIFEST ---
PLUGIN_MANIFEST = {
    "name": "CrossRepoRefactorPlugin",
    "version": "1.3.0",
    "description": "Handles complex logic for coordinated code refactoring across multiple Git repositories.",
    "author": "Self-Fixing Engineer Team",
    "capabilities": [
        "cross_repo_refactoring",
        "multi_repo_sync",
        "distributed_patching",
        "git_automation",
    ],
    "permissions_required": ["git_clone_write", "git_push", "network_access_git"],
    "compatibility": {
        "min_sim_runner_version": "1.0.0",
        "max_sim_runner_version": "2.0.0",
    },
    "entry_points": {
        "perform_cross_repo_refactor": {
            "description": "Orchestrates and executes a refactoring plan across specified Git repositories.",
            "parameters": [
                "refactor_plan",
                "git_credentials",
                "dry_run",
                "cleanup_on_success",
                "cleanup_on_failure",
            ],
        }
    },
    "health_check": "plugin_health",
    "api_version": "v1",
    "license": "MIT",
    "homepage": "https://www.self-fixing.engineer",
    "tags": ["refactoring", "git", "multi-repo", "automation", "code_health"],
}

# --- Plugin-Specific Configuration ---
GIT_CONFIG = {
    "default_branch_prefix": os.getenv("GIT_REFACTOR_BRANCH_PREFIX", "sfe-refactor/"),
    "default_author_name": os.getenv("GIT_AUTHOR_NAME", "Self-Fixing Engineer Bot"),
    "default_author_email": os.getenv("GIT_AUTHOR_EMAIL", "bot@self-fixing.engineer"),
    "clone_timeout_seconds": int(os.getenv("GIT_CLONE_TIMEOUT_SECONDS", "300")),
    "push_timeout_seconds": int(os.getenv("GIT_PUSH_TIMEOUT_SECONDS", "90")),
    "git_op_timeout_seconds": int(os.getenv("GIT_OP_TIMEOUT_SECONDS", "120")),
    "pr_api_base_url": os.getenv(
        "GIT_PR_API_BASE_URL", "https://api.github.com"
    ),  # For GitHub
    "pr_api_token": os.getenv("GIT_PR_API_TOKEN"),  # For creating PRs
    "retry_attempts": int(os.getenv("GIT_RETRY_ATTEMPTS", "3")),
    "retry_backoff_factor": float(os.getenv("GIT_RETRY_BACKOFF_FACTOR", "2.0")),
    # Enhancements
    "max_concurrency": int(os.getenv("GIT_MAX_CONCURRENCY", "3")),
    "validate_remote_in_health": _env_bool("GIT_HEALTH_VALIDATE_REMOTE", False),
    "health_sample_repo_url": os.getenv("GIT_HEALTH_SAMPLE_REPO_URL", ""),
    "scrub_pushurl_on_retain": _env_bool("SCRUB_PUSHURL_ON_RETAIN", True),
}

# --- Audit Logger Integration (Conceptual) ---
try:
    # Corrected import to resolve the 'SFE AuditLogger not found' warning
    # The file path is also relative to the project root, so we need to
    # import from 'arbiter.audit_log'
    from self_fixing_engineer.arbiter.audit_log import TamperEvidentLogger as SFE_AuditLogger

    _sfe_audit_logger = SFE_AuditLogger()
except ImportError:
    logger.warning(
        "SFE AuditLogger not found. Audit events will be logged to plugin's logger only."
    )

    class MockAuditLogger:
        async def log_event(
            self, event_type: str, details: Dict[str, Any], **kwargs: Any
        ):
            logger.info(f"[AUDIT] {event_type}: {details}")

    _sfe_audit_logger = MockAuditLogger()


async def _audit_event(event_type: str, details: Dict[str, Any]):
    """Centralized audit logging for the plugin."""
    try:
        # Corrected method name from 'log' to 'log_event'
        await _sfe_audit_logger.log_event(event_type, details)
    except Exception:
        # Never fail main flow due to audit issues
        logger.debug(f"AUDIT_LOG_FAIL: {event_type} {details}")


# --- Utility helpers ---
@contextmanager
def _temp_environ(env_updates: Dict[str, str]):
    """Temporarily set environment variables."""
    old_env = {}
    try:
        for k, v in env_updates.items():
            old_env[k] = os.environ.get(k)
            os.environ[k] = v
        yield
    finally:
        for k, v in env_updates.items():
            if old_env[k] is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old_env[k]


def _build_https_push_url(repo_url: str, username: str, token: str) -> str:
    """Build a tokenized HTTPS URL for push (do not log this)."""
    p = urlparse(repo_url)
    if p.scheme != "https":
        return repo_url
    netloc = f"{username}:{token}@{p.netloc}"
    return p._replace(netloc=netloc).geturl()


def _extract_owner_repo(repo_url: str) -> Optional[Tuple[str, str]]:
    """Extract (owner, repo) from HTTPS or SSH-style Git URL (GitHub-style two components)."""
    try:
        if repo_url.startswith("git@"):
            # git@github.com:owner/repo.git
            _, path_part = repo_url.split(":", 1)
            path_no_git = path_part.removesuffix(".git")
            owner, repo = path_no_git.split("/", 1)
            return owner, repo
        else:
            p = urlparse(repo_url)
            parts = p.path.strip("/").split("/")
            if len(parts) >= 2:
                owner = parts[0]
                repo = parts[1].removesuffix(".git")
                return owner, repo
    except Exception:
        return None
    return None


def _mask_token_in_url(url: str) -> str:
    """Mask token in a URL for logging."""
    try:
        p = urlparse(url)
        if "@" in p.netloc and ":" in p.netloc.split("@")[0]:
            creds, host = p.netloc.split("@", 1)
            user, _ = creds.split(":", 1)
            return p._replace(netloc=f"{user}:***@{host}").geturl()
    except Exception:
        pass
    return url


def _is_safe_path(base: str, path: str) -> bool:
    """Ensure 'path' is within 'base' directory to avoid traversal."""
    base_abs = os.path.abspath(base)
    path_abs = os.path.abspath(path)
    try:
        return os.path.commonpath([base_abs]) == os.path.commonpath(
            [base_abs, path_abs]
        )
    except Exception:
        return False


def _path_has_symlink(base: str, target_path: str) -> bool:
    """Return True if any component from base to target is a symlink."""
    base_abs = os.path.abspath(base)
    target_abs = os.path.abspath(target_path)
    try:
        rel = os.path.relpath(target_abs, base_abs)
    except ValueError:
        return True
    parts = rel.split(os.sep)
    cur = base_abs
    for part in parts:
        cur = os.path.join(cur, part)
        if os.path.islink(cur):
            return True
    return False


async def _to_thread_timeout(
    func: Callable,
    *args,
    timeout: Optional[float] = None,
    env: Optional[Dict[str, str]] = None,
    **kwargs,
):
    """Run blocking function in a thread with an overall timeout, optionally with temp env vars."""

    async def _runner():
        if env:
            with _temp_environ(env):
                return await asyncio.to_thread(func, *args, **kwargs)
        return await asyncio.to_thread(func, *args, **kwargs)

    return await asyncio.wait_for(_runner(), timeout=timeout)


# --- GitPython Wrapper for Asynchronous and Retriable Operations ---
class GitRepoManager:
    """
    Manages Git operations for a single repository, ensuring asynchronous execution
    via asyncio.to_thread with timeouts and robust retries (when tenacity is available).
    Note: asyncio.wait_for cancels the await but cannot forcibly kill underlying git subprocesses.
    """

    def __init__(
        self,
        repo_url: str,
        temp_clone_path: str,
        credentials: Optional[Dict[str, str]] = None,
        refactor_id: Optional[str] = None,
    ):
        self.repo_url = repo_url
        self.temp_clone_path = temp_clone_path
        self.credentials = credentials or {}
        self._repo: Optional[Repo] = None
        self.refactor_id = refactor_id or "unknown"

    async def _get_repo(self) -> Repo:
        """Lazily initializes and returns the GitPython Repo object (assumes already cloned)."""
        if self._repo is None:
            if not GITPYTHON_AVAILABLE:
                raise GitCommandError("GitPython SDK not available.")
            self._repo = await asyncio.to_thread(Repo, self.temp_clone_path)
        return self._repo

    @retry(
        stop=stop_after_attempt(GIT_CONFIG["retry_attempts"]),
        wait=wait_exponential(
            multiplier=GIT_CONFIG["retry_backoff_factor"], min=1, max=10
        ),
        retry=retry_if_exception_type(
            (GitCommandError, NoSuchPathError, asyncio.TimeoutError)
        ),
    )
    async def clone_repo(self) -> None:
        """Clones the repository asynchronously with optional HTTPS token or SSH key support."""
        start_time = time.monotonic()
        try:
            if os.path.exists(self.temp_clone_path) and os.listdir(
                self.temp_clone_path
            ):
                logger.info(
                    f"[{self.refactor_id}] Directory {self.temp_clone_path} already exists, assuming cloned."
                )
                self._repo = await asyncio.to_thread(Repo, self.temp_clone_path)
                return

            clone_env: Dict[str, str] = {}
            clone_url = self.repo_url

            # SSH key support
            if self.repo_url.startswith(("ssh://", "git@")) and self.credentials.get(
                "ssh_key_path"
            ):
                ssh_key = self.credentials["ssh_key_path"]
                clone_env["GIT_SSH_COMMAND"] = (
                    f"ssh -i {ssh_key} -o IdentitiesOnly=yes -o StrictHostKeyChecking=no"
                )
                logger.info(f"[{self.refactor_id}] Using SSH key for cloning.")

            # HTTPS token support (use tokenized URL for clone if needed; sanitize remote afterward)
            if (
                self.repo_url.startswith("https://")
                and self.credentials.get("token")
                and self.credentials.get("username")
            ):
                token_url = _build_https_push_url(
                    self.repo_url,
                    self.credentials["username"],
                    self.credentials["token"],
                )
                clone_url = token_url  # might be needed for private repos
                logger.info(
                    f"[{self.refactor_id}] Cloning via HTTPS with token (URL masked): {_mask_token_in_url(clone_url)}"
                )

            logger.info(
                f"[{self.refactor_id}] Cloning {_mask_token_in_url(self.repo_url)} to {self.temp_clone_path}..."
            )
            self._repo = await _to_thread_timeout(
                Repo.clone_from,
                clone_url,
                self.temp_clone_path,
                timeout=GIT_CONFIG["clone_timeout_seconds"],
                env=clone_env,
            )
            GIT_OPERATION_LATENCY_SECONDS.labels(operation="clone").observe(
                time.monotonic() - start_time
            )
            logger.info(
                f"[{self.refactor_id}] Repository {_mask_token_in_url(self.repo_url)} cloned successfully."
            )

            # Sanitize remote URLs: keep fetch URL clean, set tokenized push URL (for HTTPS)
            if self._repo and self.repo_url.startswith("https://"):
                try:
                    remote = await asyncio.to_thread(self._repo.remote, "origin")
                    clean_fetch_url = self.repo_url
                    push_url = clean_fetch_url
                    if self.credentials.get("token") and self.credentials.get(
                        "username"
                    ):
                        push_url = _build_https_push_url(
                            clean_fetch_url,
                            self.credentials["username"],
                            self.credentials["token"],
                        )
                    await asyncio.to_thread(remote.set_url, clean_fetch_url)
                    await asyncio.to_thread(remote.set_url, push_url, True)  # push URL
                except Exception as e:
                    logger.warning(
                        f"[{self.refactor_id}] Failed to set sanitized remote URLs: {e}"
                    )

        except Exception as e:
            CROSS_REPO_REFACTOR_ERRORS.labels(error_type="clone_failed").inc()
            logger.error(
                f"[{self.refactor_id}] Failed to clone {_mask_token_in_url(self.repo_url)}: {e}",
                exc_info=True,
            )
            raise  # Re-raise for tenacity

    @retry(
        stop=stop_after_attempt(GIT_CONFIG["retry_attempts"]),
        wait=wait_exponential(
            multiplier=GIT_CONFIG["retry_backoff_factor"], min=1, max=10
        ),
        retry=retry_if_exception_type((GitCommandError, asyncio.TimeoutError)),
    )
    async def prepare_branch(self, base_branch: str, refactor_branch: str) -> None:
        """
        Fetches from origin, checks out base_branch, and creates/checkout refactor_branch from it.
        """
        repo = await self._get_repo()
        start_time = time.monotonic()
        try:
            # fetch origin
            await _to_thread_timeout(
                repo.git.fetch,
                "origin",
                "--prune",
                timeout=GIT_CONFIG["git_op_timeout_seconds"],
            )
            # checkout base branch (create tracking branch if needed)
            try:
                await _to_thread_timeout(
                    repo.git.checkout,
                    base_branch,
                    timeout=GIT_CONFIG["git_op_timeout_seconds"],
                )
            except Exception:
                await _to_thread_timeout(
                    repo.git.checkout,
                    "-B",
                    base_branch,
                    f"origin/{base_branch}",
                    timeout=GIT_CONFIG["git_op_timeout_seconds"],
                )
            # create/checkout refactor branch
            existing = any(h.name == refactor_branch for h in repo.branches)
            if existing:
                logger.warning(
                    f"[{self.refactor_id}] Branch '{refactor_branch}' already exists. Checking it out."
                )
                await _to_thread_timeout(
                    repo.git.checkout,
                    refactor_branch,
                    timeout=GIT_CONFIG["git_op_timeout_seconds"],
                )
            else:
                await _to_thread_timeout(
                    repo.git.checkout,
                    "-b",
                    refactor_branch,
                    timeout=GIT_CONFIG["git_op_timeout_seconds"],
                )
            logger.info(
                f"[{self.refactor_id}] Prepared branch '{refactor_branch}' from base '{base_branch}'."
            )
            GIT_OPERATION_LATENCY_SECONDS.labels(operation="checkout").observe(
                time.monotonic() - start_time
            )
        except Exception as e:
            CROSS_REPO_REFACTOR_ERRORS.labels(error_type="checkout_failed").inc()
            logger.error(
                f"[{self.refactor_id}] Failed to prepare branches (base={base_branch}, refactor={refactor_branch}): {e}",
                exc_info=True,
            )
            raise

    @retry(
        stop=stop_after_attempt(GIT_CONFIG["retry_attempts"]),
        wait=wait_exponential(
            multiplier=GIT_CONFIG["retry_backoff_factor"], min=1, max=10
        ),
        retry=retry_if_exception_type((GitCommandError, asyncio.TimeoutError)),
    )
    async def add_and_commit(self, file_paths: List[str], commit_message: str) -> str:
        """Adds specified files and creates a commit."""
        repo = await self._get_repo()
        start_time = time.monotonic()
        try:
            # Configure commit author (local)
            try:
                cw = await asyncio.to_thread(repo.config_writer)
                await asyncio.to_thread(
                    cw.set_value, "user", "name", GIT_CONFIG["default_author_name"]
                )
                await asyncio.to_thread(
                    cw.set_value, "user", "email", GIT_CONFIG["default_author_email"]
                )
                await asyncio.to_thread(cw.release)
            except Exception as e:
                logger.warning(
                    f"[{self.refactor_id}] Failed to set local author info: {e}"
                )

            await _to_thread_timeout(
                repo.index.add, file_paths, timeout=GIT_CONFIG["git_op_timeout_seconds"]
            )
            commit = await _to_thread_timeout(
                repo.index.commit,
                commit_message,
                timeout=GIT_CONFIG["git_op_timeout_seconds"],
            )
            GIT_OPERATION_LATENCY_SECONDS.labels(operation="commit").observe(
                time.monotonic() - start_time
            )
            logger.info(
                f"[{self.refactor_id}] Committed changes in {_mask_token_in_url(self.repo_url)}: {commit.hexsha}"
            )
            return commit.hexsha
        except Exception as e:
            CROSS_REPO_REFACTOR_ERRORS.labels(error_type="commit_failed").inc()
            logger.error(
                f"[{self.refactor_id}] Failed to add/commit in {_mask_token_in_url(self.repo_url)}: {e}",
                exc_info=True,
            )
            raise

    @retry(
        stop=stop_after_attempt(GIT_CONFIG["retry_attempts"]),
        wait=wait_exponential(
            multiplier=GIT_CONFIG["retry_backoff_factor"], min=1, max=10
        ),
        retry=retry_if_exception_type((GitCommandError, asyncio.TimeoutError)),
    )
    async def push_branch(self, branch_name: str, remote_name: str = "origin") -> None:
        """Pushes the specified branch to the remote using refspec local:remote."""
        repo = await self._get_repo()
        start_time = time.monotonic()
        try:
            remote = await asyncio.to_thread(repo.remote, remote_name)
            push_env: Dict[str, str] = {}
            if self.repo_url.startswith(("ssh://", "git@")) and self.credentials.get(
                "ssh_key_path"
            ):
                ssh_key = self.credentials["ssh_key_path"]
                push_env["GIT_SSH_COMMAND"] = (
                    f"ssh -i {ssh_key} -o IdentitiesOnly=yes -o StrictHostKeyChecking=no"
                )
            refspec = f"{branch_name}:{branch_name}"
            await _to_thread_timeout(
                remote.push,
                refspec,
                timeout=GIT_CONFIG["push_timeout_seconds"],
                env=push_env,
            )
            GIT_OPERATION_LATENCY_SECONDS.labels(operation="push").observe(
                time.monotonic() - start_time
            )
            logger.info(
                f"[{self.refactor_id}] Pushed branch {branch_name} to {remote_name} in {_mask_token_in_url(self.repo_url)}."
            )
        except Exception as e:
            CROSS_REPO_REFACTOR_ERRORS.labels(error_type="push_failed").inc()
            logger.error(
                f"[{self.refactor_id}] Failed to push branch {branch_name} for {_mask_token_in_url(self.repo_url)}: {e}",
                exc_info=True,
            )
            raise

    async def create_pull_request(
        self, title: str, body: str, head_branch: str, base_branch: str = "main"
    ) -> Optional[str]:
        """
        Creates a Pull Request on a Git platform (e.g., GitHub via API).
        Formats 'head' as 'owner:branch' when likely pushing from a fork.
        """
        pr_api_url = GIT_CONFIG["pr_api_base_url"]
        pr_api_token = GIT_CONFIG["pr_api_token"]
        if not pr_api_url or not pr_api_token:
            logger.warning(
                f"[{self.refactor_id}] PR API URL or Token not configured. Cannot create Pull Request."
            )
            return None

        owner_repo = _extract_owner_repo(self.repo_url)
        if not owner_repo:
            logger.error(
                f"[{self.refactor_id}] Could not parse repo URL for PR creation: {_mask_token_in_url(self.repo_url)}"
            )
            return None
        owner, repo_name = owner_repo
        api_endpoint = f"{pr_api_url}/repos/{owner}/{repo_name}/pulls"

        # Derive head: for forks on GitHub, it's "<fork_owner>:<branch>"
        head_value = head_branch
        cred_user = self.credentials.get("username")
        if cred_user and cred_user != owner:
            head_value = f"{cred_user}:{head_branch}"

        payload = {
            "title": title,
            "body": body,
            "head": head_value,
            "base": base_branch,
        }
        headers = {
            "Authorization": f"Bearer {pr_api_token}",
            "Content-Type": "application/json",
            "Accept": "application/vnd.github+json",
        }

        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=GIT_CONFIG["push_timeout_seconds"])
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    api_endpoint, headers=headers, json=payload
                ) as response:
                    resp_text = await response.text()
                    if response.status >= 400:
                        # Try to extract message for 422 validation errors
                        try:
                            err_json = json.loads(resp_text)
                            msg = err_json.get("message") or resp_text
                        except Exception:
                            msg = resp_text
                        CROSS_REPO_REFACTOR_ERRORS.labels(error_type="pr_failed").inc()
                        logger.error(
                            f"[{self.refactor_id}] PR creation failed ({response.status}): {msg[:512]}"
                        )
                        return None
                    pr_data = await response.json()
                    logger.info(
                        f"[{self.refactor_id}] Pull Request created: {pr_data.get('html_url')}"
                    )
                    return pr_data.get("html_url")
        except Exception as e:
            CROSS_REPO_REFACTOR_ERRORS.labels(error_type="pr_failed").inc()
            logger.error(
                f"[{self.refactor_id}] Failed to create Pull Request for {_mask_token_in_url(self.repo_url)}: {e}",
                exc_info=True,
            )
            return None


# --- PLUGIN HEALTH CHECK ---
async def plugin_health() -> Dict[str, Any]:
    """
    Performs a health check on the GitPython library and Git CLI availability.
    Optionally validates remote connectivity when configured.
    """
    status = "ok"
    details: List[str] = []

    if not GITPYTHON_AVAILABLE:
        status = "error"
        details.append("GitPython library not found. Plugin cannot function.")
        logger.error(details[-1])
        return {"status": status, "details": details}

    # Check for Git CLI
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            details.append(f"Git CLI detected: {stdout.decode().strip()}.")
        else:
            raise RuntimeError("Git CLI found but returned non-zero exit code.")
    except (FileNotFoundError, RuntimeError) as e:
        status = "error"
        details.append(
            f"Git CLI not found in PATH or error: {e}. Cannot perform Git operations."
        )
        logger.error(details[-1])

    # Optional remote validation (non-destructive)
    if GIT_CONFIG["validate_remote_in_health"] and GIT_CONFIG["health_sample_repo_url"]:
        sample = GIT_CONFIG["health_sample_repo_url"]
        try:
            # Lightweight heads listing with a hard timeout
            ls_proc = await asyncio.create_subprocess_exec(
                "git",
                "ls-remote",
                "--heads",
                sample,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                await asyncio.wait_for(ls_proc.wait(), timeout=15)
            except asyncio.TimeoutError:
                ls_proc.kill()
                raise RuntimeError("git ls-remote timed out")
            if ls_proc.returncode == 0:
                details.append(f"Remote connectivity OK for sample: {sample}")
            else:
                err = (await ls_proc.stderr.read()).decode(errors="ignore").strip()
                details.append(
                    f"Remote connectivity check failed for sample: {sample} - {err[:256]}"
                )
                status = "degraded" if status == "ok" else status
        except Exception as e:
            details.append(
                f"Remote connectivity check error for sample: {sample} - {e}"
            )
            status = "degraded" if status == "ok" else status

    # Check PR API credentials if configured
    if GIT_CONFIG.get("pr_api_base_url") and not GIT_CONFIG.get("pr_api_token"):
        status = "degraded" if status == "ok" else status
        details.append(
            "PR API base URL is set, but GIT_PR_API_TOKEN is missing. PR creation will fail."
        )
        logger.warning(details[-1])
    elif GIT_CONFIG.get("pr_api_token"):
        details.append("Git PR API token found (PR creation enabled).")
    else:
        details.append(
            "PR creation not configured (GIT_PR_API_BASE_URL/TOKEN not set)."
        )

    logger.info(f"Plugin health check: {status}")
    return {"status": status, "details": details}


# --- Plan validation ---
def _validate_refactor_plan(
    refactor_plan: List[Dict[str, Any]],
) -> Tuple[bool, Optional[str]]:
    if not isinstance(refactor_plan, list) or not refactor_plan:
        return False, "refactor_plan must be a non-empty list."
    for i, item in enumerate(refactor_plan):
        if not isinstance(item, dict):
            return False, f"refactor_plan[{i}] must be a dict."
        if not item.get("repo_url"):
            return False, f"refactor_plan[{i}].repo_url is required."
        if not isinstance(item.get("changes", []), list):
            return False, f"refactor_plan[{i}].changes must be a list."
    return True, None


# --- Result helpers ---
def _success_status(status: str) -> bool:
    return status in (
        "SUCCESS_PR_CREATED",
        "SUCCESS_NO_PR",
        "SUCCESS_NO_PR_REQUESTED",
        "DRY_RUN_SUCCESS",
        "SKIPPED",
    )


# --- Per-Repo Processing (for concurrency) ---
async def _process_repo(
    repo_plan: Dict[str, Any],
    git_credentials: Optional[Dict[str, str]],
    refactor_id: str,
    temp_dirs_to_clean: List[str],
    dry_run: bool,
) -> Dict[str, Any]:
    repo_url = repo_plan.get("repo_url")
    changes = repo_plan.get("changes", [])
    commit_message = repo_plan.get(
        "commit_message", f"{GIT_CONFIG['default_author_name']} automated refactor"
    )
    base_branch = repo_plan.get("base_branch", "main")
    refactor_branch = repo_plan.get(
        "refactor_branch", f"{GIT_CONFIG['default_branch_prefix']}{refactor_id}"
    )
    create_pr = repo_plan.get("create_pr", True)

    repo_result: Dict[str, Any] = {
        "repo_url": repo_url,
        "status": "FAILED",
        "reason": "Unknown error",
        "cloned_path": None,
        "commit_sha": None,
        "pushed_branch": None,
        "pull_request_url": None,
        "dry_run_executed": dry_run,
        "error": None,
        "error_type": None,
    }

    repo_temp_dir = None
    try:
        if not repo_url:
            raise ValueError("repo_url missing in refactor plan.")
        if not changes:
            raise ValueError("No changes defined for repository.")

        logger.info(
            f"[{refactor_id}] Processing repository: {_mask_token_in_url(repo_url)}"
        )
        repo_temp_dir = tempfile.mkdtemp(prefix=f"sfe-repo-{uuid.uuid4().hex[:8]}_")
        repo_result["cloned_path"] = repo_temp_dir
        temp_dirs_to_clean.append(repo_temp_dir)

        repo_manager = GitRepoManager(
            repo_url, repo_temp_dir, git_credentials, refactor_id=refactor_id
        )

        # Clone
        try:
            await repo_manager.clone_repo()
        except Exception:
            repo_result["error_type"] = "clone_failed"
            raise

        # Prepare branches (fetch, checkout base, create refactor)
        try:
            await repo_manager.prepare_branch(
                base_branch=base_branch, refactor_branch=refactor_branch
            )
        except Exception:
            repo_result["error_type"] = "checkout_failed"
            raise

        # Apply changes (safely and atomically)
        applied_files = []
        for change in changes:
            filepath = change.get("filepath")
            new_content = change.get("new_content")
            if not filepath or new_content is None:
                logger.warning(
                    f"[{refactor_id}] Skipping malformed change for {repo_url}: {change}"
                )
                continue

            full_filepath = os.path.join(repo_temp_dir, filepath)
            if not _is_safe_path(repo_temp_dir, full_filepath) or _path_has_symlink(
                repo_temp_dir, full_filepath
            ):
                logger.warning(
                    f"[{refactor_id}] Skipping unsafe path (traversal/symlink): {filepath}"
                )
                continue

            os.makedirs(os.path.dirname(full_filepath), exist_ok=True)
            tmp_path = full_filepath + ".sfe.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            os.replace(tmp_path, full_filepath)
            applied_files.append(filepath)
            logger.info(
                f"[{refactor_id}] Applied change to {filepath} in {_mask_token_in_url(repo_url)}"
            )

        if not applied_files:
            repo_result["reason"] = "No valid changes applied to repository."
            repo_result["status"] = "SKIPPED"
            logger.warning(f"[{refactor_id}] {repo_result['reason']}")
            return repo_result

        if dry_run:
            repo_result["reason"] = (
                "Dry run: Changes applied locally, not committed or pushed."
            )
            repo_result["status"] = "DRY_RUN_SUCCESS"
            logger.info(f"[{refactor_id}] {repo_result['reason']}")
            return repo_result

        # Commit
        try:
            commit_sha = await repo_manager.add_and_commit(
                applied_files, commit_message
            )
            repo_result["commit_sha"] = commit_sha
            logger.info(
                f"[{refactor_id}] Committed {commit_sha} in {_mask_token_in_url(repo_url)}"
            )
        except Exception:
            repo_result["error_type"] = "commit_failed"
            raise

        # Push
        try:
            await repo_manager.push_branch(refactor_branch)
            repo_result["pushed_branch"] = refactor_branch
            logger.info(
                f"[{refactor_id}] Pushed {refactor_branch} for {_mask_token_in_url(repo_url)}"
            )
        except Exception:
            repo_result["error_type"] = "push_failed"
            raise

        # Create PR (Optional)
        if create_pr:
            pr_title_prefix = repo_plan.get("pr_title_prefix", "SFE Refactor: ")
            pr_body_suffix = repo_plan.get(
                "pr_body_suffix", "\n\n_Automated by Self-Fixing Engineer._"
            )

            pr_title = f"{pr_title_prefix}{commit_message}"
            pr_body = f"{commit_message}\n{pr_body_suffix}"

            pr_url = await repo_manager.create_pull_request(
                title=pr_title,
                body=pr_body,
                head_branch=refactor_branch,
                base_branch=base_branch,
            )
            if pr_url:
                repo_result["pull_request_url"] = pr_url
                repo_result["reason"] = "Changes committed, pushed, and PR created."
                repo_result["status"] = "SUCCESS_PR_CREATED"
                logger.info(f"[{refactor_id}] PR created: {pr_url}")
            else:
                repo_result["reason"] = (
                    "Changes committed and pushed, but PR creation failed."
                )
                repo_result["status"] = "SUCCESS_NO_PR"
                logger.warning(f"[{refactor_id}] {repo_result['reason']}")
        else:
            repo_result["reason"] = (
                "Changes committed and pushed (PR creation skipped)."
            )
            repo_result["status"] = "SUCCESS_NO_PR_REQUESTED"

        repo_result["success"] = True
        await _audit_event(
            "cross_repo_refactor_repo_completed",
            {
                "refactor_id": refactor_id,
                "repo_url": repo_url,
                "status": repo_result["status"],
                "commit_sha": repo_result["commit_sha"],
                "pr_url": repo_result["pull_request_url"],
            },
        )
        return repo_result

    except Exception as e:
        repo_result["error"] = str(e)
        if repo_result.get("error_type") is None:
            repo_result["error_type"] = "exception"
        repo_result["reason"] = f"Failed to process repository: {e}"
        repo_result["status"] = "FAILED"
        logger.error(
            f"[{refactor_id}] Error processing {_mask_token_in_url(repo_url)}: {e}",
            exc_info=True,
        )
        await _audit_event(
            "cross_repo_refactor_repo_failed",
            {
                "refactor_id": refactor_id,
                "repo_url": repo_url,
                "error": str(e),
                "reason": repo_result["reason"],
                "error_type": repo_result["error_type"],
            },
        )
        return repo_result


# --- PLUGIN FUNCTIONALITY ---
async def perform_cross_repo_refactor(
    refactor_plan: List[
        Dict[str, Any]
    ],  # List of dicts, each specifying repo_url, changes (list of files, content)
    git_credentials: Optional[
        Dict[str, str]
    ] = None,  # {"username": "...", "token": "...", "ssh_key_path": "..."}
    dry_run: bool = False,
    cleanup_on_success: bool = True,
    cleanup_on_failure: bool = True,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Orchestrates and executes a refactoring plan across multiple Git repositories.
    This function handles cloning, applying changes, committing, pushing, and optionally creating Pull Requests.
    """
    refactor_id = f"sfe-cross-repo-{uuid.uuid4().hex[:8]}"
    start_time = time.monotonic()
    results_per_repo: List[Dict[str, Any]] = []
    temp_dirs_to_clean: List[str] = []

    CROSS_REPO_REFACTOR_ATTEMPTS.labels(status="initiated").inc()
    await _audit_event(
        "cross_repo_refactor_initiated",
        {
            "refactor_id": refactor_id,
            "dry_run": dry_run,
            "plan_summary": [r.get("repo_url") for r in refactor_plan],
        },
    )

    ok, plan_err = _validate_refactor_plan(refactor_plan)
    if not ok:
        overall_success = False
        overall_reason = plan_err or "Invalid refactor plan."
        CROSS_REPO_REFACTOR_ERRORS.labels(error_type="invalid_plan").inc()
        await _audit_event(
            "cross_repo_refactor_failed_validation",
            {"refactor_id": refactor_id, "error": overall_reason},
        )
        duration = time.monotonic() - start_time
        try:
            GIT_OPERATION_LATENCY_SECONDS.labels(operation="total_refactor").observe(
                duration
            )
        except Exception:
            pass
        return {
            "success": overall_success,
            "reason": overall_reason,
            "refactor_id": refactor_id,
            "results_per_repo": [],
            "dry_run_executed": dry_run,
            "error": overall_reason,
        }

    # Concurrency control
    sem = asyncio.Semaphore(max(1, GIT_CONFIG["max_concurrency"]))

    async def _worker(plan: Dict[str, Any]) -> Dict[str, Any]:
        async with sem:
            return await _process_repo(
                plan, git_credentials, refactor_id, temp_dirs_to_clean, dry_run
            )

    try:
        # Launch all repo tasks with bounded concurrency
        tasks = [asyncio.create_task(_worker(plan)) for plan in refactor_plan]
        results_per_repo = await asyncio.gather(*tasks, return_exceptions=False)

        # Overall success if all results are successful or skipped/dry-run
        overall_success = all(_success_status(r["status"]) for r in results_per_repo)
        overall_reason = (
            "Cross-repository refactor completed successfully."
            if overall_success
            else "One or more repositories failed."
        )

    except Exception as e:
        overall_reason = f"Overall refactoring failed: {e}"
        overall_success = False
        logger.critical(f"[{refactor_id}] {overall_reason}", exc_info=True)
        CROSS_REPO_REFACTOR_ERRORS.labels(
            error_type="overall_orchestration_failure"
        ).inc()
        await _audit_event(
            "cross_repo_refactor_overall_failed",
            {"refactor_id": refactor_id, "error": overall_reason},
        )
    finally:
        # Final cleanup of temporary directories (or scrub tokens if retaining)
        should_clean = (overall_success and cleanup_on_success) or (
            not overall_success and cleanup_on_failure
        )
        if should_clean:
            for d in temp_dirs_to_clean:
                try:
                    if os.path.exists(d):
                        shutil.rmtree(d)
                        logger.debug(
                            f"[{refactor_id}] Cleaned up temporary repo clone: {d}"
                        )
                except Exception as e:
                    logger.warning(
                        f"[{refactor_id}] Failed to clean up temp dir {d}: {e}"
                    )
        else:
            # Optional: scrub tokenized pushUrl when retaining for debugging
            if GIT_CONFIG["scrub_pushurl_on_retain"]:
                for d in temp_dirs_to_clean:
                    try:
                        if os.path.exists(d) and GITPYTHON_AVAILABLE:
                            r = Repo(d)
                            try:
                                origin = r.remote("origin")
                                # Reset push URL to clean fetch URL (remove any token)
                                fetch_urls = list(origin.urls)
                                clean_url = fetch_urls[0] if fetch_urls else None
                                if clean_url:
                                    await asyncio.to_thread(
                                        origin.set_url, clean_url, True
                                    )
                            except Exception:
                                pass
                    except Exception:
                        pass
            logger.info(
                f"[{refactor_id}] Retaining temporary directories for debugging (cleanup_on_success={cleanup_on_success}, cleanup_on_failure={cleanup_on_failure}, overall_success={overall_success}): {temp_dirs_to_clean}"
            )

        # Metrics for overall outcome (observe on both success and failure)
        duration = time.monotonic() - start_time
        try:
            if overall_success:
                CROSS_REPO_REFACTOR_SUCCESS.inc()
                CROSS_REPO_REFACTOR_ATTEMPTS.labels(status="success").inc()
            else:
                CROSS_REPO_REFACTOR_ATTEMPTS.labels(status="failure").inc()
            GIT_OPERATION_LATENCY_SECONDS.labels(operation="total_refactor").observe(
                duration
            )
        except Exception:
            pass

        await _audit_event(
            "cross_repo_refactor_completed",
            {
                "refactor_id": refactor_id,
                "overall_success": overall_success,
                "reason": overall_reason,
                "duration": duration,
                "results_summary": [
                    {r["repo_url"]: r["status"]} for r in results_per_repo
                ],
            },
        )

    return {
        "success": overall_success,
        "reason": overall_reason,
        "refactor_id": refactor_id,
        "results_per_repo": results_per_repo,
        "dry_run_executed": dry_run,
        "error": None if overall_success else overall_reason,
    }


# --- Auto-registration with core system (e.g., plugin_manager) ---
def register_plugin_entrypoints(register_func: Callable):
    """
    Registers this plugin's cross-repository refactoring function with the SFE core.
    """
    logger.info("Registering CrossRepoRefactorPlugin entrypoints...")
    register_func(
        name="cross_repo_refactor",
        executor_func=perform_cross_repo_refactor,
        capabilities=["cross_repo_refactoring", "multi_repo_sync", "git_automation"],
    )


if __name__ == "__main__":
    # --- Mocking for Standalone Execution ---
    _mock_registered_plugins = {}

    def _mock_register_plugin(
        name: str, executor_func: Callable, capabilities: List[str]
    ):
        _mock_registered_plugins[name] = {
            "executor_func": executor_func,
            "capabilities": capabilities,
        }
        print(
            f"Mocked registration: Registered plugin '{name}' with capabilities: {capabilities}."
        )

    # Register our plugin's entrypoints with the mock registry
    register_plugin_entrypoints(_mock_register_plugin)

    async def main_test_run():
        print("\n--- Cross-Repository Refactor Plugin Standalone Test ---")

        # --- Running Plugin Health Check ---
        print("\n--- Running Plugin Health Check ---")
        health_status = await plugin_health()
        print(f"Health Status: {health_status['status']}")
        for detail in health_status["details"]:
            print(f"  - {detail}")

        if health_status["status"] != "ok":
            print("\n--- Skipping Live Refactor Test: Plugin not healthy. ---")
            print("Please ensure Git CLI and GitPython are installed.")
            print(
                "For live push/PR, set GIT_PR_API_BASE_URL, GIT_PR_API_TOKEN, GIT_AUTHOR_NAME, GIT_AUTHOR_EMAIL, and a real target repo."
            )
            return

        # Environment variables for test. Replace with your actual values for live testing.
        test_repo_url_1 = os.getenv(
            "SFE_TEST_REPO_URL_1", "https://github.com/your-org/sfe-test-repo-1.git"
        )

        test_git_username = os.getenv("GIT_USERNAME", "")
        test_git_token = os.getenv("GIT_TOKEN", "")
        test_credentials = None
        if test_git_username and test_git_token and "your-org" not in test_repo_url_1:
            test_credentials = {"username": test_git_username, "token": test_git_token}
            os.environ["GIT_AUTHOR_NAME"] = GIT_CONFIG["default_author_name"]
            os.environ["GIT_AUTHOR_EMAIL"] = GIT_CONFIG["default_author_email"]
            os.environ["GIT_PR_API_TOKEN"] = test_git_token
            os.environ["GIT_PR_API_BASE_URL"] = GIT_CONFIG["pr_api_base_url"]

        if not test_repo_url_1 or "your-org" in test_repo_url_1 or not test_credentials:
            print(
                "\n--- Skipping Live Refactor Test: Test repo URL or credentials not configured. ---"
            )
            print(
                "Please set SFE_TEST_REPO_URL_1 and GIT_USERNAME/GIT_TOKEN env vars for a live test."
            )
            print(
                "Or set GIT_PR_API_TOKEN, GIT_AUTHOR_NAME, GIT_AUTHOR_EMAIL if you want PR creation."
            )
            return

        # --- Define the Refactor Plan ---
        refactor_plan_example = [
            {
                "repo_url": test_repo_url_1,
                "changes": [
                    {
                        "filepath": "README.md",
                        "new_content": "# SFE Test Repo 1 - Refactored on "
                        + datetime.datetime.now().isoformat()
                        + "\n\nThis README was updated by the SFE CrossRepoRefactorPlugin!",
                    },
                    {
                        "filepath": "src/new_feature.txt",
                        "new_content": "This is a new feature file added by SFE.",
                    },
                ],
                "commit_message": "SFE: Automated refactor for initial setup and new feature file.",
                "base_branch": "main",
                "refactor_branch": f"sfe-refactor-{uuid.uuid4().hex[:6]}",
                "create_pr": True,
            }
        ]

        # --- Execute Dry Run ---
        print("\n--- Performing Dry Run ---")
        dry_run_result = await perform_cross_repo_refactor(
            refactor_plan=refactor_plan_example,
            git_credentials=test_credentials,
            dry_run=True,
            cleanup_on_success=True,
            cleanup_on_failure=True,
        )
        print("\nDry Run Result:")
        print(json.dumps(dry_run_result, indent=2))
        print("-" * 50)

        # --- Execute Live Run ---
        print("\n--- Performing Live Run ---")
        live_run_result = await perform_cross_repo_refactor(
            refactor_plan=refactor_plan_example,
            git_credentials=test_credentials,
            dry_run=False,
            cleanup_on_success=True,
            cleanup_on_failure=False,
        )
        print("\nLive Run Result:")
        print(json.dumps(live_run_result, indent=2))
        if live_run_result["success"]:
            print(
                "\nSuccessfully refactored! Check your repository for new branch and PR:"
            )
            for repo_res in live_run_result["results_per_repo"]:
                print(f"- Repo: {repo_res['repo_url']}")
                print(f"  Branch: {repo_res.get('pushed_branch')}")
                if repo_res.get("pull_request_url"):
                    print(f"  PR: {repo_res['pull_request_url']}")
                print(f"  Commit: {repo_res.get('commit_sha')}")
        else:
            print(
                f"\nLive refactor FAILED: {live_run_result.get('error') or live_run_result.get('reason')}"
            )
            print(
                f"Temporary directories might be available for inspection: {live_run_result['results_per_repo'][0].get('cloned_path') if live_run_result['results_per_repo'] else 'N/A'}"
            )

        print("\n--- Test Run Complete ---")

    # Run the test
    asyncio.run(main_test_run())
