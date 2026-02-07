# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# self_healing_import_fixer/analyzer/core_policy.py

import asyncio
import hashlib
import hmac
import json
import logging
import os
import random
import re
import threading
import time
import warnings
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Set

import boto3
from botocore.exceptions import ClientError
from pydantic import BaseModel, Field, ValidationError, validator

# Make Redis optional
try:
    import redis.asyncio as redis

    REDIS_AVAILABLE = True
except ImportError:
    redis = None
    REDIS_AVAILABLE = False

# --- Global Production Mode Flag (from analyzer.py) ---
PRODUCTION_MODE = os.getenv("PRODUCTION_MODE", "false").lower() == "true"
VERSION = "1.0.0"

logger = logging.getLogger(__name__)


# --- Custom Exception for critical errors (from analyzer.py) ---
class AnalyzerCriticalError(RuntimeError):
    """
    Custom exception for critical errors that should halt execution and alert ops.
    """

    def __init__(self, message: str, alert_level: str = "CRITICAL"):
        super().__init__(f"[CRITICAL][POLICY] {message}")
        try:
            # We need to import alert_operator here in case the top-level import fails
            from .core_utils import alert_operator

            alert_operator(message, alert_level)
        except Exception:
            pass


# --- Centralized Utilities (replacing placeholders) ---
try:
    from .core_secrets import SECRETS_MANAGER
    from .core_utils import alert_operator, scrub_secrets
except ImportError as e:
    logger.critical(
        f"CRITICAL: Missing core dependency for core_policy: {e}. Aborting startup."
    )
    try:
        from .core_utils import alert_operator

        alert_operator(
            f"CRITICAL: Policy management missing core dependency: {e}. Aborting.",
            level="CRITICAL",
        )
    except Exception:
        pass
    raise RuntimeError("[CRITICAL][POLICY] Missing core dependency") from e


# --- Event-loop bridging ---
def _run_async(coro):
    """
    Helper to run an async coroutine from a synchronous context.
    Safely bridges sync/async environments by checking for a running loop.
    """
    try:
        loop = asyncio.get_running_loop()
        if loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(coro, loop)
            return fut.result()
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        # No running event loop
        import sys

        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


# --- Policy File Integrity (HMAC Key Management) ---
POLICY_HMAC_KEY_ENV = "ANALYZER_POLICY_HMAC_KEY"
_policy_hmac_key: Optional[bytes] = None


def _get_policy_hmac_key() -> bytes:
    global _policy_hmac_key
    if _policy_hmac_key is None:
        key_str = SECRETS_MANAGER.get_secret(POLICY_HMAC_KEY_ENV)

        if not key_str:
            if PRODUCTION_MODE:
                raise AnalyzerCriticalError(
                    "Policy HMAC key not found in production mode"
                )
            else:
                # Generate a random key for non-production
                _policy_hmac_key = os.urandom(32)
                warnings.warn(
                    "ANALYZER_POLICY_HMAC_KEY_ENV not set. Generated a random key for policy file signing. THIS IS INSECURE FOR PRODUCTION."
                )
                alert_operator(
                    "WARNING: Policy HMAC key not set. Using insecure random key. IMMEDIATE ACTION REQUIRED.",
                    level="WARNING",
                )
        else:
            _policy_hmac_key = key_str.encode("utf-8")
    return _policy_hmac_key


# Ensure key is loaded at startup
try:
    _ = _get_policy_hmac_key()
except Exception as e:
    raise RuntimeError(f"[CRITICAL][POLICY] Failed to get policy HMAC key: {e}") from e

# --- Caching: Redis Client Initialization ---
REDIS_CLIENT = None
REDIS_INITIALIZED = False


def _get_redis_client():
    """Lazy Redis initialization."""
    global REDIS_CLIENT, REDIS_INITIALIZED

    if not REDIS_AVAILABLE:
        logger.debug("Redis not available - caching disabled")
        return None

    if REDIS_INITIALIZED:
        return REDIS_CLIENT

    try:
        REDIS_CLIENT = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            db=0,
            decode_responses=True,
        )
        REDIS_INITIALIZED = True
    except Exception as e:
        logger.warning(f"Redis unavailable: {e}")
        REDIS_INITIALIZED = True
        REDIS_CLIENT = None

    return REDIS_CLIENT


# --- Pydantic Models for Policy Definition ---
class PolicyRule(BaseModel):
    id: str = Field(..., description="Unique identifier for the policy rule.")
    name: str = Field(..., description="Human-readable name of the rule.")
    description: Optional[str] = Field(
        None, description="Detailed description of the rule."
    )
    type: Literal[
        "import_restriction",
        "dependency_limit",
        "cycle_prevention",
        "naming_convention",
    ] = Field(..., description="Type of policy rule.")
    severity: Literal["low", "medium", "high", "critical"] = Field(
        "medium", description="Severity of violation."
    )
    allow_imports: Optional[List[str]] = Field(
        None, description="List of allowed top-level imports (regex or module name)."
    )
    deny_imports: Optional[List[str]] = Field(
        None, description="List of denied top-level imports (regex or module name)."
    )
    target_modules: Optional[List[str]] = Field(
        None, description="Modules to which this rule applies (regex or module name)."
    )
    max_dependencies: Optional[int] = Field(
        None, ge=0, description="Maximum allowed direct dependencies for a module."
    )
    pattern: Optional[str] = Field(
        None, description="Regex pattern for naming convention."
    )
    apply_to: Optional[Literal["modules", "functions", "classes"]] = Field(
        None, description="Apply naming convention to modules, functions, or classes."
    )

    @validator(
        "allow_imports",
        "deny_imports",
        "target_modules",
        "pattern",
        pre=True,
        each_item=False,
    )
    def validate_regex_patterns(cls, v):
        if v is not None:
            items = v if isinstance(v, list) else [v]
            for pattern_str in items:
                try:
                    re.compile(pattern_str)
                except re.error as e:
                    raise ValueError(f"Invalid regex pattern '{pattern_str}': {e}")
        return v


class ArchitecturalPolicy(BaseModel):
    policies: List[PolicyRule] = Field(..., description="List of policy rules.")
    version: str = Field("1.0", description="Version of the policy set.")
    description: Optional[str] = Field(
        None, description="Overall description of the policy set."
    )
    signature: Optional[str] = Field(
        None, description="HMAC signature of the policy content."
    )

    @validator("policies", pre=True)
    def ensure_policy_rules(cls, v):
        if isinstance(v, list):
            return [
                PolicyRule(**rule) if not isinstance(rule, PolicyRule) else rule
                for rule in v
            ]
        return v


class PolicyViolation(BaseModel):
    rule_id: str
    rule_name: str
    severity: str
    message: str
    offending_item: str
    details: Dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


# --- Atomic Policy Loading and Watching ---
_policy_lock = threading.RLock()
_last_good_policy: Optional[ArchitecturalPolicy] = None
_compiled_patterns: Dict[str, Any] = {}
_watcher_thread: Optional[threading.Thread] = None
_watcher_stop_event = threading.Event()


def _validate_and_apply(
    policy_data_bytes: bytes, expect_hmac: bool
) -> ArchitecturalPolicy:
    """Performs schema and HMAC validation, raising an error on failure."""
    try:
        policy_data = json.loads(policy_data_bytes.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise AnalyzerCriticalError(f"Policy file is not valid JSON: {e}")

    # Integrity check
    stored_signature = policy_data.pop("signature", None)
    policy_content_str = json.dumps(
        policy_data, sort_keys=True, ensure_ascii=False
    ).encode("utf-8")
    calculated_signature = hmac.new(
        _get_policy_hmac_key(), policy_content_str, hashlib.sha256
    ).hexdigest()

    # Always enforce integrity check, regardless of PRODUCTION_MODE, if expect_hmac is True
    if expect_hmac and (
        stored_signature is None or stored_signature != calculated_signature
    ):
        reason = "no_signature" if stored_signature is None else "signature_mismatch"
        from .core_audit import audit_logger

        audit_logger.log_event(
            "policy_load_failure",
            reason=reason,
            expected_signature=stored_signature,
            calculated_signature=calculated_signature,
        )
        raise AnalyzerCriticalError(
            "Policy integrity check failed (signature mismatch)."
        )

    if policy_data.get("version") != "1.0":
        raise AnalyzerCriticalError(
            f"Unsupported policy version: {policy_data.get('version')}."
        )

    policy_data["signature"] = stored_signature
    try:
        policies = ArchitecturalPolicy(**policy_data)
    except ValidationError as e:
        raise AnalyzerCriticalError(f"Policy file validation failed: {e}")

    policies.policies = [
        PolicyRule(**rule) if not isinstance(rule, PolicyRule) else rule
        for rule in policies.policies
    ]

    compiled_patterns = {}
    for rule in policies.policies:
        assert isinstance(rule, PolicyRule), f"Expected PolicyRule, got {type(rule)}"
        try:
            # Anchor regex patterns to the start of the string to ensure matching as expected
            def anchor(pat):
                return pat if pat.startswith("^") else f"^{pat}"

            compiled_patterns[rule.id] = {
                "target_modules": [
                    re.compile(anchor(p)) for p in (rule.target_modules or [])
                ],
                "allow_imports": [
                    re.compile(anchor(p)) for p in (rule.allow_imports or [])
                ],
                "deny_imports": [
                    re.compile(anchor(p)) for p in (rule.deny_imports or [])
                ],
                "pattern": re.compile(rule.pattern) if rule.pattern else None,
            }
        except re.error as e:
            raise AnalyzerCriticalError(f"Invalid regex in rule {rule.id}: {e}")

    with _policy_lock:
        global _last_good_policy, _compiled_patterns
        _last_good_policy = policies
        _compiled_patterns = compiled_patterns

    from .core_audit import audit_logger

    audit_logger.log_event("policy_integrity_verified", signature=stored_signature)
    logger.info(
        f"Policy file integrity verified successfully (version: {policies.version})."
    )
    return policies


def _watch_loop(policy_file_path: str, poll_seconds: float = 10.0):
    last_mtime = None
    backoff = poll_seconds
    while not _watcher_stop_event.is_set():
        try:
            if not os.path.exists(policy_file_path):
                warnings.warn(
                    f"Policy file not found during hot-reload watch: {policy_file_path}"
                )
                time.sleep(backoff + random.uniform(0.0, 0.5))
                continue

            mtime = os.path.getmtime(policy_file_path)
            if last_mtime is None:
                last_mtime = mtime
            elif mtime > last_mtime:
                logger.info(
                    f"Detected change in policy file {policy_file_path}, attempting reload."
                )
                with open(policy_file_path, "rb") as f:
                    buf = f.read()

                _validate_and_apply(buf, expect_hmac=PRODUCTION_MODE)
                logger.info("Hot-reload successful.")
                last_mtime = mtime
                backoff = poll_seconds
        except Exception as e:
            logger.error(
                "Policy reload failed; keeping last-good policy.", exc_info=True
            )
            alert_operator(
                f"ERROR: Policy hot-reload failed: {e}. Keeping last valid version.",
                level="ERROR",
            )
            backoff = min(backoff * 1.5, 30.0)

        _watcher_stop_event.wait(backoff + random.uniform(0.0, 0.5))


def start_policy_watcher(policy_file_path: str, poll_interval: float = 10.0):
    global _watcher_thread
    if _watcher_thread is None:
        _watcher_thread = threading.Thread(
            target=_watch_loop, args=(policy_file_path, poll_interval), daemon=True
        )
        _watcher_thread.start()
        logger.info("Policy file hot-reload watcher started.")


def stop_policy_watcher():
    global _watcher_thread
    if _watcher_thread and _watcher_thread.is_alive():
        _watcher_stop_event.set()
        _watcher_thread.join()
        _watcher_thread = None
        logger.info("Policy file hot-reload watcher stopped.")


# --- Policy Manager ---
class PolicyManager:
    def __init__(
        self,
        policy_file_path: str,
        enable_hot_reload: bool = True,
        reload_poll_interval: float = 10.0,
    ):
        from .core_audit import audit_logger

        self.policy_file_path = policy_file_path
        self._redis_cache_key = (
            f"policy:{hashlib.sha256(policy_file_path.encode('utf-8')).hexdigest()}"
        )
        self._load_policies_sync()
        logger.info(
            f"PolicyManager initialized with policies from: {self.policy_file_path}"
        )
        audit_logger.log_event(
            "policy_manager_init",
            policy_file=self.policy_file_path,
            policy_version=_last_good_policy.version if _last_good_policy else "N/A",
        )
        if enable_hot_reload and not PRODUCTION_MODE:
            start_policy_watcher(policy_file_path, reload_poll_interval)

    def shutdown(self):
        stop_policy_watcher()

    def _load_policies_sync(self):
        _run_async(self._load_policies_async())

    async def _load_policies_async(self) -> None:
        from .core_audit import audit_logger

        policy_data_bytes = None
        s3_bucket = os.getenv("POLICY_S3_BUCKET")
        cache_key = self._redis_cache_key

        redis_client = _get_redis_client()
        if redis_client:
            try:
                cached_policy_bytes = await redis_client.get(cache_key)
                if cached_policy_bytes:
                    policy_data_bytes = cached_policy_bytes
                    logger.info("Policies loaded from Redis cache successfully.")
                    audit_logger.log_event(
                        "policy_load_cache_hit", policy_file=self.policy_file_path
                    )
            except Exception as e:
                logger.warning(
                    f"Failed to load policies from cache: {e}. Falling back to source."
                )

        if policy_data_bytes is None:
            try:
                if s3_bucket and PRODUCTION_MODE:
                    s3 = boto3.client("s3")
                    response = await asyncio.to_thread(
                        s3.get_object, Bucket=s3_bucket, Key=self.policy_file_path
                    )
                    policy_data_bytes = response["Body"].read()
                    logger.info(f"Policies loaded from S3 bucket '{s3_bucket}'.")
                else:
                    with open(self.policy_file_path, "rb") as f:
                        policy_data_bytes = f.read()
                    logger.info(
                        f"Policies loaded from local file '{self.policy_file_path}'."
                    )
            except FileNotFoundError:
                raise AnalyzerCriticalError(
                    f"Policy file not found at {self.policy_file_path}."
                )
            except ClientError as e:
                raise AnalyzerCriticalError(
                    f"S3 ClientError for policy file '{self.policy_file_path}': {e.response['Error']['Message']}."
                )
            except Exception as e:
                raise AnalyzerCriticalError(f"Unexpected error loading policies: {e}.")

        _validate_and_apply(policy_data_bytes, expect_hmac=True)

        if redis_client and policy_data_bytes is not None:
            try:
                await redis_client.setex(cache_key, 86400, policy_data_bytes)
                audit_logger.log_event(
                    "policy_cache_set", policy_file=self.policy_file_path
                )
            except Exception as e:
                logger.warning(f"Failed to cache validated policies: {e}.")

        logger.info("Policies loaded and validated successfully.")

    def check_architectural_policies(
        self,
        code_graph: Dict[str, Set[str]],
        module_paths: Dict[str, str],
        detected_cycles: List[List[str]],
        dead_nodes: Set[str],
    ) -> List[PolicyViolation]:
        with _policy_lock:
            if not _last_good_policy:
                raise AnalyzerCriticalError(
                    "Policy enforcement attempted but no policies loaded."
                )

            violations: List[PolicyViolation] = []
            logger.info("Starting architectural policy enforcement.")
            from .core_audit import audit_logger

            audit_logger.log_event(
                "policy_enforcement_start", policy_version=_last_good_policy.version
            )

            for rule in _last_good_policy.policies:
                try:
                    logger.debug(
                        f"Enforcing rule: {rule.name} (ID: {rule.id}, Type: {rule.type})"
                    )
                    if rule.type == "import_restriction":
                        violations.extend(
                            self._enforce_import_restriction(rule, code_graph)
                        )
                    elif rule.type == "dependency_limit":
                        violations.extend(
                            self._enforce_dependency_limit(rule, code_graph)
                        )
                    elif rule.type == "cycle_prevention":
                        violations.extend(
                            self._enforce_cycle_prevention(rule, detected_cycles)
                        )
                    elif rule.type == "naming_convention":
                        violations.extend(
                            self._enforce_naming_convention(rule, module_paths)
                        )
                except Exception as e:
                    logger.error(
                        f"Failed to enforce rule {rule.id}: {e}", exc_info=True
                    )
                    violations.append(
                        PolicyViolation(
                            rule_id=rule.id,
                            rule_name=rule.name,
                            severity="critical",
                            message=f"Enforcement error: {e}",
                            offending_item="Policy Manager",
                            details={
                                "error_type": type(e).__name__,
                                "error_message": str(e),
                            },
                        )
                    )
                    alert_operator(
                        f"CRITICAL: Failed to enforce policy rule {rule.id} due to an internal error: {e}. Check logs.",
                        level="CRITICAL",
                    )

            if violations:
                logger.warning(
                    f"Policy enforcement complete. Found {len(violations)} violations."
                )
                for violation in violations:
                    violation_extra = {
                        k: v
                        for k, v in violation.model_dump().items()
                        if k
                        not in (
                            "message",
                            "levelname",
                            "levelno",
                            "name",
                            "msg",
                            "args",
                        )
                    }
                    audit_logger.log_event(
                        "policy_violation_detailed",
                        **scrub_secrets(violation.model_dump()),
                    )
                    if violation.severity in ["high", "critical"]:
                        logger.error(
                            f"CRITICAL POLICY VIOLATION: {violation.rule_name} - {violation.message}",
                            extra=violation_extra,
                        )
                        alert_operator(
                            f"CRITICAL POLICY VIOLATION: {violation.rule_name} (Severity: {violation.severity}) in {violation.offending_item}: {violation.message}",
                            level="CRITICAL",
                        )
                    else:
                        logger.warning(
                            f"POLICY VIOLATION: {violation.rule_name} - {violation.message}",
                            extra=violation_extra,
                        )
            else:
                logger.info("Policy enforcement complete. No violations detected.")
                audit_logger.log_event(
                    "policy_enforcement_complete", status="no_violations"
                )

            return violations

    def _get_compiled_patterns(self, rule_id: str) -> Dict[str, Any]:
        with _policy_lock:
            return _compiled_patterns.get(rule_id, {})

    def _enforce_import_restriction(
        self, rule: PolicyRule, code_graph: Dict[str, Set[str]]
    ) -> List[PolicyViolation]:
        violations = []
        rule_patterns = self._get_compiled_patterns(rule.id)
        target_modules_regex = rule_patterns.get("target_modules", [])
        deny_imports_regex = rule_patterns.get("deny_imports", [])
        allow_imports_regex = rule_patterns.get("allow_imports", [])

        if not target_modules_regex:
            target_modules_regex = [re.compile("^.*")]

        for importer_module, imported_modules in code_graph.items():
            if not any(
                pattern.match(importer_module) for pattern in target_modules_regex
            ):
                continue

            for imported_module in imported_modules:
                is_denied = any(
                    pattern.match(imported_module) for pattern in deny_imports_regex
                )
                is_allowed = allow_imports_regex and any(
                    pattern.match(imported_module) for pattern in allow_imports_regex
                )
                if is_denied and not is_allowed:
                    violations.append(
                        PolicyViolation(
                            rule_id=rule.id,
                            rule_name=rule.name,
                            severity=rule.severity,
                            message=f"Module '{importer_module}' imports denied module '{imported_module}'.",
                            offending_item=f"{importer_module} -> {imported_module}",
                            details={
                                "importer": importer_module,
                                "imported": imported_module,
                                "reason": "denied_import",
                            },
                        )
                    )
        return violations

    def _enforce_dependency_limit(
        self, rule: PolicyRule, code_graph: Dict[str, Set[str]]
    ) -> List[PolicyViolation]:
        violations = []
        if rule.max_dependencies is None:
            logger.warning(
                f"Rule {rule.id}: max_dependencies is not set for dependency_limit rule. Skipping."
            )
            return []

        rule_patterns = self._get_compiled_patterns(rule.id)
        target_modules_regex = rule_patterns.get("target_modules", [])
        if not target_modules_regex:
            target_modules_regex = [re.compile("^.*")]

        for module, dependencies in code_graph.items():
            if not any(pattern.match(module) for pattern in target_modules_regex):
                continue

            num_dependencies = len(dependencies)
            if num_dependencies > rule.max_dependencies:
                violations.append(
                    PolicyViolation(
                        rule_id=rule.id,
                        rule_name=rule.name,
                        severity=rule.severity,
                        message=f"Module '{module}' has {num_dependencies} dependencies, exceeding the limit of {rule.max_dependencies}.",
                        offending_item=module,
                        details={
                            "module": module,
                            "num_dependencies": num_dependencies,
                            "limit": rule.max_dependencies,
                        },
                    )
                )
        return violations

    def _enforce_cycle_prevention(
        self, rule: PolicyRule, detected_cycles: List[List[str]]
    ) -> List[PolicyViolation]:
        violations = []
        if detected_cycles:
            for cycle in detected_cycles:
                violations.append(
                    PolicyViolation(
                        rule_id=rule.id,
                        rule_name=rule.name,
                        severity=rule.severity,
                        message=f"Import cycle detected: {' -> '.join(cycle)}.",
                        offending_item=" -> ".join(cycle),
                        details={"cycle_path": cycle},
                    )
                )
        return violations

    def _enforce_naming_convention(
        self, rule: PolicyRule, module_paths: Dict[str, str]
    ) -> List[PolicyViolation]:
        violations = []
        if not rule.pattern or not rule.apply_to:
            logger.warning(
                f"Rule {rule.id}: Naming convention rule missing pattern or apply_to. Skipping."
            )
            return []

        rule_patterns = self._get_compiled_patterns(rule.id)
        naming_pattern = rule_patterns.get("pattern")
        if not naming_pattern:
            return []

        target_modules_regex = rule_patterns.get("target_modules", [])
        if not target_modules_regex:
            target_modules_regex = [re.compile("^.*")]

        for module_name, file_path in module_paths.items():
            if not any(pattern.match(module_name) for pattern in target_modules_regex):
                continue

            if rule.apply_to == "modules":
                if not naming_pattern.match(module_name):
                    violations.append(
                        PolicyViolation(
                            rule_id=rule.id,
                            rule_name=rule.name,
                            severity=rule.severity,
                            message=f"Module '{module_name}' violates naming convention '{rule.pattern}'.",
                            offending_item=module_name,
                            details={
                                "item_type": "module",
                                "name": module_name,
                                "pattern": rule.pattern,
                            },
                        )
                    )
            elif rule.apply_to in ["functions", "classes"]:
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        file_content = f.read()
                        if len(file_content) > 1024 * 1024:
                            logger.warning(
                                f"Skipping naming convention check for {file_path}: file too large (>1MB)."
                            )
                            continue
                        tree = None
                        try:
                            import ast

                            tree = ast.parse(file_content, filename=file_path)
                        except Exception as e:
                            logger.warning(
                                f"Syntax error or AST parsing error in {file_path}: {e}. Skipping naming convention check."
                            )
                            continue

                    for node in ast.walk(tree):
                        if (
                            rule.apply_to == "functions"
                            and getattr(node, "name", None)
                            and isinstance(node, ast.FunctionDef)
                        ) or (
                            rule.apply_to == "classes"
                            and getattr(node, "name", None)
                            and isinstance(node, ast.ClassDef)
                        ):
                            if not naming_pattern.match(node.name):
                                violations.append(
                                    PolicyViolation(
                                        rule_id=rule.id,
                                        rule_name=rule.name,
                                        severity=rule.severity,
                                        message=f"{rule.apply_to.capitalize()} '{node.name}' in module '{module_name}' violates naming convention '{rule.pattern}'.",
                                        offending_item=f"{module_name}.{node.name}",
                                        details={
                                            "item_type": rule.apply_to,
                                            "name": node.name,
                                            "module": module_name,
                                            "pattern": rule.pattern,
                                        },
                                    )
                                )
                except SyntaxError as e:
                    logger.warning(
                        f"Syntax error in {file_path} for naming convention check: {e}. Skipping AST parsing."
                    )
                except Exception as e:
                    logger.error(
                        f"Error parsing {file_path} for naming convention check: {e}",
                        exc_info=True,
                    )
        return violations
