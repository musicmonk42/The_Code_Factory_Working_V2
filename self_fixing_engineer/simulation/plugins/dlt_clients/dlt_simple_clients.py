# simulation/plugins/dlt_clients/dlt_simple_clients.py

import asyncio
import hashlib  # For chain state checksum
import json
import os
import time
import uuid
from collections import OrderedDict
from datetime import datetime
from typing import Any, Dict, Final, List, Optional, Tuple, Union

from pydantic import BaseModel, Field, ValidationError, validator

from .dlt_base import (
    AUDIT,
    PRODUCTION_MODE,
    TRACER,
    BaseDLTClient,
    BaseOffChainClient,
    DLTClientCircuitBreakerError,
    DLTClientConfigurationError,
    DLTClientError,
    DLTClientQueryError,
    DLTClientTransactionError,
    DLTClientValidationError,
    Status,
    StatusCode,
    _base_logger,
    alert_operator,
    async_retry,
    scrub_secrets,
)

# Specific SimpleDLT metrics
try:
    from prometheus_client import Counter, Gauge

    SIMPLE_DLT_METRICS = {
        "validation_failure": Counter(
            "simple_dlt_validation_failure_total",
            "Total number of SimpleDLT validation failures",
            labelnames=["client_type", "operation", "error_code"],
        ),
        "chain_operation": Counter(
            "simple_dlt_chain_operation_total",
            "Total number of SimpleDLT chain state operations",
            labelnames=["client_type", "operation", "status", "error_code"],
        ),
        "chain_size": Gauge(
            "simple_dlt_chain_size",
            "Current number of entries in the SimpleDLT chain",
            labelnames=["client_type"],
        ),
    }
except ImportError:
    _base_logger.warning(
        "Prometheus client not available for SimpleDLT specific metrics."
    )
    SIMPLE_DLT_METRICS = {}  # Dummy if not available


# Configuration schema
class SimpleDLTConfig(BaseModel):
    """Configuration schema for SimpleDLT client."""

    log_format: str = "json"
    temp_file_ttl: float = Field(3600.0, ge=60.0)
    cleanup_interval: float = Field(300.0, ge=30.0)
    chain_state_path: Optional[str] = None  # Path for chain state persistence

    @validator("chain_state_path", always=True)
    def enforce_chain_state_path_in_prod(cls, v):
        if PRODUCTION_MODE and not v:
            raise ValueError(
                "In PRODUCTION_MODE, 'chain_state_path' must be provided for state persistence."
            )
        return v


class SimpleDLTClient(BaseDLTClient):
    """
    Simulates a DLT client with in-memory storage for the ledger.
    Uses a real off-chain client (e.g., S3 or in-memory mock) for payloads.
    Suitable for local development and testing without a real DLT network.
    Supports health checks, chain state persistence, and audit logging.
    """

    client_type: Final[str] = "SimpleDLT"

    def __init__(self, config: Dict[str, Any], off_chain_client: "BaseOffChainClient"):
        try:
            validated_config = SimpleDLTConfig(**config.get("simpledlt", {})).dict(
                exclude_unset=True
            )
        except ValidationError as e:
            _base_logger.critical(
                f"CRITICAL: Invalid SimpleDLT client configuration: {e}."
            )
            try:
                asyncio.get_running_loop().create_task(
                    alert_operator(
                        f"CRITICAL: Invalid SimpleDLT client configuration: {e}.",
                        level="CRITICAL",
                    )
                )
            except RuntimeError:
                pass
            raise DLTClientConfigurationError(
                f"Invalid SimpleDLT client configuration: {e}",
                "SimpleDLT",
                original_exception=e,
            )

        super().__init__({"simpledlt": validated_config}, off_chain_client)

        self.chain: OrderedDict[str, List[Dict[str, Any]]] = OrderedDict()
        self._log_format: str = validated_config["log_format"]
        self._temp_file_ttl: float = validated_config["temp_file_ttl"]
        self._cleanup_interval: float = validated_config["cleanup_interval"]
        self._chain_state_path: Optional[str] = validated_config.get("chain_state_path")
        self._cleanup_task: Optional[asyncio.Task] = None
        self._chain_lock = asyncio.Lock()  # For atomic chain state operations

        # Track files only if you populate it; currently unused but kept for parity
        self._temp_files: Dict[str, float] = {}

        self._format_log(
            "info",
            "SimpleDLTClient initialized",
            {
                "chain_state_path": self._chain_state_path or "N/A",
                "production_mode": PRODUCTION_MODE,
            },
        )

        # Initialize metrics
        if SIMPLE_DLT_METRICS:
            SIMPLE_DLT_METRICS["chain_size"].labels(client_type=self.client_type).set(0)

    async def initialize(self) -> None:
        """
        Initialize the client: load chain state (if configured) and start background tasks.
        """
        # Load chain state if specified, and validate its consistency
        if self._chain_state_path:
            self._format_log(
                "info", f"Attempting to load chain state from {self._chain_state_path}"
            )
            try:
                await self.load_chain(self._chain_state_path)
                self._format_log(
                    "info", "Chain state loaded and validated successfully."
                )
            except Exception as e:
                _base_logger.critical(
                    f"CRITICAL: Failed to load or validate chain state from {self._chain_state_path}: {e}."
                )
                try:
                    asyncio.get_running_loop().create_task(
                        alert_operator(
                            f"CRITICAL: Failed to load or validate SimpleDLT chain state: {e}.",
                            level="CRITICAL",
                        )
                    )
                except RuntimeError:
                    pass
                raise DLTClientConfigurationError(
                    f"Failed to load or validate SimpleDLT chain state: {e}",
                    self.client_type,
                    original_exception=e,
                )
        elif PRODUCTION_MODE:
            # Should have been caught by Pydantic, but keep a safeguard
            _base_logger.critical(
                "CRITICAL: In PRODUCTION_MODE, 'chain_state_path' must be provided for state persistence."
            )
            try:
                asyncio.get_running_loop().create_task(
                    alert_operator(
                        "CRITICAL: SimpleDLT 'chain_state_path' missing in production.",
                        level="CRITICAL",
                    )
                )
            except RuntimeError:
                pass
            raise DLTClientConfigurationError(
                "'chain_state_path' is required in PRODUCTION_MODE.", self.client_type
            )

        # Start background cleanup task (kept for parity; currently no temp files are added)
        try:
            loop = asyncio.get_running_loop()
            self._cleanup_task = loop.create_task(self._cleanup_temp_files_periodic())
        except RuntimeError:
            self._cleanup_task = None

    def _format_log(
        self, level: str, message: str, extra: Dict[str, Any] = None
    ) -> None:
        """
        Formats logs as JSON or text based on configuration.
        """
        if level.lower() == "audit":
            level = "info"
        extra = extra or {}
        extra.update({"client_type": self.client_type})
        if self._log_format == "json":
            log_entry = {
                "timestamp": datetime.utcnow().isoformat(),
                "level": level.upper(),
                "message": message,
                **extra,
            }
            # Serialize log_entry to a JSON string first to avoid unhashable dict issues
            serialized_log = json.dumps(log_entry, sort_keys=True, ensure_ascii=False)
            scrubbed_log = scrub_secrets(serialized_log)
            getattr(self.logger, level.lower())(scrubbed_log)
            # Log to AUDIT manager for critical events
            if level.upper() in ["ERROR", "CRITICAL"]:
                try:
                    serialized_extra = json.dumps(
                        extra, sort_keys=True, ensure_ascii=False
                    )
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            f"simpledlt_client_error.{level.lower()}",
                            message=message,
                            details=scrub_secrets(serialized_extra),
                        )
                    )
                except RuntimeError:
                    pass
        else:
            getattr(self.logger, level.lower())(message, extra=extra)
            if level.upper() in ["ERROR", "CRITICAL"]:
                try:
                    serialized_extra = json.dumps(
                        extra, sort_keys=True, ensure_ascii=False
                    )
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            f"simpledlt_client_error.{level.lower()}",
                            message=message,
                            details=scrub_secrets(serialized_extra),
                        )
                    )
                except RuntimeError:
                    pass

    async def _cleanup_temp_files_periodic(self) -> None:
        """Background coroutine to clean up expired temporary files (if any are tracked)."""
        while True:
            try:
                current_time = time.time()
                for temp_file_path in list(self._temp_files.keys()):
                    creation_time = self._temp_files.get(temp_file_path)
                    if (
                        creation_time
                        and current_time - creation_time > self._temp_file_ttl
                    ):
                        try:
                            os.unlink(temp_file_path)
                            self._temp_files.pop(
                                temp_file_path, None
                            )  # Remove from tracking
                            _base_logger.info(
                                f"Cleaned up expired temporary file: {temp_file_path}"
                            )
                        except OSError as e:
                            _base_logger.warning(
                                f"Failed to clean up temporary file {temp_file_path}: {e}"
                            )
                await asyncio.sleep(self._cleanup_interval)
            except asyncio.CancelledError:
                break  # Exit gracefully if task is cancelled
            except Exception as e:
                self._format_log(
                    "warning",
                    f"Error in temp file cleanup task: {e}",
                    {"error_code": "TEMP_CLEANUP_FAILED"},
                )
                # Do not re-raise, allow cleanup task to continue running

    def _calculate_chain_checksum(self, chain_data: Dict[str, Any]) -> str:
        """Calculates a SHA-256 checksum of the chain state for integrity validation."""
        chain_json = json.dumps(chain_data, sort_keys=True, ensure_ascii=False).encode(
            "utf-8"
        )
        return hashlib.sha256(chain_json).hexdigest()

    async def load_chain(self, path: str, correlation_id: Optional[str] = None) -> None:
        """
        Loads the chain state from a JSON file and validates its integrity.
        """
        async with self._chain_lock:  # Ensure atomic access
            with TRACER.start_as_current_span(
                f"{self.client_type}.load_chain",
                attributes={"path": path, "correlation_id": correlation_id},
            ) as span:
                try:
                    if not os.path.exists(path):
                        if SIMPLE_DLT_METRICS:
                            SIMPLE_DLT_METRICS["chain_operation"].labels(
                                client_type=self.client_type,
                                operation="load_chain",
                                status="error",
                                error_code="FILE_NOT_FOUND",
                            ).inc()
                        self._format_log(
                            "error",
                            f"Chain state file not found: {path}",
                            {
                                "correlation_id": correlation_id,
                                "error_code": "FILE_NOT_FOUND",
                            },
                        )
                        raise FileNotFoundError(f"Chain state file not found: {path}")

                    # Read file off the event loop
                    def _read_file(p: str) -> Dict[str, Any]:
                        with open(p, "r", encoding="utf-8") as f:
                            return json.load(f)

                    chain_data = await asyncio.to_thread(_read_file, path)

                    if not isinstance(chain_data, dict):
                        if SIMPLE_DLT_METRICS:
                            SIMPLE_DLT_METRICS["chain_operation"].labels(
                                client_type=self.client_type,
                                operation="load_chain",
                                status="error",
                                error_code="INVALID_CHAIN_DATA",
                            ).inc()
                        self._format_log(
                            "error",
                            "Chain state must be a dictionary",
                            {
                                "correlation_id": correlation_id,
                                "error_code": "INVALID_CHAIN_DATA",
                            },
                        )
                        raise DLTClientValidationError(
                            "Chain state must be a dictionary",
                            self.client_type,
                            correlation_id=correlation_id,
                        )

                    # Validate chain integrity with a checksum in production
                    if PRODUCTION_MODE:
                        hmac_path = f"{path}.hmac"  # checksum file
                        if not os.path.exists(hmac_path):
                            raise DLTClientValidationError(
                                "Checksum (HMAC) file for chain state not found.",
                                self.client_type,
                                correlation_id=correlation_id,
                            )

                        def _read_hmac(hp: str) -> str:
                            with open(hp, "r", encoding="utf-8") as f:
                                return f.read().strip()

                        expected = await asyncio.to_thread(_read_hmac, hmac_path)
                        calculated = self._calculate_chain_checksum(chain_data)
                        if calculated != expected:
                            raise DLTClientValidationError(
                                "Chain state integrity check failed: checksum mismatch.",
                                self.client_type,
                                correlation_id=correlation_id,
                            )
                        self._format_log(
                            "info",
                            "Chain state integrity validated with checksum.",
                            {"correlation_id": correlation_id},
                        )

                    self.chain = OrderedDict(
                        {k: v for k, v in chain_data.items() if isinstance(v, list)}
                    )

                    if SIMPLE_DLT_METRICS:
                        SIMPLE_DLT_METRICS["chain_operation"].labels(
                            client_type=self.client_type,
                            operation="load_chain",
                            status="success",
                            error_code="NONE",
                        ).inc()
                        SIMPLE_DLT_METRICS["chain_size"].labels(
                            client_type=self.client_type
                        ).set(sum(len(v) for v in self.chain.values()))

                    span.set_status(Status(StatusCode.OK))
                    self._format_log(
                        "audit",
                        f"Chain state loaded from {path}",
                        {"correlation_id": correlation_id, "path": path},
                    )
                    try:
                        asyncio.get_running_loop().create_task(
                            AUDIT.log_event(
                                "simpledlt_chain.loaded",
                                path=path,
                                correlation_id=correlation_id,
                            )
                        )
                    except RuntimeError:
                        pass
                except FileNotFoundError:
                    raise
                except Exception as e:
                    span.set_status(
                        Status(
                            StatusCode.ERROR,
                            description=f"Failed to load chain state: {e}",
                        )
                    )
                    span.record_exception(e)
                    if SIMPLE_DLT_METRICS:
                        SIMPLE_DLT_METRICS["chain_operation"].labels(
                            client_type=self.client_type,
                            operation="load_chain",
                            status="error",
                            error_code="LOAD_CHAIN_FAILED",
                        ).inc()
                    self._format_log(
                        "error",
                        f"Failed to load chain state: {e}",
                        {
                            "correlation_id": correlation_id,
                            "error_code": "LOAD_CHAIN_FAILED",
                        },
                    )
                    try:
                        asyncio.get_running_loop().create_task(
                            AUDIT.log_event(
                                "simpledlt_chain.load_failure",
                                path=path,
                                error_message=str(e),
                                correlation_id=correlation_id,
                            )
                        )
                    except RuntimeError:
                        pass
                    raise DLTClientError(
                        f"Failed to load chain state: {e}",
                        self.client_type,
                        original_exception=e,
                        correlation_id=correlation_id,
                    )

    async def dump_chain(self, path: str, correlation_id: Optional[str] = None) -> None:
        """
        Dumps the chain state to a JSON file (with checksum in production).
        """
        async with self._chain_lock:  # Ensure atomic access
            with TRACER.start_as_current_span(
                f"{self.client_type}.dump_chain",
                attributes={"path": path, "correlation_id": correlation_id},
            ) as span:
                try:
                    self._format_log(
                        "info",
                        f"Starting dump_chain to {path}",
                        {"correlation_id": correlation_id},
                    )

                    # Write file off the event loop
                    def _write_file(p: str, data: Dict[str, Any]) -> None:
                        with open(p, "w", encoding="utf-8") as f:
                            json.dump(data, f, indent=2, ensure_ascii=False)

                    await asyncio.to_thread(_write_file, path, self.chain)

                    if PRODUCTION_MODE:
                        calculated = self._calculate_chain_checksum(self.chain)

                        def _write_hmac(hp: str, val: str) -> None:
                            with open(hp, "w", encoding="utf-8") as f:
                                f.write(val)

                        await asyncio.to_thread(_write_hmac, f"{path}.hmac", calculated)
                        self._format_log(
                            "info",
                            "Chain state checksum calculated and saved.",
                            {
                                "correlation_id": correlation_id,
                                "chain_checksum": calculated,
                            },
                        )

                    if SIMPLE_DLT_METRICS:
                        SIMPLE_DLT_METRICS["chain_operation"].labels(
                            client_type=self.client_type,
                            operation="dump_chain",
                            status="success",
                            error_code="NONE",
                        ).inc()

                    span.set_status(Status(StatusCode.OK))
                    self._format_log(
                        "audit",
                        f"Chain state dumped to {path}",
                        {"correlation_id": correlation_id, "path": path},
                    )
                    try:
                        asyncio.get_running_loop().create_task(
                            AUDIT.log_event(
                                "simpledlt_chain.dumped",
                                path=path,
                                correlation_id=correlation_id,
                            )
                        )
                    except RuntimeError:
                        pass
                except Exception as e:
                    span.set_status(
                        Status(
                            StatusCode.ERROR,
                            description=f"Failed to dump chain state: {e}",
                        )
                    )
                    span.record_exception(e)
                    if SIMPLE_DLT_METRICS:
                        SIMPLE_DLT_METRICS["chain_operation"].labels(
                            client_type=self.client_type,
                            operation="dump_chain",
                            status="error",
                            error_code="DUMP_CHAIN_FAILED",
                        ).inc()
                    self._format_log(
                        "error",
                        f"Failed to dump chain state: {e}",
                        {
                            "correlation_id": correlation_id,
                            "error_code": "DUMP_CHAIN_FAILED",
                        },
                    )
                    try:
                        asyncio.get_running_loop().create_task(
                            AUDIT.log_event(
                                "simpledlt_chain.dump_failure",
                                path=path,
                                error_message=str(e),
                                correlation_id=correlation_id,
                            )
                        )
                    except RuntimeError:
                        pass
                    raise DLTClientError(
                        f"Failed to dump chain state: {e}",
                        self.client_type,
                        original_exception=e,
                        correlation_id=correlation_id,
                    )

    async def _rotate_credentials(self, correlation_id: Optional[str] = None) -> None:
        """
        Rotates credentials for the off-chain client, if supported.
        """
        with TRACER.start_as_current_span(
            f"{self.client_type}.rotate_credentials",
            attributes={"correlation_id": correlation_id},
        ) as span:
            try:
                self._format_log(
                    "info",
                    "Attempting off-chain client credential rotation",
                    {"correlation_id": correlation_id},
                )
                if hasattr(self.off_chain_client, "_rotate_credentials"):
                    await self.off_chain_client._rotate_credentials(
                        correlation_id=correlation_id
                    )
                    self._format_log(
                        "info",
                        "Off-chain client credentials rotated successfully",
                        {"correlation_id": correlation_id},
                    )
                    try:
                        asyncio.get_running_loop().create_task(
                            AUDIT.log_event(
                                "simpledlt_credentials.rotated",
                                off_chain_client_type=self.off_chain_client.client_type,
                                correlation_id=correlation_id,
                            )
                        )
                    except RuntimeError:
                        pass
                else:
                    self._format_log(
                        "warning",
                        "Off-chain client does not support credential rotation",
                        {
                            "correlation_id": correlation_id,
                            "error_code": "ROTATION_NOT_SUPPORTED",
                        },
                    )
                    try:
                        asyncio.get_running_loop().create_task(
                            AUDIT.log_event(
                                "simpledlt_credentials.rotation_skipped",
                                reason="Off-chain client does not support rotation",
                                correlation_id=correlation_id,
                            )
                        )
                    except RuntimeError:
                        pass
            except DLTClientCircuitBreakerError:
                raise
            except Exception as e:
                span.set_status(
                    Status(
                        StatusCode.ERROR,
                        description=f"Off-chain credential rotation error: {e}",
                    )
                )
                span.record_exception(e)
                self._format_log(
                    "error",
                    f"Failed to rotate off-chain client credentials: {e}",
                    {
                        "correlation_id": correlation_id,
                        "error_code": "SIMPLE_DLT_ROTATE_FAILED",
                    },
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "simpledlt_credentials.rotation_failure",
                            error_message=str(e),
                            off_chain_client_type=self.off_chain_client.client_type,
                            correlation_id=correlation_id,
                        )
                    )
                except RuntimeError:
                    pass
                raise DLTClientError(
                    f"Failed to rotate off-chain client credentials: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=correlation_id,
                )

    async def health_check(
        self, correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Checks the health of the SimpleDLT client and its off-chain client.
        """
        with TRACER.start_as_current_span(
            f"{self.client_type}.health_check",
            attributes={"correlation_id": correlation_id},
        ) as span:
            try:
                # Check off-chain client health
                off_chain_result = await self.off_chain_client.health_check(
                    correlation_id=correlation_id
                )
                if not off_chain_result.get("status"):
                    span.set_status(
                        Status(
                            StatusCode.ERROR,
                            description="Off-chain client is unhealthy",
                        )
                    )
                    self._format_log(
                        "error",
                        f"Off-chain client health check failed: {off_chain_result.get('message')}",
                        {
                            "correlation_id": correlation_id,
                            "error_code": "OFFCHAIN_HEALTH_FAILED",
                        },
                    )
                    return {
                        "status": False,
                        "message": f"Off-chain client health check failed: {off_chain_result.get('message')}",
                        "details": off_chain_result.get("details", {}),
                    }

                span.set_status(Status(StatusCode.OK))
                size = sum(len(v) for v in self.chain.values())
                self._format_log(
                    "info",
                    "SimpleDLT client is healthy (in-memory) and off-chain client is accessible",
                    {"correlation_id": correlation_id},
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "simpledlt_health.ok",
                            off_chain_client_status=True,
                            chain_size=size,
                            correlation_id=correlation_id,
                        )
                    )
                except RuntimeError:
                    pass
                return {
                    "status": True,
                    "message": "SimpleDLT client is healthy (in-memory) and off-chain client is accessible",
                    "details": {
                        "off_chain_status": True,
                        "off_chain_details": off_chain_result.get("details", {}),
                        "chain_size": size,
                    },
                }
            except DLTClientCircuitBreakerError:
                raise
            except Exception as e:
                span.set_status(
                    Status(StatusCode.ERROR, description=f"Health check failed: {e}")
                )
                span.record_exception(e)
                self._format_log(
                    "error",
                    f"SimpleDLT health check failed unexpectedly: {e}",
                    {
                        "correlation_id": correlation_id,
                        "error_code": "SIMPLE_DLT_HEALTHCHECK_FAILED",
                    },
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "simpledlt_health.failure",
                            error_message=str(e),
                            correlation_id=correlation_id,
                        )
                    )
                except RuntimeError:
                    pass
                return {
                    "status": False,
                    "message": f"SimpleDLT health check failed unexpectedly: {str(e)}",
                    "details": {"error_code": "SIMPLE_DLT_HEALTHCHECK_FAILED"},
                }

    @async_retry(
        catch_exceptions=(
            DLTClientTransactionError,
            DLTClientQueryError,
            DLTClientCircuitBreakerError,
        )
    )
    async def write_checkpoint(
        self,
        checkpoint_name: str,
        hash: str,
        prev_hash: str,
        metadata: Dict[str, Any],
        payload_blob: bytes,
        correlation_id: Optional[str] = None,
    ) -> Tuple[str, str, int]:
        with TRACER.start_as_current_span(
            f"{self.client_type}.write_checkpoint",
            attributes={
                "checkpoint_name": checkpoint_name,
                "hash": hash,
                "correlation_id": correlation_id,
            },
        ) as span:
            if not checkpoint_name:
                if SIMPLE_DLT_METRICS:
                    SIMPLE_DLT_METRICS["validation_failure"].labels(
                        client_type=self.client_type,
                        operation="write_checkpoint",
                        error_code="EMPTY_CHECKPOINT_NAME",
                    ).inc()
                self._format_log(
                    "error",
                    "Checkpoint name cannot be empty",
                    {
                        "correlation_id": correlation_id,
                        "error_code": "EMPTY_CHECKPOINT_NAME",
                    },
                )
                raise DLTClientValidationError(
                    "Checkpoint name cannot be empty",
                    self.client_type,
                    correlation_id=correlation_id,
                )
            if not hash:
                if SIMPLE_DLT_METRICS:
                    SIMPLE_DLT_METRICS["validation_failure"].labels(
                        client_type=self.client_type,
                        operation="write_checkpoint",
                        error_code="EMPTY_HASH",
                    ).inc()
                self._format_log(
                    "error",
                    "Hash cannot be empty",
                    {"correlation_id": correlation_id, "error_code": "EMPTY_HASH"},
                )
                raise DLTClientValidationError(
                    "Hash cannot be empty",
                    self.client_type,
                    correlation_id=correlation_id,
                )
            if not payload_blob:
                if SIMPLE_DLT_METRICS:
                    SIMPLE_DLT_METRICS["validation_failure"].labels(
                        client_type=self.client_type,
                        operation="write_checkpoint",
                        error_code="EMPTY_PAYLOAD",
                    ).inc()
                self._format_log(
                    "error",
                    "Payload blob cannot be empty",
                    {"correlation_id": correlation_id, "error_code": "EMPTY_PAYLOAD"},
                )
                raise DLTClientValidationError(
                    "Payload blob cannot be empty",
                    self.client_type,
                    correlation_id=correlation_id,
                )

            try:
                self._format_log(
                    "info", "Acquiring chain lock", {"correlation_id": correlation_id}
                )
                async with self._chain_lock:  # Ensure atomic write to chain state
                    self._format_log(
                        "info",
                        "Chain lock acquired",
                        {"correlation_id": correlation_id},
                    )
                    version = len(self.chain.get(checkpoint_name, [])) + 1
                    tx_id = f"{checkpoint_name}-tx{version}-{int(time.time())}-{uuid.uuid4().hex[:8]}"
                    off_chain_id = await self.off_chain_client.save_blob(
                        checkpoint_name, payload_blob, correlation_id=correlation_id
                    )
                    span.set_attribute("off_chain.id", off_chain_id)

                    entry = {
                        "hash": hash,
                        "prev_hash": prev_hash,
                        "metadata": dict(metadata or {}),
                        "off_chain_ref": off_chain_id,
                        "tx_id": tx_id,
                        "version": version,
                        "timestamp": int(time.time() * 1000),
                    }
                    self.chain.setdefault(checkpoint_name, []).append(entry)

                    if self._chain_state_path:
                        await self.dump_chain(
                            self._chain_state_path, correlation_id=correlation_id
                        )  # Dump on every write

                    self._format_log(
                        "info",
                        "Releasing chain lock",
                        {"correlation_id": correlation_id},
                    )

                self._format_log(
                    "info", "Chain lock released", {"correlation_id": correlation_id}
                )
                if SIMPLE_DLT_METRICS:
                    SIMPLE_DLT_METRICS["chain_size"].labels(
                        client_type=self.client_type
                    ).set(sum(len(v) for v in self.chain.values()))

                span.set_status(Status(StatusCode.OK))
                self._format_log(
                    "info",
                    f"SimpleDLT checkpoint written: {checkpoint_name} [tx_id={tx_id}, version={version}]",
                    {
                        "correlation_id": correlation_id,
                        "tx_id": tx_id,
                        "version": version,
                    },
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "simpledlt_checkpoint.written",
                            checkpoint_name=checkpoint_name,
                            tx_id=tx_id,
                            hash=hash,
                            prev_hash=prev_hash,
                            off_chain_id=off_chain_id,
                            version=version,
                            correlation_id=correlation_id,
                        )
                    )
                except RuntimeError:
                    pass
                return tx_id, off_chain_id, version
            except DLTClientCircuitBreakerError:
                raise
            except Exception as e:
                span.set_status(
                    Status(StatusCode.ERROR, description=f"Write checkpoint error: {e}")
                )
                span.record_exception(e)
                if SIMPLE_DLT_METRICS:
                    SIMPLE_DLT_METRICS["chain_operation"].labels(
                        client_type=self.client_type,
                        operation="write_checkpoint",
                        status="error",
                        error_code="SIMPLE_DLT_WRITE_FAILED",
                    ).inc()
                self._format_log(
                    "error",
                    f"Failed to write checkpoint: {e}",
                    {
                        "correlation_id": correlation_id,
                        "error_code": "SIMPLE_DLT_WRITE_FAILED",
                    },
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "simpledlt_checkpoint.write_failure",
                            checkpoint_name=checkpoint_name,
                            error_message=str(e),
                            correlation_id=correlation_id,
                        )
                    )
                except RuntimeError:
                    pass
                raise DLTClientTransactionError(
                    f"Failed to write checkpoint: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=correlation_id,
                )

    @async_retry(catch_exceptions=(DLTClientQueryError, DLTClientCircuitBreakerError))
    async def read_checkpoint(
        self,
        name: str,
        version: Optional[Union[int, str]] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        with TRACER.start_as_current_span(
            f"{self.client_type}.read_checkpoint",
            attributes={
                "checkpoint_name": name,
                "version": version,
                "correlation_id": correlation_id,
            },
        ) as span:
            if not name:
                if SIMPLE_DLT_METRICS:
                    SIMPLE_DLT_METRICS["validation_failure"].labels(
                        client_type=self.client_type,
                        operation="read_checkpoint",
                        error_code="EMPTY_CHECKPOINT_NAME",
                    ).inc()
                self._format_log(
                    "error",
                    "Checkpoint name cannot be empty",
                    {
                        "correlation_id": correlation_id,
                        "error_code": "EMPTY_CHECKPOINT_NAME",
                    },
                )
                raise DLTClientValidationError(
                    "Checkpoint name cannot be empty",
                    self.client_type,
                    correlation_id=correlation_id,
                )

            try:
                async with self._chain_lock:  # Ensure atomic read from chain state
                    chain = self.chain.get(name, [])
                    if not chain:
                        span.set_status(
                            Status(
                                StatusCode.ERROR,
                                description=f"No DLT checkpoint for {name}",
                            )
                        )
                        if SIMPLE_DLT_METRICS:
                            SIMPLE_DLT_METRICS["chain_operation"].labels(
                                client_type=self.client_type,
                                operation="read_checkpoint",
                                status="error",
                                error_code="SIMPLE_DLT_NOT_FOUND",
                            ).inc()
                        self._format_log(
                            "error",
                            f"No DLT checkpoint for {name}",
                            {
                                "correlation_id": correlation_id,
                                "error_code": "SIMPLE_DLT_NOT_FOUND",
                            },
                        )
                        raise FileNotFoundError(f"No DLT checkpoint for {name}")

                    entry = (
                        chain[-1]
                        if (version is None or version == "latest")
                        else next((e for e in chain if e["version"] == version), None)
                    )
                    if not entry:
                        span.set_status(
                            Status(
                                StatusCode.ERROR,
                                description=f"No DLT checkpoint for {name} version {version}",
                            )
                        )
                        if SIMPLE_DLT_METRICS:
                            SIMPLE_DLT_METRICS["chain_operation"].labels(
                                client_type=self.client_type,
                                operation="read_checkpoint",
                                status="error",
                                error_code="SIMPLE_DLT_VERSION_NOT_FOUND",
                            ).inc()
                        self._format_log(
                            "error",
                            f"No DLT checkpoint for {name} version {version}",
                            {
                                "correlation_id": correlation_id,
                                "error_code": "SIMPLE_DLT_VERSION_NOT_FOUND",
                            },
                        )
                        raise FileNotFoundError(
                            f"No DLT checkpoint for {name} version {version}"
                        )

                payload_blob = await self.off_chain_client.get_blob(
                    entry["off_chain_ref"], correlation_id=correlation_id
                )
                resolved_version = entry["version"]
                span.set_status(Status(StatusCode.OK))
                self._format_log(
                    "info",
                    f"SimpleDLT checkpoint read: {name} v{resolved_version} [tx_id={entry['tx_id']}]",
                    {
                        "correlation_id": correlation_id,
                        "tx_id": entry["tx_id"],
                        "version": resolved_version,
                    },
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "simpledlt_checkpoint.read",
                            checkpoint_name=name,
                            version=resolved_version,
                            hash=entry["hash"],
                            correlation_id=correlation_id,
                        )
                    )
                except RuntimeError:
                    pass
                return {
                    "metadata": entry,
                    "payload_blob": payload_blob,
                    "tx_id": entry["tx_id"],
                }
            except FileNotFoundError:
                raise
            except DLTClientCircuitBreakerError:
                raise
            except Exception as e:
                span.set_status(
                    Status(StatusCode.ERROR, description=f"Read checkpoint error: {e}")
                )
                span.record_exception(e)
                if SIMPLE_DLT_METRICS:
                    SIMPLE_DLT_METRICS["chain_operation"].labels(
                        client_type=self.client_type,
                        operation="read_checkpoint",
                        status="error",
                        error_code="SIMPLE_DLT_READ_FAILED",
                    ).inc()
                self._format_log(
                    "error",
                    f"Failed to read checkpoint: {e}",
                    {
                        "correlation_id": correlation_id,
                        "error_code": "SIMPLE_DLT_READ_FAILED",
                    },
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "simpledlt_checkpoint.read_failure",
                            checkpoint_name=name,
                            version=version,
                            error_message=str(e),
                            correlation_id=correlation_id,
                        )
                    )
                except RuntimeError:
                    pass
                raise DLTClientQueryError(
                    f"Failed to read checkpoint: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=correlation_id,
                )

    @async_retry(catch_exceptions=(DLTClientQueryError, DLTClientCircuitBreakerError))
    async def get_version_tx(
        self, name: str, version: int, correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        with TRACER.start_as_current_span(
            f"{self.client_type}.get_version_tx",
            attributes={
                "checkpoint_name": name,
                "version": version,
                "correlation_id": correlation_id,
            },
        ) as span:
            try:
                result = await self.read_checkpoint(
                    name, version, correlation_id=correlation_id
                )
                self._format_log(
                    "info",
                    f"SimpleDLT version transaction retrieved: {name} v{version}",
                    {"correlation_id": correlation_id, "version": version},
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "simpledlt_version_tx.retrieved",
                            checkpoint_name=name,
                            version=version,
                            correlation_id=correlation_id,
                        )
                    )
                except RuntimeError:
                    pass
                return result
            except DLTClientCircuitBreakerError:
                raise
            except Exception as e:
                span.set_status(
                    Status(
                        StatusCode.ERROR,
                        description=f"Get version transaction error: {e}",
                    )
                )
                span.record_exception(e)
                if SIMPLE_DLT_METRICS:
                    SIMPLE_DLT_METRICS["chain_operation"].labels(
                        client_type=self.client_type,
                        operation="get_version_tx",
                        status="error",
                        error_code="SIMPLE_DLT_GET_VERSION_FAILED",
                    ).inc()
                self._format_log(
                    "error",
                    f"Failed to get version transaction: {e}",
                    {
                        "correlation_id": correlation_id,
                        "error_code": "SIMPLE_DLT_GET_VERSION_FAILED",
                    },
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "simpledlt_version_tx.retrieval_failure",
                            checkpoint_name=name,
                            version=version,
                            error_message=str(e),
                            correlation_id=correlation_id,
                        )
                    )
                except RuntimeError:
                    pass
                raise DLTClientQueryError(
                    f"Failed to get version transaction: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=correlation_id,
                )

    @async_retry(
        catch_exceptions=(DLTClientTransactionError, DLTClientCircuitBreakerError)
    )
    async def rollback_checkpoint(
        self, name: str, rollback_hash: str, correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        with TRACER.start_as_current_span(
            f"{self.client_type}.rollback_checkpoint",
            attributes={
                "checkpoint_name": name,
                "rollback_hash": rollback_hash,
                "correlation_id": correlation_id,
            },
        ) as span:
            if not name:
                if SIMPLE_DLT_METRICS:
                    SIMPLE_DLT_METRICS["validation_failure"].labels(
                        client_type=self.client_type,
                        operation="rollback_checkpoint",
                        error_code="EMPTY_CHECKPOINT_NAME",
                    ).inc()
                self._format_log(
                    "error",
                    "Checkpoint name cannot be empty",
                    {
                        "correlation_id": correlation_id,
                        "error_code": "EMPTY_CHECKPOINT_NAME",
                    },
                )
                raise DLTClientValidationError(
                    "Checkpoint name cannot be empty",
                    self.client_type,
                    correlation_id=correlation_id,
                )
            if not rollback_hash:
                if SIMPLE_DLT_METRICS:
                    SIMPLE_DLT_METRICS["validation_failure"].labels(
                        client_type=self.client_type,
                        operation="rollback_checkpoint",
                        error_code="EMPTY_ROLLBACK_HASH",
                    ).inc()
                self._format_log(
                    "error",
                    "Rollback hash cannot be empty",
                    {
                        "correlation_id": correlation_id,
                        "error_code": "EMPTY_ROLLBACK_HASH",
                    },
                )
                raise DLTClientValidationError(
                    "Rollback hash cannot be empty",
                    self.client_type,
                    correlation_id=correlation_id,
                )

            try:
                async with (
                    self._chain_lock
                ):  # Ensure atomic modification of chain state
                    chain = self.chain.get(name, [])
                    entry_to_rollback_to = next(
                        (e for e in chain if e["hash"] == rollback_hash), None
                    )
                    if not entry_to_rollback_to:
                        span.set_status(
                            Status(
                                StatusCode.ERROR,
                                description=f"No DLT checkpoint for {name} with hash {rollback_hash}",
                            )
                        )
                        if SIMPLE_DLT_METRICS:
                            SIMPLE_DLT_METRICS["chain_operation"].labels(
                                client_type=self.client_type,
                                operation="rollback_checkpoint",
                                status="error",
                                error_code="SIMPLE_DLT_NOT_FOUND",
                            ).inc()
                        self._format_log(
                            "error",
                            f"No DLT checkpoint for {name} with hash {rollback_hash}",
                            {
                                "correlation_id": correlation_id,
                                "error_code": "SIMPLE_DLT_NOT_FOUND",
                            },
                        )
                        raise FileNotFoundError(
                            f"No DLT checkpoint for {name} with hash {rollback_hash}"
                        )

                    new_version = len(chain) + 1
                    tx_id = f"{name}-rollback-tx{new_version}-{int(time.time())}-{uuid.uuid4().hex[:8]}"
                    off_chain_id = entry_to_rollback_to["off_chain_ref"]

                    new_entry = {
                        "hash": entry_to_rollback_to["hash"],
                        "prev_hash": entry_to_rollback_to["prev_hash"],
                        "metadata": dict(entry_to_rollback_to["metadata"]),
                        "off_chain_ref": off_chain_id,
                        "tx_id": tx_id,
                        "version": new_version,
                        "timestamp": int(time.time() * 1000),
                        "rollback_from_hash": (
                            chain[-1]["hash"] if chain else None
                        ),  # Record pre-rollback head
                    }
                    chain.append(new_entry)

                    if self._chain_state_path:
                        await self.dump_chain(
                            self._chain_state_path, correlation_id=correlation_id
                        )  # Dump on every rollback

                    if SIMPLE_DLT_METRICS:
                        SIMPLE_DLT_METRICS["chain_size"].labels(
                            client_type=self.client_type
                        ).set(sum(len(v) for v in self.chain.values()))

                span.set_status(Status(StatusCode.OK))
                self._format_log(
                    "info",
                    f"SimpleDLT checkpoint rolled back: {name} -> v{new_version} [rollback_tx={tx_id}]",
                    {
                        "correlation_id": correlation_id,
                        "tx_id": tx_id,
                        "version": new_version,
                    },
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "simpledlt_checkpoint.rolled_back",
                            checkpoint_name=name,
                            rollback_hash=rollback_hash,
                            tx_id=tx_id,
                            new_version=new_version,
                            correlation_id=correlation_id,
                        )
                    )
                except RuntimeError:
                    pass
                return new_entry
            except FileNotFoundError:
                raise
            except DLTClientCircuitBreakerError:
                raise
            except Exception as e:
                span.set_status(
                    Status(
                        StatusCode.ERROR, description=f"Rollback checkpoint error: {e}"
                    )
                )
                span.record_exception(e)
                if SIMPLE_DLT_METRICS:
                    SIMPLE_DLT_METRICS["chain_operation"].labels(
                        client_type=self.client_type,
                        operation="rollback_checkpoint",
                        status="error",
                        error_code="SIMPLE_DLT_ROLLBACK_FAILED",
                    ).inc()
                self._format_log(
                    "error",
                    f"Failed to rollback checkpoint: {e}",
                    {
                        "correlation_id": correlation_id,
                        "error_code": "SIMPLE_DLT_ROLLBACK_FAILED",
                    },
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "simpledlt_checkpoint.rollback_failure",
                            checkpoint_name=name,
                            rollback_hash=rollback_hash,
                            error_message=str(e),
                            correlation_id=correlation_id,
                        )
                    )
                except RuntimeError:
                    pass
                raise DLTClientTransactionError(
                    f"Failed to rollback checkpoint: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=correlation_id,
                )

    async def close(self) -> None:
        """Closes any underlying resources for SimpleDLTClient."""
        try:
            await super().close()
            if self._cleanup_task:
                self._cleanup_task.cancel()
                try:
                    await self._cleanup_task  # Wait for cleanup task to finish
                except asyncio.CancelledError:
                    pass

            if self._chain_state_path:
                try:
                    await self.dump_chain(
                        self._chain_state_path
                    )  # Ensure final state is dumped
                except Exception as e:
                    self._format_log(
                        "warning",
                        f"Failed to persist final chain state on close: {e}",
                        {"client_type": self.client_type},
                    )

            self.chain.clear()
            self._format_log(
                "info", "SimpleDLTClient closed", {"client_type": self.client_type}
            )
        except Exception as e:
            self._format_log(
                "warning",
                f"Failed to close SimpleDLT client cleanly: {e}",
                {
                    "client_type": self.client_type,
                    "error_code": "SIMPLE_DLT_CLOSE_FAILED",
                },
            )
        finally:
            self._format_log(
                "audit",
                "SimpleDLTClient cleanup attempted",
                {"client_type": self.client_type},
            )


# ==============================================================================
# Plugin System Integration
# ==============================================================================

# This section integrates the SimpleDLTClient with the simulation's plugin system.

# Assuming a plugin manager is available for registration
try:
    from simulation.framework.plugin_system import PluginManager, plugin_manager
except ImportError:
    # This allows the file to be imported standalone without the full framework
    plugin_manager = None
    PluginManager = Any


def create_simple_dlt_client(
    config: Dict[str, Any], off_chain_client: "BaseOffChainClient"
) -> SimpleDLTClient:
    """
    Factory function to create an instance of SimpleDLTClient.
    This function is the entry point for the plugin system.
    """
    _base_logger.info("Factory create_simple_dlt_client called for SimpleDLT.")
    if not off_chain_client:
        _base_logger.critical(
            "CRITICAL: An 'off_chain_client' instance must be provided to create a SimpleDLTClient."
        )
        raise DLTClientConfigurationError(
            "An 'off_chain_client' instance must be provided.", "SimpleDLT"
        )
    return SimpleDLTClient(config=config, off_chain_client=off_chain_client)


PLUGIN_MANIFEST: Dict[str, Any] = {
    "plugin_type": "dlt",
    "name": "simpledlt",
    "version": "1.0.0",
    "description": "A simple, in-memory DLT client with optional persistence, designed for local development and testing.",
    "author": "Simulation Engineering Team",
    "factory_function": create_simple_dlt_client,
    "config_schema": SimpleDLTConfig,
}


def register_plugin_entrypoints(manager: "PluginManager"):
    """
    Registers the SimpleDLT plugin manifest with the provided plugin manager.
    """
    _base_logger.info(f"Registering DLT plugin: {PLUGIN_MANIFEST.get('name')}")
    manager.register_plugin(PLUGIN_MANIFEST)


# Automatic registration if a global plugin_manager instance is detected
if plugin_manager:
    register_plugin_entrypoints(plugin_manager)
