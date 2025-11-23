"""
Policy Manager (production-ready)

- Encrypted (Fernet) on-disk policy store (atomic writes).
- Optional async Postgres sync via SQLAlchemy ORM (single-row, id="current").
- Pydantic v2 models with strict validation and version compatibility check.
- Prometheus metrics and OpenTelemetry spans (no provider re-init here).
- Concurrency-safe with an asyncio.Lock for file/DB operations.

Assumptions / Integration Notes
- ArbiterConfig provides at least: POLICY_CONFIG_FILE_PATH (absolute path),
  VALID_DOMAIN_PATTERN (regex string), and optionally DATABASE_URL and
  ENCRYPTION_KEY (pydantic.SecretStr). If ENCRYPTION_KEY is unavailable,
  module fails fast on load/save. You can also set ENCRYPTION_KEY_FILE env var
  to point to a base64 Fernet key file. Optional OLD_ENCRYPTION_KEY env var is
  allowed to decrypt legacy files during rotation.
- OpenTelemetry should be initialized by the host app. We only acquire a tracer
  via trace.get_tracer(__name__) and guard spans accordingly.
- If a project-wide SQLAlchemy Base exists at arbiter.agent_state.Base, it will
  be used. Otherwise, we create a local declarative base for the ORM model.

Public API
- class PolicyManager(config: ArbiterConfig)
  - await load_policies() -> None
  - await save_policies() -> None
  - await load_from_database() -> None
  - await save_to_database() -> None
  - get_policies() -> PolicyConfig | None
  - set_policies(cfg: PolicyConfig) -> None
  - await rotate_encryption_key(new_key_b64: str) -> None
  - async def health_check() -> dict
  - async def check_permission(role: str, permission: str) -> bool  (lazy-uses PermissionManager if available)

Metrics
- policy_ops_total{operation}
- policy_errors_total{operation}
- policy_file_read_latency_seconds{operation}
- policy_file_write_latency_seconds{operation}
- policy_db_upsert_latency_seconds{operation}

"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import errno
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiofiles
from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from prometheus_client import Counter, Histogram

# OpenTelemetry: only acquire tracer; assume provider is set elsewhere
try:
    from opentelemetry import trace
except Exception:  # pragma: no cover - tracing optional

    class _NoOpTracer:  # minimal no-op
        def start_as_current_span(self, *_args, **_kwargs):
            class _Span:
                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

                def set_attribute(self, *_a, **_k):
                    return None

                def record_exception(self, *_a, **_k):
                    return None

            return _Span()

    class _NoOp:
        def get_tracer(self, *_a, **_k):
            return _NoOpTracer()

    trace = _NoOp()  # type: ignore

tracer = trace.get_tracer(__name__)

# --- Logging ---
logger = logging.getLogger(__name__)
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(_h)
logger.setLevel(logging.INFO)

# --- Metric helpers ---


def _sanitize_label(value: Any) -> str:
    s = str(value)
    return re.sub(r"[^a-zA-Z0-9_-]", "_", s)[:50]


policy_ops_total = Counter("policy_ops_total", "Total policy operations", ["operation"])
policy_errors_total = Counter(
    "policy_errors_total", "Total policy errors", ["operation"]
)
policy_file_read_latency = Histogram(
    "policy_file_read_latency_seconds",
    "Latency of policy file reads",
    ["operation"],
    buckets=(0.001, 0.01, 0.1, 0.5, 1, 2, 5),
)
policy_file_write_latency = Histogram(
    "policy_file_write_latency_seconds",
    "Latency of policy file writes",
    ["operation"],
    buckets=(0.001, 0.01, 0.1, 0.5, 1, 2, 5),
)
policy_db_upsert_latency = Histogram(
    "policy_db_upsert_latency_seconds",
    "Latency of policy DB upserts",
    ["operation"],
    buckets=(0.001, 0.01, 0.1, 0.5, 1, 2, 5),
)

# --- Config import ---
try:
    from .config import ArbiterConfig  # same package (arbiter.policy)
except Exception as e:  # pragma: no cover
    logger.error("Failed to import ArbiterConfig: %s", e)
    raise

# --- SQLAlchemy ORM (optional DB sync) ---
try:
    # Prefer project-wide Base if available to share metadata
    from arbiter.agent_state import Base as _ProjectBase  # type: ignore
except Exception:  # pragma: no cover - fallback to local Base
    _ProjectBase = None

try:
    from sqlalchemy.orm import declarative_base, Mapped, mapped_column
    from sqlalchemy import String

    try:
        from sqlalchemy import JSON as _JSONType  # cross-dialect JSON
    except Exception:  # very old SQLAlchemy
        from sqlalchemy.dialects.postgresql import JSONB as _JSONType  # type: ignore
except Exception:  # pragma: no cover - DB is optional; only needed if DATABASE_URL set
    declarative_base = None  # type: ignore
    Mapped = None  # type: ignore
    mapped_column = None  # type: ignore
    _JSONType = None  # type: ignore

if _ProjectBase is not None:
    Base = _ProjectBase
elif declarative_base is not None:
    Base = declarative_base()
else:
    Base = None  # type: ignore

# Fixed syntax: Define PolicyORM conditionally based on Base availability
if Base is not None:

    class PolicyORM(Base):  # type: ignore
        """Single-row policy storage for DB sync (id='current')."""

        __tablename__ = "policies"
        id: Mapped[str] = mapped_column(String, primary_key=True)
        data: Mapped[dict] = mapped_column(_JSONType)  # JSON across dialects

else:

    class PolicyORM:  # type: ignore
        """Dummy PolicyORM when SQLAlchemy is not available."""

        __tablename__ = "policies"
        id = None
        data = None


# --- Pydantic policy models ---


class DomainRule(BaseModel):
    active: bool = True
    allow: bool = False
    required_roles: Optional[List[str]] = None
    reason: str = Field(default="")
    control_tag: str = Field(default="POL-DOMAIN")
    max_size_kb: Optional[int] = None
    sensitive_keys: Optional[List[str]] = None
    trust_score_threshold: Optional[float] = None
    temporal_window_seconds: Optional[int] = None

    @field_validator("trust_score_threshold")
    @classmethod
    def _trust_score_range(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return v
        if not (0.0 <= v <= 1.0):
            raise ValueError("trust_score_threshold must be in [0.0, 1.0]")
        return v


class UserRule(BaseModel):
    active: bool = True
    allow: bool = False
    restricted_domains: List[str] = Field(default_factory=list)
    reason: str = Field(default="")
    control_tag: str = Field(default="POL-USER")


class LLMRules(BaseModel):
    enabled: bool = False
    threshold: float = 0.75
    prompt_template: str = Field(default="")
    control_tag: str = Field(default="POL-LLM")
    valid_responses: List[str] = Field(
        default_factory=lambda: ["YES", "NO"]
    )  # align with engine expectations

    @field_validator("threshold")
    @classmethod
    def _threshold_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("LLM threshold must be in [0.0, 1.0]")
        return v


class TrustRules(BaseModel):
    enabled: bool = False
    threshold: float = 0.5
    reason: str = Field(default="")
    temporal_window_seconds: int = 86400
    control_tag: str = Field(default="POL-TRUST")

    @field_validator("threshold")
    @classmethod
    def _trust_threshold(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("Trust threshold must be in [0.0, 1.0]")
        return v


class PolicyConfig(BaseModel):
    file_metadata: Dict[str, str]
    global_settings: Dict[str, Any]
    domain_rules: Dict[str, DomainRule]
    user_rules: Dict[str, UserRule]
    llm_rules: LLMRules
    trust_rules: TrustRules

    @model_validator(mode="after")
    def _check_versions_and_globals(self) -> "PolicyConfig":
        # Version compatibility
        ver = self.file_metadata.get("version", "0.0.0")
        compat = self.file_metadata.get("compatibility", ver)
        try:
            from packaging import version as _v

            if _v.parse(ver) < _v.parse(compat):
                raise ValueError(f"Policy version {ver} < compatibility {compat}")
        except Exception as e:
            raise ValueError(
                f"Invalid version/compatibility in file_metadata: {e}"
            ) from e
        # Minimal global_settings sanity
        if not isinstance(self.global_settings, dict):
            raise ValueError("global_settings must be a dict")
        return self

    @staticmethod
    def default() -> "PolicyConfig":
        return PolicyConfig(
            file_metadata={
                "version": "1.0.0",
                "compatibility": "1.0.0",
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            global_settings={"auto_learn_default": False},
            domain_rules={},
            user_rules={},
            llm_rules=LLMRules(),
            trust_rules=TrustRules(),
        )


# --- Policy Manager ---


class PolicyManager:
    def __init__(self, config: ArbiterConfig):
        if not isinstance(config, ArbiterConfig):  # type: ignore
            raise TypeError("config must be an instance of ArbiterConfig")
        self.config = config

        path = getattr(config, "POLICY_CONFIG_FILE_PATH", None)
        if not path:
            raise ValueError(
                "ArbiterConfig.POLICY_CONFIG_FILE_PATH is required and must be absolute"
            )
        self.policy_file = Path(path)
        if not self.policy_file.is_absolute():
            raise ValueError("POLICY_CONFIG_FILE_PATH must be an absolute path")
        self.policy_file.parent.mkdir(parents=True, exist_ok=True)

        self._lock = asyncio.Lock()
        self._fernet = self._build_fernet_from_config()
        self.policies: Optional[PolicyConfig] = None

        # Optional DB client (lazy, only if DATABASE_URL present and SQLAlchemy available)
        self.db_client: Any = None
        db_url = getattr(config, "DATABASE_URL", None)
        if db_url and Base is not None:
            try:
                # project-specific client should provide async get_session()
                from arbiter.postgres_client import PostgresClient  # type: ignore

                self.db_client = PostgresClient(db_url)
            except Exception as e:  # pragma: no cover - optional
                logger.warning("DB client unavailable (%s); proceeding file-only.", e)
                self.db_client = None
        elif db_url and Base is None:
            logger.warning(
                "DATABASE_URL is set but SQLAlchemy is unavailable; proceeding file-only."
            )

    # --- Encryption key plumbing ---

    def _build_fernet_from_config(self) -> Fernet:
        # Primary source: ArbiterConfig.ENCRYPTION_KEY (SecretStr)
        key = None
        try:
            if getattr(self.config, "ENCRYPTION_KEY", None):
                key = self.config.ENCRYPTION_KEY.get_secret_value()  # type: ignore[attr-defined]
        except Exception:
            key = None
        # Secondary source: ENCRYPTION_KEY_FILE env var
        if not key:
            key_file = os.getenv("ENCRYPTION_KEY_FILE")
            if key_file and Path(key_file).exists():
                key = Path(key_file).read_text(encoding="utf-8").strip()
        if not key:
            raise ValueError(
                "ENCRYPTION_KEY not configured. Set ENCRYPTION_KEY or ENCRYPTION_KEY_FILE."
            )
        if len(key.encode("utf-8")) != 44:
            raise ValueError(
                "ENCRYPTION_KEY must be a 32-byte base64-encoded (44 char) Fernet key"
            )
        try:
            return Fernet(key.encode("utf-8"))
        except Exception as e:
            raise ValueError(f"Invalid ENCRYPTION_KEY: {e}") from e

    def _get_old_fernet(self) -> Optional[Fernet]:
        old = os.getenv("OLD_ENCRYPTION_KEY")
        if not old:
            return None
        try:
            if len(old.encode("utf-8")) == 44:
                return Fernet(old.encode("utf-8"))
        except Exception:
            return None
        return None

    # --- File I/O helpers (encrypted JSON) ---

    async def _read_encrypted_json(self) -> Dict[str, Any]:
        start = asyncio.get_running_loop().time()
        with tracer.start_as_current_span(
            "policy_file_read", attributes={"path": str(self.policy_file)}
        ) as span:
            if not self.policy_file.exists():
                raise FileNotFoundError(str(self.policy_file))
            async with aiofiles.open(self.policy_file, "r", encoding="utf-8") as f:
                ciphertext = await f.read()
            try:
                try:
                    plaintext = self._fernet.decrypt(ciphertext.encode("utf-8"))
                except InvalidToken:
                    # try legacy key
                    old = self._get_old_fernet()
                    if not old:
                        raise
                    plaintext = old.decrypt(ciphertext.encode("utf-8"))
                data = json.loads(plaintext.decode("utf-8"))
                span.set_attribute("status", "ok")
                return data
            except InvalidToken as e:
                span.record_exception(e)
                policy_errors_total.labels(
                    operation=_sanitize_label("file_decrypt")
                ).inc()
                raise ValueError("Failed to decrypt policy file (invalid key)") from e
            except json.JSONDecodeError as e:
                span.record_exception(e)
                policy_errors_total.labels(
                    operation=_sanitize_label("file_json_decode")
                ).inc()
                raise ValueError("Policy file is not valid JSON after decrypt") from e
            finally:
                policy_file_read_latency.labels(
                    operation=_sanitize_label("read")
                ).observe(asyncio.get_running_loop().time() - start)

    async def _write_encrypted_json(self, payload: Dict[str, Any]) -> None:
        start = asyncio.get_running_loop().time()
        with tracer.start_as_current_span(
            "policy_file_write", attributes={"path": str(self.policy_file)}
        ) as span:
            # Atomic write via tmp file + replace
            tmp = self.policy_file.with_suffix(self.policy_file.suffix + ".tmp")
            plaintext = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            ciphertext = self._fernet.encrypt(plaintext).decode("utf-8")
            async with aiofiles.open(tmp, "w", encoding="utf-8") as f:
                await f.write(ciphertext)
            # Replace atomically (best-effort across platforms)
            try:
                tmp.replace(self.policy_file)
            except OSError as e:
                if getattr(e, "errno", None) == errno.EXDEV:  # Cross-device link
                    # Copy then delete instead
                    shutil.copy2(tmp, self.policy_file)
                    tmp.unlink()
                else:
                    raise
            span.set_attribute("status", "ok")
            policy_file_write_latency.labels(
                operation=_sanitize_label("write")
            ).observe(asyncio.get_running_loop().time() - start)

    # --- Public methods ---

    async def load_policies(self) -> None:
        """Load policies from encrypted file. If missing, create default and persist."""
        async with self._lock:
            with tracer.start_as_current_span("load_policies") as span:
                try:
                    data = await self._read_encrypted_json()
                except FileNotFoundError:
                    logger.warning(
                        "Policy file not found. Bootstrapping default policies at %s",
                        self.policy_file,
                    )
                    self.policies = PolicyConfig.default()
                    await self._write_encrypted_json(self.policies.model_dump())
                    policy_ops_total.labels(
                        operation=_sanitize_label("init_default")
                    ).inc()
                    if self.db_client:
                        await self.save_to_database()
                    return
                except Exception as e:
                    span.record_exception(e)
                    policy_errors_total.labels(
                        operation=_sanitize_label("load_file")
                    ).inc()
                    raise
                try:
                    self.policies = PolicyConfig(**data)
                except ValidationError as e:
                    span.record_exception(e)
                    policy_errors_total.labels(
                        operation=_sanitize_label("pydantic_validate")
                    ).inc()
                    raise ValueError(f"Policy validation failed: {e}") from e
                policy_ops_total.labels(operation=_sanitize_label("load")).inc()
                if self.db_client:
                    try:
                        await self.save_to_database()
                    except Exception as e:  # Do not fail load if DB is down
                        logger.error("DB sync after load failed: %s", e)
                        policy_errors_total.labels(
                            operation=_sanitize_label("db_sync_post_load")
                        ).inc()

    async def save_policies(self) -> None:
        """Persist current policies to encrypted file and upsert DB (if configured)."""
        async with self._lock:
            with tracer.start_as_current_span("save_policies"):
                if self.policies is None:
                    raise ValueError(
                        "No policies in memory. Call set_policies() or load_policies() first."
                    )
                payload = self.policies.model_dump()
                await self._write_encrypted_json(payload)
                policy_ops_total.labels(operation=_sanitize_label("save")).inc()
                if self.db_client:
                    await self.save_to_database()

    async def load_from_database(self) -> None:
        """Load policies from DB into memory. Falls back to file if nothing stored."""
        if not self.db_client or Base is None:
            raise RuntimeError(
                "Database client not configured or SQLAlchemy unavailable."
            )
        async with self._lock:
            with tracer.start_as_current_span("load_policies_db") as span:
                try:
                    async with self.db_client.get_session() as session:  # type: ignore[attr-defined]
                        row = await session.get(PolicyORM, "current")
                        if row and getattr(row, "data", None):
                            self.policies = PolicyConfig(**row.data)
                            policy_ops_total.labels(
                                operation=_sanitize_label("load_db")
                            ).inc()
                        else:
                            logger.warning(
                                "No DB policy row found; falling back to file."
                            )
                            await self.load_policies()
                except Exception as e:
                    span.record_exception(e)
                    policy_errors_total.labels(
                        operation=_sanitize_label("load_db")
                    ).inc()
                    raise ValueError(f"Database load failed: {e}") from e

    async def save_to_database(self) -> None:
        """Upsert the in-memory policies to DB (id='current')."""
        if not self.db_client or Base is None:
            return  # silently skip if DB isn't configured
        start = asyncio.get_running_loop().time()
        with tracer.start_as_current_span("save_policies_db") as span:
            try:
                payload = self.policies.model_dump() if self.policies else None
                if payload is None:
                    raise ValueError("No policies in memory to save.")
                async with self.db_client.get_session() as session:  # type: ignore[attr-defined]
                    row = await session.get(PolicyORM, "current")
                    if row is None:
                        row = PolicyORM(id="current", data=payload)  # type: ignore[call-arg]
                        session.add(row)
                    else:
                        row.data = payload
                    await session.commit()
                policy_ops_total.labels(operation=_sanitize_label("save_db")).inc()
            except Exception as e:
                span.record_exception(e)
                policy_errors_total.labels(operation=_sanitize_label("save_db")).inc()
                raise ValueError(f"Database save failed: {e}") from e
            finally:
                policy_db_upsert_latency.labels(
                    operation=_sanitize_label("upsert")
                ).observe(asyncio.get_running_loop().time() - start)

    def get_policies(self) -> Optional[PolicyConfig]:
        return self.policies

    def set_policies(self, cfg: PolicyConfig) -> None:
        if not isinstance(cfg, PolicyConfig):
            raise TypeError("cfg must be a PolicyConfig")
        self.policies = cfg

    async def rotate_encryption_key(self, new_key_b64: str) -> None:
        """Re-encrypts on-disk file with a new Fernet key. In-memory policies remain.
        Accepts a base64 (44-char) Fernet key.
        """
        if len(new_key_b64.encode("utf-8")) != 44:
            raise ValueError(
                "new_key_b64 must be a 32-byte base64-encoded Fernet key (44 chars)"
            )
        async with self._lock:
            with tracer.start_as_current_span("rotate_key"):
                # ensure we can load current payload
                if self.policies is None:
                    await self.load_policies()
                payload = self.policies.model_dump() if self.policies else None
                if payload is None:
                    raise ValueError("No policies available to re-encrypt.")
                # temporarily swap fernet and write
                old = self._fernet
                try:
                    self._fernet = Fernet(new_key_b64.encode("utf-8"))
                    await self._write_encrypted_json(payload)
                    policy_ops_total.labels(
                        operation=_sanitize_label("rotate_key")
                    ).inc()
                finally:
                    self._fernet = old

    async def health_check(self) -> Dict[str, Any]:
        """Shallow health: can we read + decrypt + validate the file quickly?"""
        with tracer.start_as_current_span("policy_health") as span:
            try:
                data = await self._read_encrypted_json()
                _ = PolicyConfig(**data)
                status = {
                    "status": "healthy",
                    "path": str(self.policy_file),
                    "version": data.get("file_metadata", {}).get("version"),
                    "updated_at": datetime.fromtimestamp(
                        self.policy_file.stat().st_mtime, tz=timezone.utc
                    ).isoformat(),
                }
                policy_ops_total.labels(operation=_sanitize_label("health")).inc()
                return status
            except Exception as e:
                span.record_exception(e)
                policy_errors_total.labels(operation=_sanitize_label("health")).inc()
                return {
                    "status": "unhealthy",
                    "path": str(self.policy_file),
                    "error": str(e),
                }

    async def check_permission(self, role: str, permission: str) -> bool:
        """Delegates to PermissionManager if available; otherwise raises.
        Implemented lazy to avoid hard dependency.
        """
        try:
            from arbiter.permission_manager import PermissionManager  # type: ignore
        except Exception as e:
            raise RuntimeError(
                "PermissionManager not available in this environment"
            ) from e
        mgr = PermissionManager(self.config)
        # Assume a sync or async check; support both
        res = mgr.check(role, permission)
        if asyncio.iscoroutine(res):
            return await res
        return bool(res)
