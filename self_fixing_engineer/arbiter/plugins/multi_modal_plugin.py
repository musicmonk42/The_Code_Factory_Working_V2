# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# D:\SFE\self_fixing_engineer\arbiter\plugins\multi_modal_plugin.py
import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

# Assuming these are available from the arbiter package root
from self_fixing_engineer.arbiter.plugins.multi_modal_config import MultiModalConfig

# Initialize logger early before any usage
logger = logging.getLogger("self_fixing_engineer.arbiter.plugins.multi_modal_plugin")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    )
    logger.addHandler(handler)

# Import docker with try-except for graceful degradation
try:
    import docker

    DOCKER_AVAILABLE = True
except ImportError:
    DOCKER_AVAILABLE = False
    docker = None
    logger.warning("Docker library not available. Sandboxing will be disabled.")

# It's better to explicitly define the import paths for the sub-packages
from .multimodal.interface import (
    AudioProcessor,
    ConfigurationError,
    ImageProcessor,
    InvalidInputError,
    MultiModalException,
    ProcessingError,
    ProcessingResult,
    ProviderNotAvailableError,
    TextProcessor,
    VideoProcessor,
)
from .multimodal.providers.default_multimodal_providers import PluginRegistry

try:
    import redis.asyncio as redis
    from prometheus_client import REGISTRY, Counter, Histogram
except ImportError:
    # Prometheus and Redis are optional dependencies
    Counter, Histogram, redis, REGISTRY = None, None, None, None


# Helper functions for metrics
def get_or_create_counter(name: str, description: str, labels: List[str]) -> Counter:
    """Get or create a Prometheus counter metric."""
    if Counter is None:
        return None
    try:
        return Counter(name, description, labels)
    except ValueError as e:
        # Metric already exists - try to retrieve it from registry
        if "Duplicated timeseries" in str(e):
            if REGISTRY is not None:
                for collector in list(REGISTRY._collector_to_names.keys()):
                    try:
                        if hasattr(collector, "_name") and collector._name == name:
                            return collector
                    except AttributeError:
                        continue
            logger.debug(f"Counter {name} already registered, returning None")
            return None
        raise


def get_or_create_histogram(
    name: str, description: str, labels: List[str], buckets: Optional[tuple] = None
) -> Histogram:
    """Get or create a Prometheus histogram metric."""
    if Histogram is None:
        return None
    try:
        if buckets is not None:
            return Histogram(name, description, labels, buckets=buckets)
        return Histogram(name, description, labels)
    except ValueError as e:
        # Metric already exists - try to retrieve it from registry
        if "Duplicated timeseries" in str(e):
            if REGISTRY is not None:
                for collector in list(REGISTRY._collector_to_names.keys()):
                    try:
                        if hasattr(collector, "_name") and collector._name == name:
                            return collector
                    except AttributeError:
                        continue
            logger.debug(f"Histogram {name} already registered, returning None")
            return None
        raise


# --- Standalone Components for Clarity ---
class AuditLogger:
    """A dedicated logger for auditing multi-modal events."""

    def __init__(self, config):
        self.config = config
        self.audit_log = logging.getLogger("audit_log")
        # Ensure handlers are not duplicated on re-initialization
        if not self.audit_log.handlers:
            if self.config.destination == "file":
                handler = logging.FileHandler("audit.log")
                formatter = logging.Formatter("%(asctime)s - %(message)s")
                handler.setFormatter(formatter)
                self.audit_log.addHandler(handler)
            elif self.config.destination == "console":
                handler = logging.StreamHandler(sys.stdout)
                formatter = logging.Formatter("%(asctime)s - %(message)s")
                handler.setFormatter(formatter)
                self.audit_log.addHandler(handler)
            # Add other destinations (e.g., Kafka) here
        self.audit_log.setLevel(
            getattr(logging, self.config.log_level.upper(), logging.INFO)
        )

    def log_event(
        self,
        user_id: str,
        event_type: str,
        timestamp: str,
        success: bool,
        input_hash: Optional[str] = None,
        output_summary: Optional[str] = None,
        error_message: Optional[str] = None,
        model_version: str = "unknown",
        latency_ms: Optional[float] = None,
        operation_id: str = "",
        compliance_info: Optional[List[str]] = None,
    ):
        log_entry = {
            "timestamp": timestamp,
            "user_id": user_id,
            "event_type": event_type,
            "success": success,
            "operation_id": operation_id,
            "input_hash": input_hash,
            "output_summary": output_summary,
            "error_message": error_message,
            "model_version": model_version,
            "latency_ms": latency_ms,
            "compliance_info": compliance_info or [],
        }
        self.audit_log.info(json.dumps(log_entry))


class MetricsCollector:
    """Collects and exposes Prometheus metrics for the plugin."""

    def __init__(self, config):
        self.config = config
        if self.config.enabled and Counter and Histogram:
            # Use get_or_create_metric for thread-safe and idempotent metric registration
            self.requests_total = get_or_create_counter(
                "multimodal_requests_total",
                "Total processing requests",
                ["modality", "status"],
            )
            self.processing_latency_seconds = get_or_create_histogram(
                "multimodal_processing_latency_seconds",
                "Processing latency in seconds",
                ["modality"],
                buckets=(0.001, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, float("inf")),
            )
            self.cache_hits_total = get_or_create_counter(
                "multimodal_cache_hits_total", "Total cache hits", ["modality"]
            )
            self.cache_misses_total = get_or_create_counter(
                "multimodal_cache_misses_total", "Total cache misses", ["modality"]
            )
        else:
            self.requests_total = self.processing_latency_seconds = (
                self.cache_hits_total
            ) = self.cache_misses_total = None

    def increment_successful_requests(self, modality: str):
        if self.requests_total:
            self.requests_total.labels(modality=modality, status="success").inc()

    def increment_failed_requests(self, modality: str):
        if self.requests_total:
            self.requests_total.labels(modality=modality, status="failure").inc()

    def observe_latency(self, modality: str, latency_ms: float):
        if self.processing_latency_seconds:
            self.processing_latency_seconds.labels(modality=modality).observe(
                latency_ms / 1000.0
            )  # Convert ms to seconds

    def increment_cache_hits(self, modality: str):
        if self.cache_hits_total:
            self.cache_hits_total.labels(modality=modality).inc()

    def increment_cache_misses(self, modality: str):
        if self.cache_misses_total:
            self.cache_misses_total.labels(modality=modality).inc()


class CacheManager:
    """Manages an async Redis cache with graceful failure."""

    def __init__(self, config):
        self.config = config
        self.redis_client = None
        self._connected = False
        if self.config.enabled and self.config.type == "redis":
            if redis is None:
                logger.warning("Redis module not installed. Caching disabled.")
                self.config.enabled = False
            else:
                logger.info(
                    f"Cache enabled with {self.config.type} at {self.config.host}:{self.config.port}"
                )
                try:
                    self.redis_client = redis.Redis(
                        host=self.config.host,
                        port=self.config.port,
                        decode_responses=True,
                    )
                except Exception as e:
                    logger.error(f"Failed to initialize Redis client: {e}")
                    self.config.enabled = False

    async def connect(self):
        if self.config.enabled and self.redis_client and not self._connected:
            try:
                await self.redis_client.ping()
                self._connected = True
                logger.info("Connected to Redis cache.")
            except Exception as e:
                logger.error(
                    f"Failed to connect to Redis cache: {e}. Caching will be disabled."
                )
                self.config.enabled = False

    async def disconnect(self):
        if self.redis_client and self._connected:
            await self.redis_client.close()
            self._connected = False
            logger.info("Disconnected from Redis cache.")

    async def get(self, key: str) -> Optional[Dict[str, Any]]:
        if not self.config.enabled or not self.redis_client or not self._connected:
            return None
        try:
            value = await self.redis_client.get(key)
            return json.loads(value) if value else None
        except Exception as e:
            logger.error(
                f"Error getting from cache for key '{key}': {e}", exc_info=True
            )
            return None

    async def set(self, key: str, value: Dict[str, Any], ttl_seconds: int):
        if not self.config.enabled or not self.redis_client or not self._connected:
            return
        try:
            await self.redis_client.setex(key, ttl_seconds, json.dumps(value))
        except Exception as e:
            logger.error(f"Error setting to cache for key '{key}': {e}", exc_info=True)


class InputValidator:
    """
    Validates input data against predefined rules and masks PII for each modality.
    """

    @staticmethod
    def validate(modality: str, data: Any, security_config) -> Any:
        """
        Validates the input data based on the modality and security configuration.

        Args:
            modality (str): The type of input ('text', 'image', 'audio', 'video').
            data (Any): The input data to be validated.
            security_config: The security configuration object.

        Raises:
            InvalidInputError: If the input data fails validation rules.
        """
        if data is None:
            raise InvalidInputError("Input data cannot be None.")

        # Validate based on modality type
        if modality == "text":
            if not isinstance(data, str):
                raise InvalidInputError("Text input must be a string.")

            rules = security_config.input_validation_rules.get("text", {})
            if rules.get("max_length") and len(data) > rules["max_length"]:
                raise InvalidInputError(
                    f"Text input exceeds max length of {rules['max_length']} characters."
                )

            # Apply PII masking if enabled
            if security_config.mask_pii_in_logs:
                for pattern in security_config.pii_patterns.values():
                    data = re.sub(pattern, "[PII_MASKED]", data)
                logger.debug("PII masking applied to text input.")

        elif modality in ["image", "audio", "video"]:
            if isinstance(data, str):  # Assume it's a file path
                if not os.path.exists(data):
                    raise InvalidInputError(
                        f"{modality.capitalize()} input path does not exist: {data}"
                    )
            elif not isinstance(data, bytes):
                raise InvalidInputError(
                    f"{modality.capitalize()} input must be bytes or a file path."
                )

            # Check file size if a rule is defined
            rules = security_config.input_validation_rules.get(modality, {})
            if rules.get("max_size"):
                current_size = 0
                if isinstance(data, bytes):
                    current_size = len(data)
                elif isinstance(data, str) and os.path.exists(data):
                    current_size = os.path.getsize(data)

                if current_size > rules["max_size"]:
                    raise InvalidInputError(
                        f"{modality.capitalize()} data exceeds max size of {rules['max_size']} bytes."
                    )

        logger.debug(f"Input validated for {modality}.")
        return data


class OutputValidator:
    """
    Validates output data against predefined rules for each modality.
    """

    @staticmethod
    def validate(modality: str, result: Dict[str, Any], security_config):
        """
        Validates the processing result based on modality and security configuration.

        Args:
            modality (str): The type of input ('text', 'image', 'audio', 'video').
            result (Dict[str, Any]): The processing result dictionary.
            security_config: The security configuration object.

        Raises:
            MultiModalException: If the output data fails validation rules.
        """
        rules = security_config.output_validation_rules.get(modality, {})
        if not isinstance(result, dict):
            raise MultiModalException(
                f"Invalid output format for {modality} processing: not a dictionary."
            )

        if rules.get("require_success_flag") and not result.get("success"):
            raise MultiModalException(
                f"Output for {modality} processing did not indicate success."
            )

        if "model_confidence" in result and result["model_confidence"] < rules.get(
            "min_confidence", 0.0
        ):
            raise MultiModalException(
                f"Model confidence for {modality} below threshold."
            )

        logger.debug(f"Output validated for {modality}.")


class SandboxExecutor:
    """
    Executes functions in a sandboxed environment using Docker.
    This implementation provides a basic level of isolation for untrusted code execution.
    It serializes input/output via stdin/stdout and runs the function in a restricted
    Docker container with no network access and a read-only filesystem.
    """

    @staticmethod
    async def execute(func: Callable, *args, **kwargs) -> Any:
        """
        Executes a function in a sandboxed Docker container.
        """
        if not os.getenv("REAL_SANDBOXING_ENABLED", "false").lower() == "true":
            # This is the trusted input scenario.
            try:
                if asyncio.iscoroutinefunction(func):
                    return await func(*args, **kwargs)
                else:
                    loop = asyncio.get_running_loop()
                    with ThreadPoolExecutor(max_workers=1) as executor:
                        return await loop.run_in_executor(
                            executor, func, *args, **kwargs
                        )
            except Exception as e:
                raise MultiModalException(f"Error during execution: {e}") from e

        # Real sandboxing implementation using docker-py
        if not DOCKER_AVAILABLE:
            raise ConfigurationError(
                "Docker library not available. Sandboxing cannot be enabled."
            )

        try:
            client = docker.from_env()

            # Serialize inputs to a JSON string for passing to the container
            input_data = json.dumps(
                {"func_name": func.__name__, "args": args, "kwargs": kwargs}
            )

            # The Docker command will execute a Python one-liner to deserialize input,
            # run the function, and serialize the output.
            # NOTE: This sandboxing approach has limitations:
            # - Relative imports won't work in container
            # - Function module might not be available in container
            # - No way to pass actual function code to container
            # For production, use pre-built images with required dependencies.
            docker_cmd = f'python -c \'import json; from .multimodal.interface import {func.__name__}; data = json.loads(input()); result = {func.__name__}(*data["args"], **data["kwargs"]); print(json.dumps(result.model_dump()))\''

            # Run the container with restricted permissions
            # Note: docker-py does not support 'input' parameter. Use stdin instead.
            container = client.containers.run(
                image="python:3.9-slim",  # Or a more specific image with required dependencies
                command=["/bin/sh", "-c", docker_cmd],
                network_mode="none",
                read_only=True,
                stdin_open=True,
                detach=True,
                remove=True,  # Clean up container after it exits
            )

            # Write input data to container stdin
            try:
                sock = container.attach_socket(params={"stdin": 1, "stream": 1})
                # Access underlying socket with error handling for encapsulation
                try:
                    sock._sock.sendall(input_data.encode("utf-8"))
                except AttributeError:
                    # If _sock doesn't exist, try public API
                    sock.sendall(input_data.encode("utf-8"))
                sock.close()
            except Exception as e:
                logger.warning(
                    f"Failed to write to container stdin: {e}. Proceeding without input."
                )

            # Wait for the container to finish and get its logs
            result = container.wait(timeout=30)  # Wait for a max of 30 seconds
            output = container.logs(stdout=True, stderr=False).decode("utf-8")

            if result["StatusCode"] != 0:
                error_log = container.logs(stdout=False, stderr=True).decode("utf-8")
                raise MultiModalException(
                    f"Sandboxed execution failed with status code {result['StatusCode']}. Error: {error_log}"
                )

            logger.info(
                f"Sandboxed execution completed successfully. Container ID: {container.id[:12]}"
            )
            return ProcessingResult(**json.loads(output))

        except docker.errors.ImageNotFound:
            raise ConfigurationError(
                "Docker image for sandboxing not found. Please ensure 'python:3.9-slim' is available."
            )
        except docker.errors.ContainerError as e:
            raise ProcessingError(
                f"Container error during sandboxed execution: {e}"
            ) from e
        except docker.errors.APIError as e:
            raise MultiModalException(
                f"Docker API error during sandboxed execution: {e}"
            ) from e
        except Exception as e:
            raise MultiModalException(
                f"An unexpected error occurred during sandboxed execution: {e}"
            ) from e


class MultiModalProcessor:
    """
    Internal processor that dispatches data to the correct modality-specific provider.
    This acts as an orchestration layer for the actual processing logic.
    """

    def __init__(self, providers: Dict[str, Any]):
        self.providers = providers
        logger.info(
            "MultiModalProcessor initialized with providers: %s",
            list(self.providers.keys()),
        )

    async def process(self, modality: str, data: Any) -> Any:
        """
        Processes input data based on modality using the appropriate provider.
        """
        provider_instance = self.providers.get(modality)
        if not provider_instance:
            raise ProviderNotAvailableError(
                f"No provider registered for modality: {modality}"
            )

        # Call the provider's process method, which is assumed to be async
        if asyncio.iscoroutinefunction(provider_instance.process):
            result = await provider_instance.process(data)
        else:
            # Run synchronous provider process in a thread pool
            loop = asyncio.get_running_loop()
            with ThreadPoolExecutor(max_workers=1) as executor:
                result = await loop.run_in_executor(
                    executor, provider_instance.process, data
                )
        return result


global_multi_modal_plugin: Optional["MultiModalPlugin"] = None


class MultiModalPlugin:
    """
    Industry-leading plugin for multimodal (image/audio/video/text) processing.
    Extensible, secure, observable, and SOTA-compliant.

    This class orchestrates various modal processors, handles configuration,
    ensures security, logs events, and exposes metrics.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initializes the MultiModalPlugin with configuration and core components.
        """
        logger.info("Initializing MultiModalPlugin.")
        try:
            self.config: MultiModalConfig = MultiModalConfig.model_validate(
                config or {}
            )
        except Exception as e:
            raise ConfigurationError(f"Invalid plugin configuration: {e}") from e

        self.image_processor: Optional[ImageProcessor] = None
        self.audio_processor: Optional[AudioProcessor] = None
        self.video_processor: Optional[VideoProcessor] = None
        self.text_processor: Optional[TextProcessor] = None

        self._setup_hooks()
        self.audit_logger = AuditLogger(self.config.audit_log_config)
        self.metrics_collector = MetricsCollector(self.config.metrics_config)
        self.cache_manager = CacheManager(self.config.cache_config)

        # Circuit Breaker state
        self._circuit_breaker_states: Dict[str, str] = {
            modality: "closed"
            for modality in self.config.circuit_breaker_config.modalities
        }
        self._circuit_breaker_failures: Dict[str, int] = {
            modality: 0 for modality in self.config.circuit_breaker_config.modalities
        }
        self._circuit_breaker_last_failure_time: Dict[str, float] = {
            modality: 0.0 for modality in self.config.circuit_breaker_config.modalities
        }

        self._internal_processor = MultiModalProcessor({})
        self._lock = asyncio.Lock()

        logger.info("MultiModalPlugin initialized successfully (synchronous part).")

    async def initialize(self) -> None:
        """
        Asynchronously initializes the plugin's components and loads processors.
        This method should be called before processing any data.
        """
        await self.cache_manager.connect()
        self._load_processors()
        self._internal_processor.providers.update(
            {
                "image": self.image_processor,
                "audio": self.audio_processor,
                "video": self.video_processor,
                "text": self.text_processor,
            }
        )

        if self.config.metrics_config.enabled and Counter and Histogram:
            try:
                from prometheus_client import start_http_server

                start_http_server(self.config.metrics_config.exporter_port)
                logger.info(
                    f"Prometheus metrics server started on port {self.config.metrics_config.exporter_port}"
                )
            except Exception as e:
                logger.error(f"Failed to start Prometheus server: {e}")

        logger.info("MultiModalPlugin asynchronous initialization complete.")

    async def start(self):
        """Starts the plugin, making it ready for processing requests."""
        logger.info("MultiModalPlugin started.")

    async def stop(self):
        """Stops the plugin and performs graceful cleanup."""
        await self.cache_manager.disconnect()
        logger.info("MultiModalPlugin stopped.")

    async def health_check(self) -> bool:
        """Performs a health check of the plugin and its dependencies."""
        try:
            if self.config.cache_config.enabled and self.cache_manager.redis_client:
                await self.cache_manager.redis_client.ping()
            # Additional health checks for other processors can be added here
            return True
        except Exception as e:
            logger.error(f"MultiModalPlugin health check failed: {e}", exc_info=True)
            return False

    async def get_capabilities(self) -> List[str]:
        """Returns a list of the plugin's capabilities."""
        capabilities = ["multimodal_processing"]
        if self.config.image_processing.enabled:
            capabilities.append("image")
        if self.config.audio_processing.enabled:
            capabilities.append("audio")
        if self.config.video_processing.enabled:
            capabilities.append("video")
        if self.config.text_processing.enabled:
            capabilities.append("text")
        return capabilities

    def _load_processors(self):
        """
        Dynamically loads and initializes concrete processor instances for each modality.
        """
        if self.config.image_processing.enabled:
            self.image_processor = PluginRegistry.get_processor(
                "image",
                self.config.image_processing.default_provider,
                self.config.image_processing.provider_config,
            )
            logger.info(
                f"Image processing enabled with provider: {self.config.image_processing.default_provider}"
            )
        if self.config.audio_processing.enabled:
            self.audio_processor = PluginRegistry.get_processor(
                "audio",
                self.config.audio_processing.default_provider,
                self.config.audio_processing.provider_config,
            )
            logger.info(
                f"Audio processing enabled with provider: {self.config.audio_processing.default_provider}"
            )
        if self.config.video_processing.enabled:
            self.video_processor = PluginRegistry.get_processor(
                "video",
                self.config.video_processing.default_provider,
                self.config.video_processing.provider_config,
            )
            logger.info(
                f"Video processing enabled with provider: {self.config.video_processing.default_provider}"
            )
        if self.config.text_processing.enabled:
            self.text_processor = PluginRegistry.get_processor(
                "text",
                self.config.text_processing.default_provider,
                self.config.text_processing.provider_config,
            )
            logger.info(
                f"Text processing enabled with provider: {self.config.text_processing.default_provider}"
            )

    def _setup_hooks(self):
        """Initializes dictionaries for pre and post-processing hooks."""
        self.pre_hooks: Dict[str, List[Callable]] = {
            "image": [],
            "audio": [],
            "video": [],
            "text": [],
        }
        self.post_hooks: Dict[str, List[Callable]] = {
            "image": [],
            "audio": [],
            "video": [],
            "text": [],
        }

    def add_hook(self, modality: str, hook_fn: Callable, hook_type: str = "post"):
        """Registers a pre or post-processing hook for a specific modality."""
        if modality not in self.pre_hooks:
            raise ValueError(
                f"Invalid modality: {modality}. Must be one of {list(self.pre_hooks.keys())}"
            )
        if hook_type == "pre":
            self.pre_hooks[modality].append(hook_fn)
            logger.info(f"Registered pre-processing hook for {modality}")
        elif hook_type == "post":
            self.post_hooks[modality].append(hook_fn)
            logger.info(f"Registered post-processing hook for {modality}")
        else:
            raise ValueError(
                f"Invalid hook_type: {hook_type}. Must be 'pre' or 'post'."
            )

    async def _execute_hooks(self, modality: str, data: Any, hook_type: str) -> Any:
        """Executes registered hooks for a given modality and hook type."""
        hooks = (
            self.pre_hooks[modality]
            if hook_type == "pre"
            else self.post_hooks[modality]
        )
        processed_data = data
        for hook_fn in hooks:
            try:
                if asyncio.iscoroutinefunction(hook_fn):
                    processed_data = await hook_fn(processed_data)
                else:
                    loop = asyncio.get_running_loop()
                    processed_data = await loop.run_in_executor(
                        None, hook_fn, processed_data
                    )
            except Exception as e:
                logger.error(
                    f"Error executing {hook_type} hook for {modality}: {e}",
                    exc_info=True,
                )
                raise MultiModalException(
                    f"Hook execution failed for {modality}"
                ) from e
        return processed_data

    def _check_circuit_breaker(self, modality: str):
        """Checks the circuit breaker state and raises an error if it's open."""
        state = self._circuit_breaker_states.get(modality, "closed")
        if state == "open":
            last_failure_time = self._circuit_breaker_last_failure_time.get(
                modality, 0.0
            )
            timeout = self.config.circuit_breaker_config.timeout_seconds
            # Use time.monotonic() for consistent monotonic time measurement
            if time.monotonic() - last_failure_time > timeout:
                # Transition to half-open
                self._circuit_breaker_states[modality] = "half-open"
                logger.warning(f"Circuit breaker for {modality} is now 'half-open'.")
            else:
                raise ProcessingError(f"Circuit breaker for {modality} is open.")

    def _update_circuit_breaker(self, modality: str, success: bool):
        """Updates the circuit breaker state based on the result of the operation."""
        if success:
            if self._circuit_breaker_states.get(modality) in ["open", "half-open"]:
                logger.info(
                    f"Circuit breaker for {modality} is now 'closed' after a successful request."
                )
            self._circuit_breaker_failures[modality] = 0
            self._circuit_breaker_states[modality] = "closed"
        else:
            self._circuit_breaker_failures[modality] += 1
            if self._circuit_breaker_states.get(modality) == "half-open":
                self._circuit_breaker_states[modality] = "open"
                # Use time.monotonic() for consistent monotonic time measurement
                self._circuit_breaker_last_failure_time[modality] = time.monotonic()
                logger.error(
                    f"Circuit breaker for {modality} failed in 'half-open' state and is now 'open'."
                )
            elif (
                self._circuit_breaker_failures.get(modality)
                >= self.config.circuit_breaker_config.threshold
            ):
                self._circuit_breaker_states[modality] = "open"
                # Use time.monotonic() for consistent monotonic time measurement
                self._circuit_breaker_last_failure_time[modality] = time.monotonic()
                logger.error(
                    f"Circuit breaker for {modality} is now 'open' after {self._circuit_breaker_failures.get(modality)} consecutive failures."
                )

    async def _process_data(
        self, modality: str, data: Any, processor: Any
    ) -> ProcessingResult:
        """
        Internal method to handle the full processing pipeline for a single modality.
        Includes validation, circuit breaking, caching, sandboxing, hook execution, metrics, and auditing.
        """
        async with self._lock:
            operation_id = hashlib.sha256(str(datetime.now()).encode()).hexdigest()[:12]
            event_start_time = datetime.now(timezone.utc)
            input_hash = None
            success = False

            try:
                # 1. Circuit Breaker Check
                self._check_circuit_breaker(modality)

                # 2. Input Validation (including PII masking)
                validated_data = InputValidator.validate(
                    modality, data, self.config.security_config
                )

                # 3. Pre-processing Hooks
                processed_data_for_hooks = await self._execute_hooks(
                    modality, validated_data, "pre"
                )

                # Calculate hash for cache key and audit log
                if isinstance(processed_data_for_hooks, (bytes, str)):
                    input_hash = hashlib.sha256(
                        processed_data_for_hooks.encode()
                        if isinstance(processed_data_for_hooks, str)
                        else processed_data_for_hooks
                    ).hexdigest()
                else:
                    input_hash = hashlib.sha256(
                        json.dumps(processed_data_for_hooks, sort_keys=True).encode()
                    ).hexdigest()

                # 4. Cache Lookup
                cache_key = f"{modality}-{input_hash}-{processor.__class__.__name__}-{self.config.current_model_version.get(modality, '1.0')}"
                cached_result = await self.cache_manager.get(cache_key)
                if cached_result:
                    logger.info(
                        f"Cache hit for {modality} processing. Operation ID: {operation_id}"
                    )
                    self.metrics_collector.increment_cache_hits(modality)
                    result = ProcessingResult(**cached_result)
                    success = True
                else:
                    self.metrics_collector.increment_cache_misses(modality)
                    logger.info(
                        f"Processing {modality} data. Operation ID: {operation_id}"
                    )
                    processing_start_time = time.monotonic()

                    # 5. Core Processing (with optional Sandboxing)
                    if self.config.security_config.sandbox_enabled:
                        raw_result = await SandboxExecutor.execute(
                            processor.process, processed_data_for_hooks
                        )
                    else:
                        if asyncio.iscoroutinefunction(processor.process):
                            raw_result = await processor.process(
                                processed_data_for_hooks
                            )
                        else:
                            loop = asyncio.get_running_loop()
                            raw_result = await loop.run_in_executor(
                                None, processor.process, processed_data_for_hooks
                            )

                    latency_ms = (time.monotonic() - processing_start_time) * 1000
                    self.metrics_collector.observe_latency(modality, latency_ms)
                    logger.info(
                        f"{modality} core processing completed in {latency_ms:.2f}ms. Operation ID: {operation_id}"
                    )

                    # 6. Post-processing Hooks
                    final_result_data = await self._execute_hooks(
                        modality, raw_result.model_dump(), "post"
                    )
                    result = ProcessingResult(**final_result_data)

                    # 7. Output Validation
                    OutputValidator.validate(
                        modality, result.model_dump(), self.config.security_config
                    )
                    success = True

                    # 8. Cache Write
                    if self.config.cache_config.enabled:
                        await self.cache_manager.set(
                            cache_key,
                            result.model_dump(),
                            self.config.cache_config.ttl_seconds,
                        )

            except (
                MultiModalException,
                InvalidInputError,
                ConfigurationError,
                ProviderNotAvailableError,
                ProcessingError,
            ) as e:
                # Handle known application-level errors
                logger.error(
                    f"Application error during {modality} processing for Operation ID {operation_id}: {e}",
                    exc_info=True,
                )
                success = False
                result = ProcessingResult(
                    success=False, error=str(e), operation_id=operation_id
                )
            except Exception as e:
                # Catch-all for any other unexpected errors
                logger.critical(
                    f"Unhandled critical error during {modality} processing for Operation ID {operation_id}: {e}",
                    exc_info=True,
                )
                success = False
                result = ProcessingResult(
                    success=False,
                    error=f"An unexpected error occurred: {e}",
                    operation_id=operation_id,
                )
            finally:
                # Update metrics and audit log regardless of success
                if success:
                    self.metrics_collector.increment_successful_requests(modality)
                else:
                    self.metrics_collector.increment_failed_requests(modality)

                self._update_circuit_breaker(modality, success)

                self.audit_logger.log_event(
                    user_id=self.config.user_id_for_auditing,
                    event_type=(
                        f"{modality}_processing"
                        if success
                        else f"{modality}_processing_failed"
                    ),
                    timestamp=event_start_time.isoformat(),
                    success=success,
                    input_hash=input_hash,
                    output_summary=result.summary,
                    error_message=result.error,
                    model_version=self.config.current_model_version.get(
                        modality, "unknown"
                    ),
                    latency_ms=(
                        datetime.now(timezone.utc) - event_start_time
                    ).total_seconds()
                    * 1000,
                    operation_id=operation_id,
                    compliance_info=self.config.compliance_config.mapping.get(
                        modality, []
                    ),
                )
                return result

    async def process_image(self, image_data: Any) -> ProcessingResult:
        """
        Processes image data through the plugin pipeline.

        Args:
            image_data: The image data (bytes or file path) to process.

        Returns:
            A ProcessingResult object containing the outcome of the operation.
        """
        # Verify image processing is enabled and processor is configured.
        if not self.config.image_processing.enabled or not self.image_processor:
            return ProcessingResult(
                success=False,
                error="Image processing is not enabled or configured.",
                operation_id="",
            )

        # Check circuit breaker to prevent processing during failures.
        # Delegate to the core pipeline for validation, caching, sandboxing, and metrics.
        return await self._process_data("image", image_data, self.image_processor)

    async def process_audio(self, audio_data: Any) -> ProcessingResult:
        """
        Processes audio data through the plugin pipeline.

        Args:
            audio_data: The audio data (bytes or file path) to process.

        Returns:
            A ProcessingResult object containing the outcome of the operation.
        """
        # Verify audio processing is enabled and processor is configured.
        if not self.config.audio_processing.enabled or not self.audio_processor:
            return ProcessingResult(
                success=False,
                error="Audio processing is not enabled or configured.",
                operation_id="",
            )

        # Check circuit breaker to prevent processing during failures.
        # Delegate to the core pipeline for validation, caching, sandboxing, and metrics.
        return await self._process_data("audio", audio_data, self.audio_processor)

    async def process_video(self, video_data: Any) -> ProcessingResult:
        """
        Processes video data through the plugin pipeline.

        Args:
            video_data: The video data (bytes or file path) to process.

        Returns:
            A ProcessingResult object containing the outcome of the operation.
        """
        # Verify video processing is enabled and processor is configured.
        if not self.config.video_processing.enabled or not self.video_processor:
            return ProcessingResult(
                success=False,
                error="Video processing is not enabled or configured.",
                operation_id="",
            )

        # Check circuit breaker to prevent processing during failures.
        # Delegate to the core pipeline for validation, caching, sandboxing, and metrics.
        return await self._process_data("video", video_data, self.video_processor)

    async def process_text(self, text: str) -> ProcessingResult:
        """
        Processes text data through the plugin pipeline.

        Args:
            text: The text string to process.

        Returns:
            A ProcessingResult object containing the outcome of the operation.
        """
        # Verify text processing is enabled and processor is configured.
        if not self.config.text_processing.enabled or not self.text_processor:
            return ProcessingResult(
                success=False,
                error="Text processing is not enabled or configured.",
                operation_id="",
            )

        # Check circuit breaker to prevent processing during failures.
        # Delegate to the core pipeline for validation, caching, sandboxing, and metrics.
        return await self._process_data("text", text, self.text_processor)

    # --- Model Management and API ---
    def get_supported_providers(self, modality: str) -> List[str]:
        """Returns a list of supported providers for a given modality."""
        return PluginRegistry.get_supported_providers(modality)

    def set_default_provider(self, modality: str, provider_name: str):
        """Sets the default provider for a given modality and reloads processors."""
        if modality == "image":
            self.config.image_processing.default_provider = provider_name
        elif modality == "audio":
            self.config.audio_processing.default_provider = provider_name
        elif modality == "video":
            self.config.video_processing.default_provider = provider_name
        elif modality == "text":
            self.config.text_processing.default_provider = provider_name
        else:
            raise ValueError(f"Unknown modality: {modality}")

        self._load_processors()  # Reload processors to apply new default
        # Re-initialize the internal processor with updated provider instances
        self._internal_processor = MultiModalProcessor(
            {
                "image": self.image_processor,
                "audio": self.audio_processor,
                "video": self.video_processor,
                "text": self.text_processor,
            }
        )
        logger.info(f"Default provider for {modality} set to {provider_name}.")

    def update_model_version(self, modality: str, version: str):
        """Updates the active model version for a given modality."""
        self.config.current_model_version[modality] = version
        logger.info(f"Model version for {modality} updated to {version}.")

    # Async Context Manager for graceful shutdown (e.g., closing connections)
    async def __aenter__(self):
        logger.info("Entering MultiModalPlugin async context.")
        await self.initialize()  # Perform async initialization
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        logger.info("Exiting MultiModalPlugin async context. Performing cleanup.")
        await self.stop()  # Perform async cleanup
        if exc_val:
            logger.error(
                f"MultiModalPlugin exited with an exception: {exc_val}", exc_info=True
            )
        logger.info("MultiModalPlugin cleanup complete.")


# --- Example of how to use and configure ---
async def main():
    # Example Plugin Configuration (can be loaded from YAML/JSON in real app)
    plugin_config = {
        "image_processing": {
            "enabled": True,
            "default_provider": "default",
            "provider_config": {"default": {"mock_min_latency_ms": 10}},
        },
        "audio_processing": {
            "enabled": True,
            "default_provider": "default",
            "provider_config": {"default": {"mock_min_latency_ms": 10}},
        },
        "video_processing": {
            "enabled": True,
            "default_provider": "default",
            "provider_config": {"default": {"mock_min_latency_ms": 10}},
        },
        "text_processing": {
            "enabled": True,
            "default_provider": "default",
            "provider_config": {
                "default": {"mock_min_latency_ms": 5, "max_length": 5000}
            },
        },
        "security_config": {
            "sandbox_enabled": False,
            "mask_pii_in_logs": True,
            "pii_patterns": [
                r"\b\d{3}[-.]?\d{2}[-.]?\d{4}\b",
                r"[\w\.-]+@[\w\.-]+\.\w+",
            ],
            "input_validation_rules": {
                "image": {"max_size": 10 * 1024 * 1024},  # 10 MB
                "text": {"max_length": 5000},  # 5000 chars
            },
            "output_validation_rules": {
                "text": {"require_success_flag": True, "min_confidence": 0.7}
            },
        },
        "cache_config": {
            "enabled": False,  # Set to True and ensure Redis is running for testing
            "type": "redis",
            "host": "localhost",
            "port": 6379,
            "ttl_seconds": 60,
        },
        "metrics_config": {
            "enabled": False,  # Set to True to enable Prometheus metrics
            "exporter_port": 9090,
        },
        "circuit_breaker_config": {
            "enabled": True,
            "threshold": 3,
            "timeout_seconds": 30,
            "modalities": ["image", "text"],
        },
        "user_id_for_auditing": "api_client_123",
    }

    # Use the async context manager for proper initialization and cleanup
    async with MultiModalPlugin(config=plugin_config) as plugin:
        logger.info("\n--- Testing Text Processing ---")
        text_result = await plugin.process_text(
            "Hello, world! This is a test for the text processing module."
        )
        logger.info(f"Text processing result: {text_result.model_dump_json(indent=2)}")

        text_error_result = await plugin.process_text(
            ""
        )  # Example of invalid input (empty string might fail validation)
        logger.info(
            f"Text processing error result: {text_error_result.model_dump_json(indent=2)}"
        )

        logger.info("\n--- Testing Image Processing ---")
        # Need a simple way to get some valid image bytes for a test
        # A simple black and white 1x1 png
        png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90\x77\x53\xde\x00\x00\x00\x0cIDATx\x9c\x63\x00\x01\x00\x00\x05\x00\x01\x0d\x0a\x2d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        image_result = await plugin.process_image(png_bytes)
        logger.info(
            f"Image processing result: {image_result.model_dump_json(indent=2)}"
        )

        image_error_result = await plugin.process_image(
            None
        )  # Example of invalid input
        logger.info(
            f"Image processing error result: {image_error_result.model_dump_json(indent=2)}"
        )

        logger.info("\n--- Testing Disabled Modality ---")
        # Temporarily disable audio processing for this test
        plugin.config.audio_processing.enabled = False
        audio_disabled_result = await plugin.process_audio(b"dummy_audio")
        logger.info(
            f"Audio processing (disabled) result: {audio_disabled_result.model_dump_json(indent=2)}"
        )
        # Re-enable for other tests if needed
        plugin.config.audio_processing.enabled = True

        # Example of adding a custom hook
        def custom_text_post_hook(result_data: Dict[str, Any]) -> Dict[str, Any]:
            if result_data.get("success") and "processed_text" in result_data.get(
                "data", {}
            ):
                result_data["data"]["custom_tag"] = "processed_by_hook"
                result_data["summary"] += " (Hooked!)"
            return result_data

        plugin.add_hook("text", custom_text_post_hook, "post")
        logger.info("\n--- Testing Text Processing with Hook ---")
        hooked_text_result = await plugin.process_text("Another text for hooking.")
        logger.info(
            f"Hooked text processing result: {hooked_text_result.model_dump_json(indent=2)}"
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ConfigurationError as e:
        logger.critical(f"Configuration Error: {e}")
    except Exception as e:
        logger.critical(
            f"An unexpected error occurred during main execution: {e}", exc_info=True
        )
