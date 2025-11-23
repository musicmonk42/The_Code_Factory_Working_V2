import asyncio
import json
import logging
import os
import random
import signal
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import aioboto3
import aiofiles
import aiohttp
import redis.asyncio as aioredis
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, TopicPartition
from opentelemetry import trace
from tenacity import retry, stop_after_attempt, wait_random

from .audit_utils import AuditUtils
from .clients import AgentConfigurationService, MLPlatformClient

# --- Assuming these modules exist in the same package ---
from .config import MetaLearningConfig
from .metrics import (
    ML_CURRENT_MODEL_VERSION,
    ML_DATA_QUEUE_SIZE,
    ML_DEPLOYMENT_FAILURE_COUNT,
    ML_DEPLOYMENT_LATENCY,
    ML_DEPLOYMENT_SUCCESS_COUNT,
    ML_DEPLOYMENT_TRIGGER_COUNT,
    ML_EVALUATION_COUNT,
    ML_EVALUATION_LATENCY,
    ML_INGESTION_COUNT,
    ML_LEADER_STATUS,
    ML_ORCHESTRATOR_ERRORS,
    ML_TRAINING_FAILURE_COUNT,
    ML_TRAINING_LATENCY,
    ML_TRAINING_SUCCESS_COUNT,
    ML_TRAINING_TRIGGER_COUNT,
)
from .models import (
    DataIngestionError,
    LeaderElectionError,
    LearningRecord,
    ModelDeploymentError,
    ModelVersion,
    ValidationError,
)

# Assuming a `secrets_manager.py` file exists to handle credentials
# from .secrets_manager import get_secret

# --- Structured Logging Setup ---
# In a real application, this would be configured globally using logging.dictConfig
# For this file, we will manually format logs as JSON to meet the requirement.
# This ensures logs are machine-readable for platforms like ELK/Datadog/Splunk.
logger = logging.getLogger(__name__)


def _log_structured(level: int, message: str, **kwargs):
    """Helper to log messages in a structured JSON format."""
    log_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": logging.getLevelName(level),
        "message": message,
        **kwargs,
    }
    logger.log(level, json.dumps(log_data))


# --- Background Task Supervision ---
async def create_task_with_supervision(
    coro: Awaitable,
    task_name: str,
    restart_on_error: bool = True,
    restart_delay: int = 5,
):
    """
    Creates an asyncio task that is supervised. If it crashes, it logs the error
    and optionally restarts after a delay. This is more robust than a standard
    asyncio.TaskGroup for daemon tasks that should run indefinitely.
    """
    while True:
        task = asyncio.create_task(coro, name=task_name)
        try:
            await task
        except asyncio.CancelledError:
            _log_structured(
                logging.INFO,
                f"Task '{task_name}' was cancelled gracefully.",
                task_name=task_name,
            )
            break
        except Exception as e:
            _log_structured(
                logging.CRITICAL,
                f"Task '{task_name}' crashed with unhandled exception.",
                task_name=task_name,
                error=str(e),
                exc_info=True,
            )
            ML_ORCHESTRATOR_ERRORS.inc()
            if restart_on_error:
                _log_structured(
                    logging.INFO,
                    f"Restarting task '{task_name}' in {restart_delay} seconds...",
                    task_name=task_name,
                    restart_delay=restart_delay,
                )
                await asyncio.sleep(restart_delay)
            else:
                _log_structured(
                    logging.ERROR,
                    f"Task '{task_name}' failed and will NOT be restarted.",
                    task_name=task_name,
                    restart_on_error=restart_on_error,
                )
                break


# --- Sub-classes for Modular Orchestration Logic ---
class Ingestor:
    """Handles data ingestion from various sources (Kafka, S3, local file)."""

    def __init__(
        self,
        config: MetaLearningConfig,
        kafka_producer: Optional[AIOKafkaProducer] = None,
    ):
        self.config = config
        self.kafka_producer = kafka_producer
        self.s3_session = None

    async def initialize(self):
        """Initializes clients for ingestion."""
        if self.config.USE_S3_DATA_LAKE:
            self.s3_session = aioboto3.Session()

        if self.config.USE_KAFKA_INGESTION and not self.kafka_producer:
            # Note: AIOKafkaProducer batches messages automatically based on linger_ms and batch_size
            # for improved performance, fitting the "batching for ingestion" scalability goal.
            self.kafka_producer = AIOKafkaProducer(
                bootstrap_servers=self.config.KAFKA_BOOTSTRAP_SERVERS
            )
            await self.kafka_producer.start()
            _log_structured(logging.INFO, "Kafka producer for ingestion started.")
        elif self.kafka_producer and not self.kafka_producer.bootstrap_connected():
            await self.kafka_producer.start()
            _log_structured(
                logging.INFO, "Injected Kafka producer for ingestion started."
            )

    async def shutdown(self):
        """Shuts down ingestion clients."""
        if self.kafka_producer and not self.kafka_producer.closed():
            await self.kafka_producer.stop()
            _log_structured(logging.INFO, "Kafka producer for ingestion stopped.")

    async def ingest_learning_record(self, record_data: Dict[str, Any]):
        """Ingests a single learning record into the data pipeline."""
        with trace.get_tracer(__name__).start_as_current_span("ingest_learning_record"):
            ML_INGESTION_COUNT.inc()
            try:
                record = LearningRecord(**record_data)
                data_line = record.model_dump_json()

                if self.config.USE_KAFKA_INGESTION:
                    if (
                        not self.kafka_producer
                        or not self.kafka_producer.bootstrap_connected()
                    ):
                        raise DataIngestionError(
                            "Kafka producer not initialized or connected."
                        )

                    await self.kafka_producer.send_and_wait(
                        topic=self.config.KAFKA_TOPIC,
                        value=data_line.encode("utf-8"),
                        key=record.agent_id.encode("utf-8"),
                    )
                    _log_structured(
                        logging.DEBUG,
                        "Ingested learning record to Kafka.",
                        agent_id=record.agent_id,
                    )

                elif self.config.USE_S3_DATA_LAKE:
                    if not self.s3_session:
                        raise DataIngestionError("S3 session not initialized.")
                    s3_key = f"{self.config.DATA_LAKE_S3_PREFIX}{record.agent_id}/{record.timestamp.replace(':', '-')}_{record.session_id}.json"
                    async with self.s3_session.client("s3") as s3:
                        await s3.put_object(
                            Bucket=self.config.DATA_LAKE_S3_BUCKET,
                            Key=s3_key,
                            Body=data_line.encode("utf-8"),
                        )
                    _log_structured(
                        logging.DEBUG, "Ingested learning record to S3.", s3_key=s3_key
                    )

                else:
                    async with aiofiles.open(self.config.DATA_LAKE_PATH, "a") as f:
                        await f.write(data_line + "\n")
                    _log_structured(
                        logging.DEBUG,
                        "Ingested learning record to local file.",
                        path=self.config.DATA_LAKE_PATH,
                    )

            except ValidationError as e:
                _log_structured(
                    logging.ERROR,
                    "Failed to ingest record due to validation error.",
                    error=e.errors(),
                    exc_info=True,
                )
                ML_ORCHESTRATOR_ERRORS.inc()
                raise DataIngestionError(
                    f"Invalid learning record data: {e.errors()}"
                ) from e
            except Exception as e:
                _log_structured(
                    logging.ERROR,
                    "Failed to ingest learning record.",
                    error=str(e),
                    exc_info=True,
                )
                ML_ORCHESTRATOR_ERRORS.inc()
                raise DataIngestionError(f"Failed to write learning record: {e}") from e


class Trainer:
    """Manages the training, evaluation, and deployment of new models."""

    def __init__(
        self,
        config: MetaLearningConfig,
        ml_platform_client: MLPlatformClient,
        agent_config_service: AgentConfigurationService,
    ):
        self.config = config
        self.ml_platform_client = ml_platform_client
        self.agent_config_service = agent_config_service
        self._trained_models_awaiting_deployment: List[ModelVersion] = []

    async def _evaluate_model(self, model_version: ModelVersion) -> bool:
        """Evaluates a newly trained model against benchmarks."""
        with trace.get_tracer(__name__).start_as_current_span("evaluate_model"):
            ML_EVALUATION_COUNT.inc()
            start_time = time.monotonic()
            try:
                _log_structured(
                    logging.INFO,
                    "Evaluating model...",
                    model_id=model_version.model_id,
                    version=model_version.version,
                )
                # Simulate evaluation time
                await asyncio.sleep(random.uniform(2, 8))

                primary_metric = model_version.evaluation_metrics.get("accuracy", 0.0)
                if primary_metric >= self.config.MODEL_BENCHMARK_THRESHOLD:
                    _log_structured(
                        logging.INFO,
                        "Model passed evaluation.",
                        model_id=model_version.model_id,
                        version=model_version.version,
                        metric=primary_metric,
                        threshold=self.config.MODEL_BENCHMARK_THRESHOLD,
                    )
                    return True
                else:
                    _log_structured(
                        logging.WARNING,
                        "Model failed evaluation.",
                        model_id=model_version.model_id,
                        version=model_version.version,
                        metric=primary_metric,
                        threshold=self.config.MODEL_BENCHMARK_THRESHOLD,
                    )
                    return False
            except Exception as e:
                _log_structured(
                    logging.ERROR,
                    "Error during model evaluation.",
                    model_id=model_version.model_id,
                    error=str(e),
                    exc_info=True,
                )
                ML_ORCHESTRATOR_ERRORS.inc()
                return False
            finally:
                ML_EVALUATION_LATENCY.observe(time.monotonic() - start_time)

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_random(min=5, max=10),
        reraise=True,
        before_sleep=lambda retry_state: _log_structured(
            logging.WARNING,
            "Deployment failed, retrying...",
            attempt=retry_state.attempt_number,
            max_attempts=5,
        ),
    )
    async def _deploy_model(self, model_version: ModelVersion):
        """Deploys an updated model/policy to SFE agents with retries."""
        with trace.get_tracer(__name__).start_as_current_span("deploy_model"):
            ML_DEPLOYMENT_TRIGGER_COUNT.inc()
            start_time = time.monotonic()
            try:
                _log_structured(
                    logging.INFO,
                    "Attempting to deploy model.",
                    model_id=model_version.model_id,
                    version=model_version.version,
                )

                new_prioritization_weights = model_version.evaluation_metrics.get(
                    "new_prioritization_weights"
                )
                if new_prioritization_weights:
                    success = (
                        await self.agent_config_service.update_prioritization_weights(
                            new_prioritization_weights, model_version.version
                        )
                    )
                    if not success:
                        raise ModelDeploymentError(
                            "Failed to update prioritization weights."
                        )
                    _log_structured(
                        logging.INFO,
                        "Prioritization weights updated.",
                        model_id=model_version.model_id,
                    )

                new_policy_rules = model_version.evaluation_metrics.get(
                    "new_policy_rules"
                )
                if new_policy_rules:
                    success = await self.agent_config_service.update_policy_rules(
                        new_policy_rules, model_version.version
                    )
                    if not success:
                        raise ModelDeploymentError("Failed to update policy rules.")
                    _log_structured(
                        logging.INFO,
                        "Policy rules updated.",
                        model_id=model_version.model_id,
                    )

                if model_version.evaluation_metrics.get("is_rl_policy", False):
                    success = await self.agent_config_service.update_rl_policy(
                        model_version.model_id, model_version.version
                    )
                    if not success:
                        raise ModelDeploymentError("Failed to deploy RL policy.")
                    _log_structured(
                        logging.INFO,
                        "RL policy deployed.",
                        model_id=model_version.model_id,
                    )

                ML_DEPLOYMENT_SUCCESS_COUNT.inc()
                _log_structured(
                    logging.INFO,
                    "Model successfully deployed.",
                    model_id=model_version.model_id,
                    version=model_version.version,
                )

            except Exception as e:
                ML_DEPLOYMENT_FAILURE_COUNT.inc()
                _log_structured(
                    logging.ERROR,
                    "Deployment failed for model.",
                    model_id=model_version.model_id,
                    error=str(e),
                    exc_info=True,
                )
                raise ModelDeploymentError(
                    f"Deployment failed for {model_version.model_id}"
                ) from e
            finally:
                ML_DEPLOYMENT_LATENCY.observe(time.monotonic() - start_time)

    async def trigger_model_training_and_deployment(
        self, data_location: str
    ) -> Optional[ModelVersion]:
        """
        Orchestrates a full training and deployment cycle.
        Returns the new deployed model version on success.
        """
        with trace.get_tracer(__name__).start_as_current_span("trainer_full_cycle"):
            ML_TRAINING_TRIGGER_COUNT.inc()
            start_time = time.monotonic()
            try:
                # For synchronous, CPU-bound training tasks not handled by the external ML platform,
                # it would be advisable to offload them to a separate process pool to avoid blocking
                # the asyncio event loop. Example:
                # loop = asyncio.get_running_loop()
                # with concurrent.futures.ProcessPoolExecutor() as pool:
                #     result = await loop.run_in_executor(pool, cpu_bound_training_function, data)
                job_id = await self.ml_platform_client.trigger_training_job(
                    data_location,
                    {"model_type": "reinforcement_learning_policy", "epochs": 100},
                )
                _log_structured(
                    logging.INFO,
                    "ML training job triggered on platform.",
                    job_id=job_id,
                )

                # Poll for job status (simulated blocking call)
                status_result = {"status": "running"}
                while status_result["status"] == "running":
                    await asyncio.sleep(5)
                    status_result = (
                        await self.ml_platform_client.get_training_job_status(job_id)
                    )
                    _log_structured(
                        logging.DEBUG,
                        "Polling training job status.",
                        job_id=job_id,
                        status=status_result["status"],
                    )

                if status_result["status"] != "completed":
                    ML_TRAINING_FAILURE_COUNT.inc()
                    _log_structured(
                        logging.ERROR,
                        "ML training job failed.",
                        job_id=job_id,
                        error=status_result.get("error", "Unknown error"),
                    )
                    return None

                ML_TRAINING_SUCCESS_COUNT.inc()
                model_version = ModelVersion(
                    model_id=status_result["model_id"],
                    version=datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"),
                    training_timestamp=datetime.now(timezone.utc).isoformat(),
                    evaluation_metrics=status_result["metrics"],
                    deployment_status="pending",
                )

                if await self._evaluate_model(model_version):
                    await self._deploy_model(model_version)
                    model_version.deployment_status = "deployed"
                    model_version.deployment_timestamp = datetime.now(
                        timezone.utc
                    ).isoformat()
                    return model_version
                else:
                    model_version.deployment_status = "failed_evaluation"
                    return None
            except Exception as e:
                ML_TRAINING_FAILURE_COUNT.inc()
                _log_structured(
                    logging.ERROR,
                    "Error during training/deployment cycle.",
                    error=str(e),
                    exc_info=True,
                )
                ML_ORCHESTRATOR_ERRORS.inc()
                return None
            finally:
                ML_TRAINING_LATENCY.observe(time.monotonic() - start_time)


class MetaLearningOrchestrator:
    """
    A central, production-ready module to manage the meta-learning lifecycle for the SFE.
    Now features structured logging, robust task supervision, atomic leadership with fencing,
    and comprehensive health/readiness checks for high availability.
    """

    def __init__(
        self,
        config: MetaLearningConfig,
        ml_platform_client: Optional[MLPlatformClient] = None,
        agent_config_service: Optional[AgentConfigurationService] = None,
        kafka_producer: Optional[AIOKafkaProducer] = None,
        redis_client: Optional[aioredis.Redis] = None,
    ):
        self.config = config
        self._instance_id: str = str(uuid.uuid4())

        # Initialize modular clients and sub-classes
        self.ml_platform_client = ml_platform_client or MLPlatformClient(
            self.config.ML_PLATFORM_ENDPOINT
        )
        self.agent_config_service = agent_config_service or AgentConfigurationService(
            self.config.AGENT_CONFIG_SERVICE_ENDPOINT
        )
        self.ingestor = Ingestor(self.config, kafka_producer=kafka_producer)
        self.trainer = Trainer(
            self.config, self.ml_platform_client, self.agent_config_service
        )
        self.audit_utils = AuditUtils(log_path=self.config.LOCAL_AUDIT_LOG_PATH)

        self.redis_client = redis_client

        self._running = False
        self._training_check_task: Optional[asyncio.Task] = None
        self._leader_election_task: Optional[asyncio.Task] = None
        self._data_cleanup_task: Optional[asyncio.Task] = None

        self._new_records_count: int = 0
        self._records_count_lock = (
            asyncio.Lock()
        )  # Protect _new_records_count from race conditions
        self._current_active_model: Optional[ModelVersion] = None

        self._is_leader: bool = False
        self._fencing_token: Optional[int] = None

        # Validate local directories for fallback
        if not self.config.USE_S3_DATA_LAKE and not self.config.USE_KAFKA_INGESTION:
            self._validate_local_dir(os.path.dirname(self.config.DATA_LAKE_PATH))
        self._validate_local_dir(os.path.dirname(self.config.LOCAL_AUDIT_LOG_PATH))

        self._health_cache: Dict[str, Any] = {"timestamp": 0, "status": {}}
        self._readiness_cache: Dict[str, Any] = {"timestamp": 0, "ready": False}
        self._health_cache_ttl = 5
        self._readiness_cache_ttl = 5

        _log_structured(
            logging.INFO,
            "MetaLearningOrchestrator initialized.",
            instance_id=self._instance_id,
        )

    def _validate_local_dir(self, path: str):
        """Validates if a local directory exists and is writable."""
        if not os.path.exists(path):
            try:
                os.makedirs(path, exist_ok=True)
                _log_structured(logging.INFO, "Created local directory.", path=path)
            except OSError as e:
                _log_structured(
                    logging.CRITICAL,
                    "Failed to create local directory. Check permissions.",
                    path=path,
                    error=str(e),
                    exc_info=True,
                )
                raise SystemExit(
                    f"Startup failed: Cannot create directory {path}"
                ) from e
        elif not os.access(path, os.W_OK):
            _log_structured(
                logging.CRITICAL,
                "Local directory is not writable. Check permissions.",
                path=path,
            )
            raise SystemExit(f"Startup failed: Directory {path} not writable")

    async def start(self):
        """Starts the Meta-Learning Orchestrator's background tasks."""
        if self._running:
            _log_structured(
                logging.WARNING, "MetaLearningOrchestrator is already running."
            )
            return

        self._running = True
        _log_structured(
            logging.INFO, "Starting MetaLearningOrchestrator background tasks."
        )

        await self.ml_platform_client.__aenter__()
        await self.agent_config_service.__aenter__()
        await self.ingestor.initialize()

        # Load initial state
        if self.config.USE_KAFKA_INGESTION:
            self._new_records_count = await self._get_kafka_new_records_count()
        elif not self.config.USE_S3_DATA_LAKE:
            self._new_records_count = await self._get_local_file_records_count()

        if not self.redis_client:
            try:
                self.redis_client = aioredis.from_url(self.config.REDIS_URL)
                _log_structured(
                    logging.INFO, "Redis client connected for leader election."
                )
            except Exception as e:
                _log_structured(
                    logging.CRITICAL,
                    "Failed to connect to Redis for leader election. HA features will be disabled.",
                    error=str(e),
                    exc_info=True,
                )
                self.redis_client = None

        self._leader_election_task = asyncio.create_task(
            create_task_with_supervision(
                self._run_leader_election(), "leader_election_loop"
            )
        )

        # Leader-specific tasks are now started in _become_leader to ensure they only run after leadership is confirmed.

    async def stop(self):
        """Stops the Meta-Learning Orchestrator's background tasks gracefully."""
        if not self._running:
            _log_structured(logging.WARNING, "MetaLearningOrchestrator is not running.")
            return

        self._running = False
        _log_structured(
            logging.INFO, "Stopping MetaLearningOrchestrator background tasks."
        )

        tasks_to_cancel = [
            self._training_check_task,
            self._leader_election_task,
            self._data_cleanup_task,
        ]

        for task in tasks_to_cancel:
            if task and not task.done():
                task.cancel("Orchestrator shutdown initiated.")
                try:
                    await asyncio.wait_for(task, timeout=5)
                except asyncio.CancelledError:
                    pass
                except asyncio.TimeoutError:
                    _log_structured(
                        logging.ERROR,
                        "Timeout waiting for task to cancel.",
                        task_name=task.get_name(),
                    )
                except Exception as e:
                    _log_structured(
                        logging.ERROR,
                        "Error while waiting for task to stop.",
                        task_name=task.get_name(),
                        error=str(e),
                        exc_info=True,
                    )

        await self.ingestor.shutdown()
        await self.ml_platform_client.close()
        await self.agent_config_service.close()

        if self.redis_client:
            await self.redis_client.close()
            _log_structured(logging.INFO, "Redis client closed.")

        _log_structured(logging.INFO, "MetaLearningOrchestrator stopped.")

    async def _run_periodic_leader_task(
        self, coro: Callable[[], Awaitable], task_name: str, interval_seconds: int
    ):
        """A generic supervised loop for leader-only tasks."""
        while self._running:
            try:
                if self._is_leader:
                    if not await self._verify_leadership_and_fencing():
                        _log_structured(
                            logging.WARNING,
                            "Lost leadership during task execution. Stepping down.",
                            instance_id=self._instance_id,
                            task_name=task_name,
                        )
                        continue  # Leadership will be re-evaluated by the main leader election loop.

                    await coro()
                else:
                    _log_structured(
                        logging.DEBUG,
                        "Follower instance skipping leader task.",
                        instance_id=self._instance_id,
                        task_name=task_name,
                    )
            except asyncio.CancelledError:
                _log_structured(
                    logging.INFO,
                    "Periodic leader task cancelled.",
                    task_name=task_name,
                    instance_id=self._instance_id,
                )
                break
            except Exception as e:
                _log_structured(
                    logging.ERROR,
                    "Error during periodic leader task.",
                    task_name=task_name,
                    error=str(e),
                    exc_info=True,
                )
                ML_ORCHESTRATOR_ERRORS.inc()
            finally:
                # Use a cancellable sleep to ensure stop() is responsive.
                try:
                    await asyncio.sleep(interval_seconds)
                except asyncio.CancelledError:
                    break

    async def _run_leader_election(self):
        """Periodically attempts to acquire or renew leadership using a Redis lock."""
        while self._running:
            try:
                if self.redis_client:
                    acquired, current_lock_value = await self._acquire_leader_lock()
                    if acquired and not self._is_leader:
                        await self._become_leader(current_lock_value)
                    elif not acquired and self._is_leader:
                        await self._step_down_leadership("lock_lost")
                    elif not acquired:
                        holder_id = current_lock_value.get("instance_id", "unknown")
                        _log_structured(
                            logging.DEBUG,
                            "Instance is a FOLLOWER.",
                            instance_id=self._instance_id,
                            lock_holder=holder_id,
                        )
                else:
                    # Non-HA mode: always become leader if not already.
                    if not self._is_leader:
                        await self._become_leader(
                            {"instance_id": self._instance_id, "token": "non-ha-mode"}
                        )
                        _log_structured(
                            logging.WARNING,
                            "Redis not available. Running in non-HA mode (always leader).",
                            instance_id=self._instance_id,
                        )
            except asyncio.CancelledError:
                break
            except Exception as e:
                _log_structured(
                    logging.ERROR,
                    "Error during leader election cycle.",
                    error=str(e),
                    exc_info=True,
                )
                ML_ORCHESTRATOR_ERRORS.inc()
                if self._is_leader:
                    await self._step_down_leadership("election_error")
            finally:
                try:
                    await asyncio.sleep(self.config.REDIS_LOCK_TTL_SECONDS / 3)
                except asyncio.CancelledError:
                    break

    async def _become_leader(self, lock_info: Dict[str, Any]):
        """Transitions this instance to leader and starts leader-only tasks."""
        self._is_leader = True
        ML_LEADER_STATUS.set(1)
        self._fencing_token = lock_info.get("token")
        _log_structured(
            logging.INFO,
            "Instance is now the LEADER.",
            instance_id=self._instance_id,
            fencing_token=self._fencing_token,
        )
        await self.audit_utils.add_audit_event(
            "leader_elected",
            {"instance_id": self._instance_id, "fencing_token": self._fencing_token},
        )

        # Start leader-only tasks
        if not self._training_check_task or self._training_check_task.done():
            self._training_check_task = asyncio.create_task(
                create_task_with_supervision(
                    self._run_periodic_leader_task(
                        self._training_check_core,
                        "training_check_loop",
                        self.config.TRAINING_CHECK_INTERVAL_SECONDS,
                    ),
                    "training_check_loop",
                )
            )
        if not self._data_cleanup_task or self._data_cleanup_task.done():
            self._data_cleanup_task = asyncio.create_task(
                create_task_with_supervision(
                    self._run_periodic_leader_task(
                        self._data_cleanup_core, "data_cleanup_loop", 24 * 3600
                    ),
                    "data_cleanup_loop",
                )
            )

    async def _step_down_leadership(self, reason: str):
        """Forces this instance to step down from leadership and stops leader-only tasks."""
        if self._is_leader:
            self._is_leader = False
            ML_LEADER_STATUS.set(0)
            fencing_token_before_clear = self._fencing_token
            self._fencing_token = None
            _log_structured(
                logging.INFO,
                "Instance stepping down from leadership.",
                instance_id=self._instance_id,
                reason=reason,
                old_fencing_token=fencing_token_before_clear,
            )
            await self.audit_utils.add_audit_event(
                "leader_stepped_down",
                {"instance_id": self._instance_id, "reason": reason},
            )

            # Cancel leader-only tasks
            tasks_to_cancel = [self._training_check_task, self._data_cleanup_task]
            for task in tasks_to_cancel:
                if task and not task.done():
                    task.cancel("Stepping down from leadership.")
                    try:
                        await asyncio.wait_for(task, timeout=2)
                    except (asyncio.CancelledError, asyncio.TimeoutError):
                        pass

    async def _acquire_leader_lock(self) -> Tuple[bool, Dict[str, Any]]:
        """
        Attempts to acquire a distributed lock in Redis with a fencing token.
        This uses `SET key value NX EX ttl` which is an atomic operation.
        """
        if not self.redis_client:
            raise LeaderElectionError(
                "Redis client not initialized for leader election."
            )

        current_time = time.time()
        fencing_token = int(
            current_time * 1000
        )  # High-resolution timestamp as a simple fencing token.
        lock_value = {"instance_id": self._instance_id, "token": fencing_token}
        lock_value_str = json.dumps(lock_value)

        is_acquired = await self.redis_client.set(
            self.config.REDIS_LOCK_KEY,
            lock_value_str,
            nx=True,
            ex=self.config.REDIS_LOCK_TTL_SECONDS,
        )

        if is_acquired:
            return True, lock_value
        else:
            current_holder_str = await self.redis_client.get(self.config.REDIS_LOCK_KEY)
            current_holder_info = {}
            if current_holder_str:
                try:
                    current_holder_info = json.loads(current_holder_str)
                except json.JSONDecodeError:
                    _log_structured(
                        logging.ERROR,
                        "Failed to decode Redis lock value. Possible corruption.",
                        raw_value=current_holder_str,
                    )
                    current_holder_info = {"instance_id": "unknown", "token": 0}
            return False, current_holder_info

    async def _verify_leadership_and_fencing(self) -> bool:
        """
        Verifies that this instance is still the leader and holds the correct fencing token.
        This prevents a "split-brain" scenario where a delayed instance performs actions after losing leadership.
        """
        if not self._is_leader or not self.redis_client:
            return self._is_leader

        try:
            current_lock_value_str = await self.redis_client.get(
                self.config.REDIS_LOCK_KEY
            )

            if not current_lock_value_str:
                await self._step_down_leadership("lock_disappeared")
                return False

            current_lock_value = json.loads(current_lock_value_str)
            if (
                current_lock_value.get("instance_id") == self._instance_id
                and current_lock_value.get("token") == self._fencing_token
            ):
                # We are still the rightful leader.
                return True
            else:
                _log_structured(
                    logging.WARNING,
                    "Fencing token mismatch or new leader elected. Lost leadership.",
                    instance_id=self._instance_id,
                    my_token=self._fencing_token,
                    current_lock_value=current_lock_value,
                )
                await self._step_down_leadership("fencing_token_mismatch")
                return False
        except (json.JSONDecodeError, KeyError) as e:
            _log_structured(
                logging.ERROR,
                "Corrupted lock value in Redis. Stepping down to be safe.",
                error=str(e),
            )
            await self._step_down_leadership("corrupted_lock_value")
            return False
        except Exception as e:
            _log_structured(
                logging.ERROR,
                "Error verifying leadership. Stepping down to be safe.",
                error=str(e),
                exc_info=True,
            )
            await self._step_down_leadership(f"verification_error: {e}")
            return False

    async def _get_local_file_records_count(self) -> int:
        """Counts records in the local data lake file."""
        count = 0
        path = self.config.DATA_LAKE_PATH
        try:
            if os.path.exists(path):
                async with aiofiles.open(path, "r") as f:
                    async for _ in f:
                        count += 1
        except Exception as e:
            _log_structured(
                logging.ERROR,
                "Failed to count records in local file.",
                path=path,
                error=str(e),
                exc_info=True,
            )
            ML_ORCHESTRATOR_ERRORS.inc()
        return count

    async def _get_kafka_new_records_count(self) -> int:
        """Gets the count of new records in the Kafka topic by checking offsets."""
        if not self.config.USE_KAFKA_INGESTION:
            return 0
        consumer = None
        try:
            # Using a unique group_id to ensure we read offsets from the beginning without being part of a consumer group.
            consumer = AIOKafkaConsumer(
                self.config.KAFKA_TOPIC,
                bootstrap_servers=self.config.KAFKA_BOOTSTRAP_SERVERS,
                group_id=f"meta_learning_counter_{uuid.uuid4().hex}",
                enable_auto_commit=False,
                auto_offset_reset="earliest",
            )
            await consumer.start()
            partitions = await consumer.partitions_for_topic(self.config.KAFKA_TOPIC)
            if not partitions:
                return 0

            tps = [TopicPartition(self.config.KAFKA_TOPIC, p) for p in partitions]
            end_offsets = await consumer.end_offsets(tps)
            start_offsets = await consumer.beginning_offsets(tps)

            total_records = sum(
                end_offsets.get(p, 0) - start_offsets.get(p, 0) for p in tps
            )
            return total_records
        except Exception as e:
            _log_structured(
                logging.ERROR,
                "Failed to get Kafka new records count.",
                topic=self.config.KAFKA_TOPIC,
                error=str(e),
                exc_info=True,
            )
            ML_ORCHESTRATOR_ERRORS.inc()
            return self._new_records_count  # Return last known count on failure
        finally:
            if consumer:
                await consumer.stop()

    async def ingest_learning_record(self, record_data: Dict[str, Any]):
        """Ingests a single learning record and updates the internal count."""
        await self.ingestor.ingest_learning_record(record_data)
        async with self._records_count_lock:
            self._new_records_count += 1
            ML_DATA_QUEUE_SIZE.set(self._new_records_count)
        await self.audit_utils.add_audit_event(
            "record_ingested",
            {
                "agent_id": record_data.get("agent_id"),
                "session_id": record_data.get("session_id"),
                "event_type": record_data.get("event_type"),
            },
        )

    async def _training_check_core(self):
        """Core logic for the periodic training check, executed only by the leader."""
        current_records_count = self._new_records_count
        if self.config.USE_KAFKA_INGESTION:
            # For Kafka, always get the fresh count from the source of truth.
            current_records_count = await self._get_kafka_new_records_count()
            self._new_records_count = current_records_count
            ML_DATA_QUEUE_SIZE.set(current_records_count)

        if current_records_count >= self.config.MIN_RECORDS_FOR_TRAINING:
            _log_structured(
                logging.INFO,
                "Threshold met. Triggering model training.",
                instance_id=self._instance_id,
                record_count=current_records_count,
                threshold=self.config.MIN_RECORDS_FOR_TRAINING,
            )

            if self.config.USE_S3_DATA_LAKE:
                data_location = f"s3://{self.config.DATA_LAKE_S3_BUCKET}/{self.config.DATA_LAKE_S3_PREFIX}"
            elif self.config.USE_KAFKA_INGESTION:
                data_location = f"kafka://{self.config.KAFKA_BOOTSTRAP_SERVERS}/{self.config.KAFKA_TOPIC}"
            else:
                data_location = self.config.DATA_LAKE_PATH

            new_model = await self.trainer.trigger_model_training_and_deployment(
                data_location
            )
            if new_model and new_model.deployment_status == "deployed":
                if self._current_active_model:
                    self._current_active_model = self._current_active_model.model_copy(
                        update={"is_active": False}
                    )
                self._current_active_model = new_model.model_copy(
                    update={"is_active": True}
                )
                # Reset count for file-based accumulation. Kafka topics are streams, so a reset is conceptual.
                self._new_records_count = 0
                ML_DATA_QUEUE_SIZE.set(0)
                try:
                    # Model version is a timestamp string, e.g., "20250820184203"
                    numeric_version = float(new_model.version)
                    ML_CURRENT_MODEL_VERSION.set(numeric_version)
                except (ValueError, TypeError):
                    _log_structured(
                        logging.WARNING,
                        "Could not convert model version to numeric for gauge.",
                        version=new_model.version,
                    )

    async def _data_cleanup_core(self):
        """Core logic for the periodic data cleanup task, executed only by the leader."""
        _log_structured(
            logging.INFO,
            "Initiating data cleanup.",
            instance_id=self._instance_id,
            retention_days=self.config.DATA_RETENTION_DAYS,
        )

        if self.config.USE_S3_DATA_LAKE:
            await self._cleanup_s3_data_lake()
        elif self.config.USE_KAFKA_INGESTION:
            _log_structured(
                logging.INFO,
                "Kafka topic retention is managed by Kafka broker configuration (log.retention.ms). No direct cleanup performed by orchestrator.",
            )
        else:
            await self._cleanup_local_data_lake()

        await self.audit_utils.add_audit_event(
            "data_cleanup_completed", {"retained_days": self.config.DATA_RETENTION_DAYS}
        )

    async def _cleanup_s3_data_lake(self):
        """Cleans up old objects from the S3 data lake. Note: S3 Lifecycle Policies are the recommended production approach."""
        cutoff_time = datetime.now(timezone.utc) - timedelta(
            days=self.config.DATA_RETENTION_DAYS
        )
        try:
            async with aioboto3.Session().client("s3") as s3_client:
                paginator = s3_client.get_paginator("list_objects_v2")
                async for page in paginator.paginate(
                    Bucket=self.config.DATA_LAKE_S3_BUCKET,
                    Prefix=self.config.DATA_LAKE_S3_PREFIX,
                ):
                    if "Contents" in page:
                        objects_to_delete = [
                            {"Key": obj["Key"]}
                            for obj in page["Contents"]
                            if obj["LastModified"] < cutoff_time
                        ]

                        if objects_to_delete:
                            delete_payload = {"Objects": objects_to_delete}
                            await s3_client.delete_objects(
                                Bucket=self.config.DATA_LAKE_S3_BUCKET,
                                Delete=delete_payload,
                            )
                            _log_structured(
                                logging.INFO,
                                "Deleted old objects from S3.",
                                count=len(objects_to_delete),
                                bucket=self.config.DATA_LAKE_S3_BUCKET,
                            )
        except Exception as e:
            _log_structured(
                logging.ERROR,
                "Failed to cleanup S3 data lake.",
                error=str(e),
                exc_info=True,
            )
            ML_ORCHESTRATOR_ERRORS.inc()

    async def _cleanup_local_data_lake(self):
        """Cleans up old records from the local data lake file using an atomic replace operation."""
        temp_path = self.config.DATA_LAKE_PATH + ".tmp_cleanup"
        retained_count = 0
        cutoff_time = datetime.now(timezone.utc) - timedelta(
            days=self.config.DATA_RETENTION_DAYS
        )

        try:
            async with (
                aiofiles.open(self.config.DATA_LAKE_PATH, "r") as infile,
                aiofiles.open(temp_path, "w") as outfile,
            ):
                async for line in infile:
                    try:
                        record_data = json.loads(line)
                        record_timestamp = datetime.fromisoformat(
                            record_data["timestamp"]
                        )
                        if record_timestamp >= cutoff_time:
                            await outfile.write(line)
                            retained_count += 1
                    except (json.JSONDecodeError, ValueError, KeyError):
                        # Skip corrupted lines but log them.
                        _log_structured(
                            logging.WARNING,
                            "Skipping corrupted line during cleanup.",
                            line=line.strip(),
                        )

            # Atomic move operation
            await asyncio.to_thread(os.replace, temp_path, self.config.DATA_LAKE_PATH)
            self._new_records_count = retained_count
            ML_DATA_QUEUE_SIZE.set(self._new_records_count)
            _log_structured(
                logging.INFO,
                "Local data cleanup complete.",
                retained_records=retained_count,
            )
        except Exception as e:
            _log_structured(
                logging.ERROR,
                "Error during local data cleanup.",
                error=str(e),
                exc_info=True,
            )
            ML_ORCHESTRATOR_ERRORS.inc()
            # Ensure temp file is removed on failure
            if os.path.exists(temp_path):
                os.remove(temp_path)

    async def get_health_status(self) -> Dict[str, Any]:
        """Returns a cached dictionary reflecting the orchestrator's operational health."""
        current_time = time.time()
        if (
            current_time - self._health_cache.get("timestamp", 0)
            < self._health_cache_ttl
        ):
            return self._health_cache["status"]

        is_healthy = (
            self._running
            and self._leader_election_task
            and not self._leader_election_task.done()
        )

        status = {
            "status": "healthy" if is_healthy else "unhealthy",
            "is_running": self._running,
            "is_leader": self._is_leader,
            "instance_id": self._instance_id,
            "tasks_status": {
                "leader_election": (
                    "running"
                    if self._leader_election_task
                    and not self._leader_election_task.done()
                    else "stopped"
                ),
                "training_check": (
                    "running"
                    if self._training_check_task
                    and not self._training_check_task.done()
                    else "stopped"
                ),
                "data_cleanup": (
                    "running"
                    if self._data_cleanup_task and not self._data_cleanup_task.done()
                    else "stopped"
                ),
            },
            "new_records_count": self._new_records_count,
            "current_active_model": (
                self._current_active_model.model_dump()
                if self._current_active_model
                else None
            ),
        }

        # Check external dependencies with a short timeout
        try:
            if self.redis_client:
                await asyncio.wait_for(self.redis_client.ping(), timeout=1.0)
            status["redis_connected"] = True
        except Exception:
            status["redis_connected"] = False

        status["kafka_connected"] = bool(
            self.ingestor.kafka_producer
            and self.ingestor.kafka_producer.bootstrap_connected()
        )

        self._health_cache = {"timestamp": current_time, "status": status}
        return status

    async def is_ready(self) -> bool:
        """
        Performs a deep check to see if the orchestrator is ready to perform its duties.
        For a Kubernetes readiness probe, this should only return true if this instance is the leader
        and all its critical dependencies are available.
        """
        current_time = time.time()
        if (
            current_time - self._readiness_cache.get("timestamp", 0)
            < self._readiness_cache_ttl
        ):
            return self._readiness_cache["ready"]

        is_ready_flag = False
        try:
            # Must be running and be the designated leader.
            if not self._running or not self._is_leader:
                return False

            # Leader must have its core tasks running.
            if not all(
                task and not task.done()
                for task in [self._leader_election_task, self._training_check_task]
            ):
                return False

            # --- Check critical dependencies with longer, production-safe timeouts ---
            timeout = 3.0

            # Verify leadership lock is still held.
            if not await asyncio.wait_for(
                self._verify_leadership_and_fencing(), timeout=timeout
            ):
                raise Exception("Lost leadership during readiness check.")

            # Check connectivity to external services.
            if self.config.USE_KAFKA_INGESTION and not (
                self.ingestor.kafka_producer
                and self.ingestor.kafka_producer.bootstrap_connected()
            ):
                raise Exception("Kafka producer is not connected.")

            if self.config.USE_S3_DATA_LAKE:
                async with self.ingestor.s3_session.client("s3") as s3:
                    await asyncio.wait_for(
                        s3.head_bucket(Bucket=self.config.DATA_LAKE_S3_BUCKET),
                        timeout=timeout,
                    )

            # Check dependent microservices are healthy.
            async with aiohttp.ClientSession() as session:
                ml_resp = await asyncio.wait_for(
                    session.get(f"{self.ml_platform_client.endpoint}/health"),
                    timeout=timeout,
                )
                ml_resp.raise_for_status()
                if (await ml_resp.json()).get("status") != "healthy":
                    raise Exception("Dependency 'ML Platform' is unhealthy.")

                agent_resp = await asyncio.wait_for(
                    session.get(f"{self.agent_config_service.endpoint}/health"),
                    timeout=timeout,
                )
                agent_resp.raise_for_status()
                if (await agent_resp.json()).get("status") != "healthy":
                    raise Exception("Dependency 'Agent Config Service' is unhealthy.")

            is_ready_flag = True
            return True
        except Exception as e:
            _log_structured(logging.WARNING, "Readiness check failed.", reason=str(e))
            return False
        finally:
            self._readiness_cache = {"timestamp": current_time, "ready": is_ready_flag}


# --- Signal Handler Setup for Graceful Shutdown ---
def setup_signal_handlers(orchestrator: MetaLearningOrchestrator):
    """Sets up signal handlers for graceful shutdown (SIGINT, SIGTERM)."""
    loop = asyncio.get_event_loop()

    async def shutdown_handler(sig):
        _log_structured(logging.INFO, "Received shutdown signal.", signal=sig.name)
        await orchestrator.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(
            sig, lambda s=sig: asyncio.create_task(shutdown_handler(s))
        )
    _log_structured(logging.INFO, "Signal handlers registered for graceful shutdown.")
