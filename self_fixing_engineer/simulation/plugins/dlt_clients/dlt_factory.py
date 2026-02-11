# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Production-hardened DLT Factory.

Key improvements:
- Replaces sys.exit calls with typed exceptions (DLTClientConfigurationError, DLTClientError).
- Guards Prometheus metrics when client library is unavailable.
- Maps unsupported log level "audit" to "info" to avoid logger attribute errors.
- Schedules AUDIT events safely even when no event loop is running.
- Robust atexit cleanup that works whether or not an event loop is available.
- Preserves static, approved registries for DLT and off-chain clients (safer in production).
"""

import asyncio
import atexit
import json
import os
import time
from datetime import datetime
from typing import Any, Dict, Final, List, Literal, Optional, Type

from pydantic import BaseModel, Field, ValidationError, field_validator

from .dlt_base import (
    AUDIT,
    PRODUCTION_MODE,
    BaseDLTClient,
    BaseOffChainClient,
    DLTClientConfigurationError,
    DLTClientError,
    DLTClientLoggerAdapter,
    _base_logger,
    alert_operator,
    scrub_secrets,
)

# Import specific DLT client implementations conditionally
from .dlt_simple_clients import SimpleDLTClient

try:
    from .dlt_fabric_clients import FabricClientWrapper
except ImportError as e:
    _base_logger.warning(
        f"FabricClientWrapper import failed: {e}. Fabric support disabled."
    )
try:
    from .dlt_evm_clients import EthereumClientWrapper
except ImportError as e:
    _base_logger.warning(
        f"EthereumClientWrapper import failed: {e}. EVM support disabled."
    )
try:
    from .dlt_corda_clients import CordaClientWrapper
except ImportError as e:
    _base_logger.warning(
        f"CordaClientWrapper import failed: {e}. Corda support disabled."
    )
try:
    from .dlt_quorum_clients import QuorumClientWrapper
except ImportError as e:
    _base_logger.warning(
        f"QuorumClientWrapper import failed: {e}. Quorum support disabled."
    )

# Import specific off-chain client implementations conditionally
from .dlt_offchain_clients import InMemoryOffChainClient

try:
    from .dlt_offchain_clients import S3OffChainClient
except ImportError as e:
    _base_logger.warning(f"S3OffChainClient import failed: {e}. S3 support disabled.")
try:
    from .dlt_offchain_clients import GcsOffChainClient
except ImportError as e:
    _base_logger.warning(f"GcsOffChainClient import failed: {e}. GCS support disabled.")
try:
    from .dlt_offchain_clients import AzureBlobOffChainClient
except ImportError as e:
    _base_logger.warning(
        f"AzureBlobOffChainClient import failed: {e}. Azure Blob support disabled."
    )
try:
    from .dlt_offchain_clients import IPFSClient
except ImportError as e:
    _base_logger.warning(f"IPFSClient import failed: {e}. IPFS support disabled.")

# Optional Factory-specific Prometheus metrics
try:
    from prometheus_client import Counter, Histogram

    FACTORY_METRICS = {
        "init_total": Counter(
            "dlt_factory_init_total",
            "Total number of DLT factory client initializations",
            labelnames=["client_type", "operation", "status"],
        ),
        "init_latency": Histogram(
            "dlt_factory_init_latency_seconds",
            "Latency of DLT factory client initializations in seconds",
            labelnames=["client_type", "operation"],
        ),
        "secrets_unavailable_total": Counter(
            "dlt_factory_secrets_unavailable_total",
            "Total number of times a secrets backend was requested but unavailable during factory init",
            labelnames=["client_type", "backend"],
        ),
        "client_creation_failure": Counter(
            "dlt_factory_client_creation_failure_total",
            "Total failures creating DLT or off-chain clients from factory",
            labelnames=["factory_client_type", "requested_client_type", "error_type"],
        ),
    }
except Exception:
    _base_logger.warning(
        "Prometheus client not available for Factory-specific metrics."
    )
    FACTORY_METRICS = {}

# --- DLT Client Registry (Static - No Dynamic Re-registration in Prod) ---
DLT_CLIENT_REGISTRY: Dict[str, Type[BaseDLTClient]] = {
    "simple": SimpleDLTClient,
}
if "FabricClientWrapper" in globals():
    DLT_CLIENT_REGISTRY["fabric"] = FabricClientWrapper
if "EthereumClientWrapper" in globals():
    DLT_CLIENT_REGISTRY["evm"] = EthereumClientWrapper
if "CordaClientWrapper" in globals():
    DLT_CLIENT_REGISTRY["corda"] = CordaClientWrapper
if "QuorumClientWrapper" in globals():
    DLT_CLIENT_REGISTRY["quorum"] = QuorumClientWrapper

# --- Off-Chain Client Registry (Static - No Dynamic Re-registration in Prod) ---
OFF_CHAIN_CLIENT_REGISTRY: Dict[str, Type[BaseOffChainClient]] = {
    "in_memory": InMemoryOffChainClient,
}
if "S3OffChainClient" in globals():
    OFF_CHAIN_CLIENT_REGISTRY["s3"] = S3OffChainClient
if "GcsOffChainClient" in globals():
    OFF_CHAIN_CLIENT_REGISTRY["gcs"] = GcsOffChainClient
if "AzureBlobOffChainClient" in globals():
    OFF_CHAIN_CLIENT_REGISTRY["azure_blob"] = AzureBlobOffChainClient
if "IPFSClient" in globals():
    OFF_CHAIN_CLIENT_REGISTRY["ipfs"] = IPFSClient


# Configuration schema
class FactoryConfig(BaseModel):
    """
    Configuration schema for DLT factory.
    Validates inputs for compliance with regulatory requirements (e.g., SOX, SOC2).
    """

    off_chain_storage_type: Literal["s3", "gcs", "azure_blob", "ipfs", "in_memory"] = (
        "in_memory"
    )
    secrets_providers: List[Literal["aws", "azure", "gcp"]] = Field(
        default_factory=list
    )
    secrets_provider_config: Optional[Dict[str, Any]] = None
    log_format: str = Field("json", pattern=r"^(json|text)$")
    close_timeout: float = Field(5.0, ge=0.1)
    temp_file_ttl: float = Field(3600.0, ge=60.0)  # 1 hour
    config_version: str = "1.0"
    use_multiprocessing: bool = False  # For temp file management, if needed

    @classmethod
    @field_validator("secrets_providers")
    def validate_secrets_providers_list(cls, v, values):
        if v:
            for provider in v:
                if provider not in ("aws", "azure", "gcp"):
                    raise ValueError(
                        f"Invalid secrets_provider: {provider}. Must be one of 'aws', 'azure', 'gcp'."
                    )
                if provider == "azure" and not (
                    values.get("secrets_provider_config") or {}
                ).get("vault_url"):
                    raise ValueError(
                        "secrets_provider_config.vault_url required for Azure Key Vault."
                    )
                if provider == "gcp" and not (
                    values.get("secrets_provider_config") or {}
                ).get("project_id"):
                    raise ValueError(
                        "secrets_provider_config.project_id required for GCP Secret Manager."
                    )
        return v

    @classmethod
    @field_validator("config_version")
    def validate_config_version_value(cls, v):
        if v != "1.0":
            raise ValueError("Only config_version '1.0' is supported")
        return v

    @classmethod
    @field_validator("off_chain_storage_type")
    def validate_off_chain_storage_type_in_prod(cls, v):
        if PRODUCTION_MODE and v == "in_memory":
            raise ValueError(
                "In PRODUCTION_MODE, 'in_memory' off-chain storage type is forbidden. Use a persistent off-chain client."
            )
        return v


class DLTFactory:
    """
    Factory class for initializing DLT and off-chain clients.
    Ensures strict registry-based instantiation, audit logging, and metrics for regulatory compliance (e.g., SOX, SOC2).
    All initialization failures are logged and instrumented for forensic traceability.
    Designed for testability via dependency injection and mockable registries, enabling robust unit testing for SRE teams.
    """

    client_type: Final[str] = "DLTFactory"
    _temp_files: Dict[str, float] = {}  # Use dict for tracking temp files
    _logger = DLTClientLoggerAdapter(_base_logger, {"client_type": client_type})

    # Multiprocessing manager (optional)
    _manager = None

    @classmethod
    def _metrics_inc(cls, name: str, labels: Dict[str, str]) -> None:
        if FACTORY_METRICS and name in FACTORY_METRICS:
            try:
                FACTORY_METRICS[name].labels(**labels).inc()
            except Exception:
                pass

    @classmethod
    def _metrics_observe(cls, name: str, labels: Dict[str, str], value: float) -> None:
        if FACTORY_METRICS and name in FACTORY_METRICS:
            try:
                FACTORY_METRICS[name].labels(**labels).observe(value)
            except Exception:
                pass

    @classmethod
    def _initialize_temp_files_manager(cls, use_multiprocessing: bool) -> None:
        """
        Initialize a multiprocessing Manager-backed dict for temp files when requested.
        """
        if use_multiprocessing and cls._manager is None:
            from multiprocessing import Manager

            cls._manager = Manager()
            cls._temp_files = cls._manager.dict()  # shared state
            cls._logger.info(
                "Initialized multiprocessing.Manager for temporary file tracking."
            )

    @classmethod
    async def cleanup_temp_files(cls) -> None:
        """
        Cleans up temporary files created by off-chain clients.
        Invoked on process exit or client initialization failure.
        """
        files_to_clean = list(cls._temp_files.keys())  # iterate over copy
        for temp_file in files_to_clean:
            try:
                await asyncio.to_thread(os.unlink, temp_file)
                cls._temp_files.pop(temp_file, None)  # remove from tracking
                cls._logger.info(f"Factory cleaned up temporary file: {temp_file}")
            except OSError as e:
                cls._logger.warning(
                    f"Factory failed to clean up temporary file {temp_file}: {e}"
                )

        if cls._manager:
            # Shut down multiprocessing manager
            try:
                cls._manager.shutdown()
            except Exception:
                pass
            finally:
                cls._manager = None
                cls._logger.info("Multiprocessing Manager shut down.")

    @classmethod
    def _schedule_audit(cls, event_type: str, **kwargs) -> None:
        """
        Schedule an audit event safely regardless of event loop availability.
        """
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(AUDIT.log_event(event_type, **kwargs))
        except RuntimeError:
            # No event loop; best-effort synchronous log message
            _base_logger.error(f"[AUDIT-DELAYED] {event_type}: {kwargs}")

    @classmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        """
        Returns the FactoryConfig schema for documentation and autogeneration.
        """
        # Pydantic v2 compatibility
        schema = (
            FactoryConfig.model_json_schema()
            if hasattr(FactoryConfig, "model_json_schema")
            else FactoryConfig.schema()
        )
        cls._format_log(
            "audit",
            "Retrieved FactoryConfig schema",
            {"operation": "get_config_schema", "schema_title": schema.get("title")},
        )
        return schema

    @classmethod
    async def get_dlt_client(
        cls,
        dlt_type: Literal["fabric", "evm", "corda", "simple", "quorum"],
        config: Dict[str, Any],
        off_chain_client_instance: Optional[BaseOffChainClient] = None,
        correlation_id: Optional[str] = None,
    ) -> BaseDLTClient:
        """
        Factory method to get an initialized DLT client instance.
        Handles initialization of the off-chain storage client if not provided.
        All operations are audit-logged and instrumented with Prometheus metrics for regulatory compliance.
        """
        start_time = time.time()
        operation = "get_dlt_client"
        try:
            # Validate factory config first
            validated_factory_config = FactoryConfig(**config).dict(exclude_unset=True)
            cls._initialize_temp_files_manager(
                validated_factory_config.get("use_multiprocessing", False)
            )

            # Verify DLT client type
            client_class = DLT_CLIENT_REGISTRY.get(dlt_type)
            if not client_class:
                cls._metrics_inc(
                    "client_creation_failure",
                    {
                        "factory_client_type": cls.client_type,
                        "requested_client_type": dlt_type,
                        "error_type": "unsupported_dlt_type",
                    },
                )
                msg = f"Unsupported DLT client type requested: {dlt_type}. Available types: {list(DLT_CLIENT_REGISTRY.keys())}"
                cls._format_log(
                    "critical",
                    msg,
                    {"correlation_id": correlation_id, "dlt_type": dlt_type},
                )
                await alert_operator(
                    f"CRITICAL: Unsupported DLT client type '{dlt_type}' requested.",
                    level="CRITICAL",
                )
                raise DLTClientConfigurationError(msg, cls.client_type)

            # Initialize off-chain client if not provided
            current_off_chain_client = off_chain_client_instance
            if current_off_chain_client is None:
                off_chain_type = validated_factory_config["off_chain_storage_type"]
                off_chain_client_class = OFF_CHAIN_CLIENT_REGISTRY.get(off_chain_type)
                if not off_chain_client_class:
                    cls._metrics_inc(
                        "client_creation_failure",
                        {
                            "factory_client_type": cls.client_type,
                            "requested_client_type": off_chain_type,
                            "error_type": "unsupported_offchain_type",
                        },
                    )
                    msg = f"Unsupported off-chain storage type requested: {off_chain_type}. Available types: {list(OFF_CHAIN_CLIENT_REGISTRY.keys())}"
                    cls._format_log(
                        "critical",
                        msg,
                        {
                            "correlation_id": correlation_id,
                            "off_chain_type": off_chain_type,
                        },
                    )
                    await alert_operator(
                        f"CRITICAL: Unsupported off-chain storage type '{off_chain_type}'.",
                        level="CRITICAL",
                    )
                    raise DLTClientConfigurationError(msg, cls.client_type)

                # Prepare off-chain client config (inject factory-level settings)
                off_chain_config_for_client = config.get(off_chain_type, {}).copy()
                off_chain_config_for_client["log_format"] = (
                    validated_factory_config.get("log_format", "json")
                )
                off_chain_config_for_client["secrets_providers"] = (
                    validated_factory_config.get("secrets_providers", [])
                )
                off_chain_config_for_client["secrets_provider_config"] = (
                    validated_factory_config.get("secrets_provider_config", {})
                )
                off_chain_config_for_client["temp_file_ttl"] = (
                    validated_factory_config.get("temp_file_ttl", 3600.0)
                )

                try:
                    current_off_chain_client = off_chain_client_class(
                        off_chain_config_for_client
                    )
                    cls._format_log(
                        "info",
                        f"Initialized off-chain client: {off_chain_type}",
                        {
                            "correlation_id": correlation_id,
                            "off_chain_type": off_chain_type,
                        },
                    )
                    # Collect temporary files from off-chain client if exposed
                    if hasattr(current_off_chain_client, "_temp_files"):
                        try:
                            for f_path, f_time in getattr(
                                current_off_chain_client, "_temp_files"
                            ).items():
                                cls._temp_files[f_path] = f_time
                        except Exception:
                            pass
                except Exception as e:
                    cls._metrics_inc(
                        "client_creation_failure",
                        {
                            "factory_client_type": cls.client_type,
                            "requested_client_type": off_chain_type,
                            "error_type": "offchain_init_failed",
                        },
                    )
                    msg = (
                        f"Failed to initialize off-chain client '{off_chain_type}': {e}"
                    )
                    cls._format_log(
                        "critical",
                        msg,
                        {
                            "correlation_id": correlation_id,
                            "off_chain_type": off_chain_type,
                        },
                    )
                    await alert_operator(f"CRITICAL: {msg}", level="CRITICAL")
                    raise DLTClientConfigurationError(
                        msg, cls.client_type, original_exception=e
                    )

            # Initialize DLT client
            try:
                dlt_client = client_class(config, current_off_chain_client)

                versioning_strategy = (
                    "flow"
                    if dlt_type == "corda"
                    else (
                        "chaincode"
                        if dlt_type == "fabric"
                        else (
                            "block_number"
                            if dlt_type in ("evm", "quorum")
                            else "timestamp"
                        )
                    )
                )
                cls._format_log(
                    "audit",
                    f"Initialized DLT client: {dlt_type} with versioning strategy {versioning_strategy}",
                    {
                        "correlation_id": correlation_id,
                        "dlt_type": dlt_type,
                        "versioning_strategy": versioning_strategy,
                        "config_version": validated_factory_config.get(
                            "config_version"
                        ),
                        "log_format": validated_factory_config.get("log_format"),
                    },
                )
                cls._metrics_inc(
                    "init_total",
                    {
                        "client_type": cls.client_type,
                        "operation": operation,
                        "status": "success",
                    },
                )
                cls._metrics_observe(
                    "init_latency",
                    {"client_type": cls.client_type, "operation": operation},
                    time.time() - start_time,
                )

                # Audit success event (non-blocking)
                cls._schedule_audit(
                    "dlt_factory.client_initialized",
                    dlt_type=dlt_type,
                    versioning_strategy=versioning_strategy,
                    correlation_id=correlation_id,
                )
                return dlt_client
            except Exception as e:
                cls._metrics_inc(
                    "client_creation_failure",
                    {
                        "factory_client_type": cls.client_type,
                        "requested_client_type": dlt_type,
                        "error_type": "dlt_client_init_failed",
                    },
                )
                msg = f"Failed to initialize DLT client '{dlt_type}': {e}"
                cls._format_log(
                    "critical",
                    msg,
                    {"correlation_id": correlation_id, "dlt_type": dlt_type},
                )
                await alert_operator(f"CRITICAL: {msg}", level="CRITICAL")
                raise DLTClientConfigurationError(
                    msg, cls.client_type, original_exception=e
                )

        except ValidationError as e:
            cls._metrics_inc(
                "client_creation_failure",
                {
                    "factory_client_type": cls.client_type,
                    "requested_client_type": "N/A",
                    "error_type": "factory_config_validation_failed",
                },
            )
            msg = f"Invalid factory configuration: {e}"
            cls._format_log(
                "critical",
                msg,
                {
                    "correlation_id": correlation_id,
                    "config_version": config.get("config_version", "unknown"),
                },
            )
            await alert_operator(f"CRITICAL: {msg}", level="CRITICAL")
            raise DLTClientConfigurationError(
                msg, cls.client_type, original_exception=e
            )
        except (DLTClientConfigurationError, DLTClientError):
            # Re-raise our custom exceptions without catching them again
            raise
        except Exception as e:
            cls._metrics_inc(
                "client_creation_failure",
                {
                    "factory_client_type": cls.client_type,
                    "requested_client_type": "N/A",
                    "error_type": "unexpected_factory_error",
                },
            )
            msg = f"Unexpected error during DLT factory initialization: {e}"
            cls._format_log("critical", msg, {"correlation_id": correlation_id})
            await alert_operator(f"CRITICAL: {msg}", level="CRITICAL")
            raise DLTClientError(msg, cls.client_type, original_exception=e)

    @classmethod
    def _format_log(
        cls, level: str, message: str, extra: Dict[str, Any] = None
    ) -> None:
        """
        Structured logs with JSON body; maps 'audit' level to 'info' for compatibility.
        """
        extra = extra or {}
        extra.update({"client_type": cls.client_type})
        # Map custom 'audit' level to 'info' to avoid attribute errors
        log_level = level.lower()
        if log_level == "audit":
            log_level = "info"

        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": log_level.upper(),
            "message": message,
            **extra,
        }
        # Always JSON-encode for consistent structure
        getattr(cls._logger, log_level)(json.dumps(scrub_secrets(log_entry)))

        # Critical/error paths also emit AUDIT event (non-blocking)
        if level.upper() in ("CRITICAL", "ERROR"):
            cls._schedule_audit(
                f"dlt_factory_error.{level.lower()}",
                message=message,
                details=scrub_secrets(extra),
            )

    @classmethod
    def list_available_dlt_clients(cls) -> List[str]:
        """Returns a list of available DLT client types from the static registry."""
        return list(DLT_CLIENT_REGISTRY.keys())

    @classmethod
    def list_available_off_chain_clients(cls) -> List[str]:
        """Returns a list of available off-chain client types from the static registry."""
        return list(OFF_CHAIN_CLIENT_REGISTRY.keys())


def _cleanup_at_exit():
    """
    Robust cleanup for atexit. Attempts to run async cleanup; falls back to sync best-effort.
    """
    try:
        asyncio.run(DLTFactory.cleanup_temp_files())
    except RuntimeError:
        # Event loop already running or cannot start; best-effort synchronous cleanup
        for temp_file in list(DLTFactory._temp_files.keys()):
            try:
                os.unlink(temp_file)
                DLTFactory._temp_files.pop(temp_file, None)
                _base_logger.info(
                    f"Factory (sync) cleaned up temporary file: {temp_file}"
                )
            except OSError:
                pass
        # Manager shutdown best-effort (if present)
        if DLTFactory._manager:
            try:
                DLTFactory._manager.shutdown()
            except Exception:
                pass
            DLTFactory._manager = None
            _base_logger.info("Factory (sync) multiprocessing Manager shut down.")


# Register cleanup on process exit
atexit.register(_cleanup_at_exit)
