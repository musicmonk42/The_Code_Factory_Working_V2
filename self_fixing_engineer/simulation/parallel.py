# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

import asyncio
import atexit
import json
import logging
import os
import sys
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import psutil
import yaml

# --- Optional Dependency Imports ---
try:
    import ray
    from ray.rllib.algorithms.ppo import PPOConfig
    from ray.rllib.models.torch.fcnet import FullyConnectedNetwork
    from ray.rllib.models.torch.torch_modelv2 import TorchModelV2
    from ray.rllib.policy.policy import Policy
    from ray.rllib.utils.framework import try_import_torch

    RAY_AVAILABLE = True
    RLLIB_AVAILABLE = True
    torch, nn = try_import_torch()
except ImportError:
    RAY_AVAILABLE = False
    RLLIB_AVAILABLE = False
    torch = None

try:
    import dask
    from dask.distributed import Client, LocalCluster, as_completed

    DASK_AVAILABLE = True
except ImportError:
    DASK_AVAILABLE = False

try:
    import redis

    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

try:
    from kubernetes import client as k8s_client
    from kubernetes import config as k8s_config
    from kubernetes.client.rest import ApiException as K8sApiException

    K8S_AVAILABLE = True
except ImportError:
    K8S_AVAILABLE = False
    K8sApiException = None

try:
    import mpi4py

    MPI_AVAILABLE = True
except ImportError:
    MPI_AVAILABLE = False

try:
    import uvicorn
    from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
    from fastapi.security import (
        HTTPAuthorizationCredentials,
        HTTPBearer,
        OAuth2PasswordBearer,
    )
    from jose import JWTError, jwt
    from pydantic import BaseModel, Field, ValidationError, model_validator

    FASTAPI_AVAILABLE = True
    PYDANTIC_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    PYDANTIC_AVAILABLE = False

try:
    from prometheus_client import (
        CollectorRegistry,
        Counter,
        Gauge,
        Histogram,
        make_asgi_app,
    )

    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False

try:
    import boto3
    from botocore.exceptions import ClientError

    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False
    ClientError = None

try:
    from test_generation.audit_log import AuditLogger as DLTLogger

    AUDIT_LOGGER_AVAILABLE = True
except ImportError:
    AUDIT_LOGGER_AVAILABLE = False
    DLTLogger = None


# --- Logging Setup ---
parallel_logger = logging.getLogger("simulation.parallel")
parallel_logger.setLevel(logging.INFO)
if not parallel_logger.hasHandlers():
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s - [%(levelname)s] - %(message)s")
    handler.setFormatter(formatter)
    parallel_logger.addHandler(handler)

# --- Metrics (Idempotent and Thread-Safe Registration) ---
if PROMETHEUS_AVAILABLE:
    _metrics_registry = CollectorRegistry(auto_describe=True)
else:
    _metrics_registry = None

_metrics_lock = threading.Lock()


def get_or_create_metric(
    metric_type, name, documentation, labelnames=None, buckets=None
):
    """Get or create a metric, handling both real and mocked metric types"""
    labelnames = labelnames or []

    # Handle mocked metric types - check if it's callable
    if not isinstance(metric_type, type):
        # If it's a mock or callable, check if it's actually callable
        if callable(metric_type):
            return metric_type(name, documentation, labelnames)
        else:
            # If it's not callable and not a type, something is wrong
            raise TypeError(
                f"metric_type must be a type or callable, got {type(metric_type)}"
            )

    # Rest of original logic...
    if name in _metrics_registry._names_to_collectors:
        existing_metric = _metrics_registry._names_to_collectors[name]
        if isinstance(existing_metric, metric_type):
            return existing_metric

    if metric_type == Histogram and buckets:
        return metric_type(
            name, documentation, labelnames, buckets=buckets, registry=_metrics_registry
        )
    else:
        return metric_type(name, documentation, labelnames, registry=_metrics_registry)


if PROMETHEUS_AVAILABLE:
    PARALLEL_METRICS = {
        "backend_usage_total": get_or_create_metric(
            Counter,
            "parallel_backend_usage_total",
            "Total usage of each parallel backend",
            ["backend_name"],
        ),
        "simulation_tasks_total": get_or_create_metric(
            Counter,
            "parallel_simulation_tasks_total",
            "Total simulation tasks processed",
            ["status"],
        ),
        "rl_tuner_heartbeat": get_or_create_metric(
            Gauge,
            "parallel_rl_tuner_heartbeat_timestamp",
            "Timestamp of last RL tuner heartbeat",
        ),
        "rl_tuner_status": get_or_create_metric(
            Gauge, "parallel_rl_tuner_status", "Status of RL tuner (1=alive, 0=dead)"
        ),
        "backend_availability": get_or_create_metric(
            Gauge,
            "parallel_backend_availability",
            "Availability of parallel backends (1=available, 0=unavailable)",
            ["backend_name"],
        ),
        "job_latency": get_or_create_metric(
            Histogram,
            "parallel_job_latency_seconds",
            "Latency of individual jobs",
            ["backend_name"],
        ),
    }
else:
    parallel_logger.warning(
        "Prometheus client not available. Metrics will not be collected."
    )
    PARALLEL_METRICS = {}

# --- Pydantic Models for Configuration ---
if PYDANTIC_AVAILABLE:

    class RLTunerConfig(BaseModel):
        enabled: bool = False
        checkpoint_dir: str = "rl_tuner_checkpoint"
        reward_log: str = "rl_rewards.log"
        training_interval_seconds: int = 30
        heartbeat_timeout_seconds: int = 120
        max_concurrency_limit: int = 100

    class BackendSpecificConfig(BaseModel):
        kubernetes_namespace: Optional[str] = None
        kubernetes_image: Optional[str] = None
        kubernetes_job_timeout: int = 3600
        aws_batch_job_queue: Optional[str] = None
        aws_batch_job_definition: Optional[str] = None
        aws_batch_s3_bucket: Optional[str] = None
        aws_batch_job_timeout: int = 3600

    class SecretsConfig(BaseModel):
        redis_url: Optional[str] = None
        aws_access_key_id: Optional[str] = None
        aws_secret_access_key: Optional[str] = None

    class ParallelConfig(BaseModel):
        default_backend: str = "local_asyncio"
        max_local_workers: Optional[int] = None
        rl_tuner: RLTunerConfig = Field(default_factory=RLTunerConfig)
        backend_configs: BackendSpecificConfig = Field(
            default_factory=BackendSpecificConfig
        )
        secrets: SecretsConfig = Field(default_factory=SecretsConfig)

        @model_validator(mode="after")
        def validate_backend_configs(self):
            if (
                self.default_backend == "kubernetes"
                and not self.backend_configs.kubernetes_image
            ):
                raise ValueError(
                    "Kubernetes image must be specified if it's the default backend."
                )
            if self.default_backend == "aws_batch" and (
                not self.backend_configs.aws_batch_job_queue
                or not self.backend_configs.aws_batch_job_definition
            ):
                raise ValueError(
                    "AWS Batch job queue and definition must be specified if it's the default backend."
                )
            return self

        @classmethod
        def load_from_yaml(cls, file_path: str):
            try:
                with open(file_path, "r") as f:
                    config_data = yaml.safe_load(f)
                return cls(**config_data)
            except (FileNotFoundError, yaml.YAMLError, ValidationError) as e:
                parallel_logger.critical(
                    f"Critical Error: Failed to load or validate parallel configuration from {file_path}: {e}. Aborting startup."
                )
                sys.exit(1)
            except Exception as e:
                parallel_logger.critical(
                    f"Critical Error: An unexpected error occurred while loading parallel config from {file_path}: {e}. Aborting startup."
                )
                sys.exit(1)

else:
    parallel_logger.warning(
        "Pydantic not available. Configuration validation will be skipped."
    )

    class ParallelConfig:
        def __init__(self):
            self.default_backend = "local_asyncio"
            self.max_local_workers = None
            self.rl_tuner = type(
                "RLTunerConfig",
                (object,),
                {
                    "enabled": False,
                    "checkpoint_dir": "rl_tuner_checkpoint",
                    "reward_log": "rl_rewards.log",
                    "training_interval_seconds": 30,
                    "heartbeat_timeout_seconds": 120,
                    "max_concurrency_limit": 100,
                },
            )()
            self.backend_configs = type(
                "BackendSpecificConfig",
                (object,),
                {
                    "kubernetes_namespace": os.getenv("K8S_NAMESPACE", "default"),
                    "kubernetes_image": os.getenv("K8S_SIMULATION_IMAGE"),
                    "kubernetes_job_timeout": 3600,
                    "aws_batch_job_queue": os.getenv("AWS_BATCH_JOB_QUEUE"),
                    "aws_batch_job_definition": os.getenv("AWS_BATCH_JOB_DEFINITION"),
                    "aws_batch_s3_bucket": os.getenv("AWS_BATCH_S3_BUCKET"),
                    "aws_batch_job_timeout": 3600,
                },
            )()
            self.secrets = type(
                "SecretsConfig",
                (object,),
                {
                    "redis_url": os.getenv("PROGRESS_REDIS_URL"),
                    "aws_access_key_id": os.getenv("AWS_ACCESS_KEY_ID"),
                    "aws_secret_access_key": os.getenv("AWS_SECRET_ACCESS_KEY"),
                },
            )()

        @classmethod
        def load_from_yaml(cls, file_path: str):
            parallel_logger.warning(
                "Pydantic not available. Skipping configuration validation."
            )
            try:
                with open(file_path, "r") as f:
                    config_data = yaml.safe_load(f)
                instance = cls()
                if "default_backend" in config_data:
                    instance.default_backend = config_data["default_backend"]
                if "max_local_workers" in config_data:
                    instance.max_local_workers = config_data["max_local_workers"]
                if "rl_tuner" in config_data:
                    for k, v in config_data["rl_tuner"].items():
                        setattr(instance.rl_tuner, k, v)
                if "backend_configs" in config_data:
                    for k, v in config_data["backend_configs"].items():
                        setattr(instance.backend_configs, k, v)
                if "secrets" in config_data:
                    for k, v in config_data["secrets"].items():
                        setattr(instance.secrets, k, v)
                return instance
            except (FileNotFoundError, yaml.YAMLError) as e:
                parallel_logger.critical(
                    f"Critical Error: Failed to load parallel configuration from {file_path}: {e}. Aborting startup."
                )
                sys.exit(1)
            except Exception as e:
                parallel_logger.critical(
                    f"Critical Error: An unexpected error occurred while loading parallel config from {file_path}: {e}. Aborting startup."
                )
                sys.exit(1)


# Load global configuration
PARALLEL_CONFIG_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "config", "parallel_config.yaml"
)
os.makedirs(os.path.dirname(PARALLEL_CONFIG_FILE), exist_ok=True)
if not os.path.exists(PARALLEL_CONFIG_FILE):
    parallel_logger.info(
        f"Creating a default parallel config file at {PARALLEL_CONFIG_FILE}"
    )
    default_config_content = {
        "default_backend": "local_asyncio",
        "max_local_workers": None,
        "rl_tuner": {
            "enabled": False,
            "checkpoint_dir": "rl_tuner_checkpoint",
            "reward_log": "rl_rewards.log",
            "training_interval_seconds": 30,
            "heartbeat_timeout_seconds": 120,
            "max_concurrency_limit": 100,
        },
        "backend_configs": {
            "kubernetes_namespace": "default",
            "kubernetes_image": "your-k8s-sim-image:latest",
            "aws_batch_job_queue": "your-aws-batch-queue",
            "aws_batch_job_definition": "your-aws-batch-job-definition",
            "aws_batch_s3_bucket": "your-s3-results-bucket",
        },
    }
    with open(PARALLEL_CONFIG_FILE, "w") as f:
        yaml.dump(default_config_content, f, indent=2)

GLOBAL_PARALLEL_CONFIG = ParallelConfig.load_from_yaml(PARALLEL_CONFIG_FILE)


# --- Secure Credential Management ---
def load_secret(secret_name: str) -> str:
    if not BOTO3_AVAILABLE:
        raise RuntimeError("Boto3 not available for secure secret loading.")
    client = boto3.client("secretsmanager")
    try:
        response = client.get_secret_value(SecretId=secret_name)
        return response["SecretString"]
    except ClientError as e:
        parallel_logger.critical(f"Failed to load secret {secret_name}: {e}")
        raise
    except Exception as e:
        parallel_logger.critical(
            f"Unexpected error loading secret {secret_name}: {e}", exc_info=True
        )
        raise


# DLT Logger instance
DLT_LOGGER_INSTANCE = DLTLogger.from_environment() if AUDIT_LOGGER_AVAILABLE else None

# --- Backend Registration ---
_parallel_backends: Dict[str, Callable] = {}
_backend_availability: Dict[str, bool] = {}


def register_backend(name: str, is_available: bool = True):
    def decorator(func: Callable):
        if name in _parallel_backends:
            parallel_logger.warning(
                f"Parallel backend '{name}' already registered. Overwriting."
            )
        _parallel_backends[name] = func
        _backend_availability[name] = is_available
        parallel_logger.info(
            f"Registered parallel backend: {name} (Available: {is_available})"
        )
        if PROMETHEUS_AVAILABLE:
            PARALLEL_METRICS["backend_availability"].labels(backend_name=name).set(
                1 if is_available else 0
            )
        return func

    return decorator


# --- Placeholder for Operator Alerting ---
def alert_ops(message: str, level: str = "CRITICAL"):
    parallel_logger.critical(f"[OPS ALERT - {level}] {message}")


# Define RLTunerConfig if it doesn't exist
class RLTunerConfig:
    """Configuration for RL-based tuning."""

    def __init__(self, learning_rate=0.001, batch_size=32, episodes=100, **kwargs):
        self.learning_rate = learning_rate
        self.batch_size = batch_size
        self.episodes = episodes
        # Store any additional config
        for key, value in kwargs.items():
            setattr(self, key, value)


# --- RL-based Concurrency Tuner ---
class RayRLlibConcurrencyTuner:
    def __init__(self, config: RLTunerConfig):
        self.config = config
        if not RLLIB_AVAILABLE or not self.config.enabled:
            parallel_logger.warning(
                "Ray RLlib not available or RL tuner disabled. Concurrency tuner will be disabled."
            )
            self.policy = None
            return

        self.policy: Optional[Policy] = None
        self.last_obs: Optional[np.ndarray] = None
        self.last_action: Optional[int] = None
        self.checkpoint_dir = self.config.checkpoint_dir
        self.reward_log = self.config.reward_log
        self.training_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.log_file_pos = 0
        self.log_lock = threading.Lock()
        self.last_heartbeat_time = time.time()

        os.makedirs(self.checkpoint_dir, exist_ok=True)
        self._init_policy()
        self._start_training_loop()

    def _init_policy(self):
        from gymnasium.spaces import Box, Discrete

        obs_space = Box(low=0, high=np.inf, shape=(7,), dtype=np.float32)
        act_space = Discrete(self.config.max_concurrency_limit)

        self.ppo_config = (
            PPOConfig()
            .environment(observation_space=obs_space, action_space=act_space)
            .rollouts(num_rollout_workers=0)
            .framework("torch")
            .training(
                train_batch_size=128,
                sgd_minibatch_size=32,
                gamma=0.95,
                lr=5e-4,
                model={"fcnet_hiddens": [64, 64]},
            )
        )

        try:
            if os.path.exists(os.path.join(self.checkpoint_dir, "policy_state.pkl")):
                self.policy = Policy.from_checkpoint(self.checkpoint_dir)
                parallel_logger.info(
                    f"Loaded RL policy from checkpoint: {self.checkpoint_dir}"
                )
            else:
                raise FileNotFoundError("No valid checkpoint found.")
        except Exception as e:
            parallel_logger.warning(
                f"Could not load policy from checkpoint (error: {e}). Initializing a new policy."
            )
            self.policy = self.ppo_config.build().get_policy()
            parallel_logger.info("Initialized new RL policy.")

    def _start_training_loop(self):
        def training_loop():
            parallel_logger.info("RL training loop started.")
            while not self.stop_event.is_set():
                try:
                    self.last_heartbeat_time = time.time()
                    if PROMETHEUS_AVAILABLE:
                        PARALLEL_METRICS["rl_tuner_heartbeat"].set(
                            self.last_heartbeat_time
                        )
                        PARALLEL_METRICS["rl_tuner_status"].set(1)

                    self.stop_event.wait(self.config.training_interval_seconds)
                    if self.stop_event.is_set():
                        break

                    experiences = self._collect_experiences()

                    if not experiences or experiences["obs"].shape[0] < 10:
                        continue

                    parallel_logger.info(
                        f"Training RL policy with {experiences['obs'].shape[0]} new experiences."
                    )
                    self.policy.learn_on_batch(experiences)

                    self.policy.export_checkpoint(self.checkpoint_dir)
                    parallel_logger.info(
                        f"RL policy checkpoint saved to {self.checkpoint_dir}"
                    )

                except Exception as e:
                    parallel_logger.error(
                        f"Error in RL training loop: {e}", exc_info=True
                    )
                    alert_ops(f"RL Training Loop Error: {e}", level="ERROR")
                    if PROMETHEUS_AVAILABLE:
                        PARALLEL_METRICS["rl_tuner_status"].set(0)

            parallel_logger.info("RL training loop stopped.")
            if PROMETHEUS_AVAILABLE:
                PARALLEL_METRICS["rl_tuner_status"].set(0)

        self.training_thread = threading.Thread(target=training_loop, daemon=True)
        self.training_thread.start()

    def _collect_experiences(self) -> Dict[str, np.ndarray]:
        experiences = {
            "obs": [],
            "actions": [],
            "rewards": [],
            "new_obs": [],
            "terminateds": [],
        }
        try:
            with self.log_lock:
                with open(self.reward_log, "r") as f:
                    f.seek(self.log_file_pos)
                    new_lines = f.readlines()
                    self.log_file_pos = f.tell()

            for line in new_lines:
                try:
                    data = json.loads(line.strip())
                    experiences["obs"].append(np.array(data["obs"], dtype=np.float32))
                    experiences["actions"].append(data["action"])
                    experiences["rewards"].append(data["reward"])
                    experiences["new_obs"].append(
                        np.zeros_like(np.array(data["obs"], dtype=np.float32))
                    )
                    experiences["terminateds"].append(True)
                except (json.JSONDecodeError, KeyError, TypeError):
                    parallel_logger.warning(
                        f"Skipping malformed log line: {line.strip()}"
                    )
                    continue

            if experiences["obs"]:
                return {
                    "obs": np.array(experiences["obs"]),
                    "actions": np.array(experiences["actions"]),
                    "rewards": np.array(experiences["rewards"]),
                    "next_obs": np.array(experiences["new_obs"]),
                    "terminateds": np.array(experiences["terminateds"]),
                }
        except FileNotFoundError:
            pass
        except Exception as e:
            parallel_logger.error(f"Failed to read RL reward log: {e}")
        return {}

    def check_liveness(self) -> bool:
        if not self.training_thread or not self.training_thread.is_alive():
            parallel_logger.error("RL training thread is not alive.")
            alert_ops(
                "RL Training Thread is dead. Concurrency tuning will fall back to heuristics.",
                level="CRITICAL",
            )
            if PROMETHEUS_AVAILABLE:
                PARALLEL_METRICS["rl_tuner_status"].set(0)
            return False

        if (
            time.time() - self.last_heartbeat_time
            > self.config.heartbeat_timeout_seconds
        ):
            parallel_logger.warning(
                f"RL training thread heartbeat is stale (last update {time.time() - self.last_heartbeat_time:.2f}s ago)."
            )
            alert_ops(
                "RL Training Thread heartbeat stale. Concurrency tuning may be degraded.",
                level="WARNING",
            )
            if PROMETHEUS_AVAILABLE:
                PARALLEL_METRICS["rl_tuner_status"].set(0)
            return False

        if PROMETHEUS_AVAILABLE:
            PARALLEL_METRICS["rl_tuner_status"].set(1)
        return True

    def get_optimal_concurrency(self, resources: Dict[str, Any], num_tasks: int) -> int:
        if not self.policy or not self.check_liveness():
            return auto_tune_concurrency_heuristic(num_tasks)

        obs = np.array(
            [
                resources.get("cpu_cores", os.cpu_count() or 1),
                resources.get("memory_gb", 1),
                resources.get("gpus", 0),
                num_tasks,
                resources.get("last_throughput", 0),
                resources.get("last_failures", 0),
                self.last_action or 1,
            ],
            dtype=np.float32,
        )

        action, _, _ = self.policy.compute_single_action(obs, explore=True)
        concurrency = int(
            np.clip(action + 1, 1, min(num_tasks, self.config.max_concurrency_limit))
        )

        self.last_obs = obs
        self.last_action = concurrency
        return concurrency

    def record_feedback(self, throughput: float, failures: int):
        if (
            self.last_obs is not None
            and self.last_action is not None
            and self.policy is not None
        ):
            reward = throughput - (failures * 2.0)

            log_entry = {
                "timestamp": datetime.now().isoformat(),
                "obs": self.last_obs.tolist(),
                "action": self.last_action,
                "reward": reward,
                "throughput": throughput,
                "failures": failures,
            }

            try:
                with self.log_lock:
                    with open(self.reward_log, "a") as f:
                        f.write(json.dumps(log_entry) + "\n")
            except IOError as e:
                parallel_logger.error(f"Failed to write to RL reward log: {e}")
                alert_ops(f"Failed to write to RL reward log: {e}", level="ERROR")

            parallel_logger.info(
                f"RL feedback recorded: action={self.last_action}, reward={reward:.2f}"
            )
            self.last_obs, self.last_action = None, None

    def stop(self):
        self.stop_event.set()
        if self.training_thread and self.training_thread.is_alive():
            self.training_thread.join(timeout=5)
            if self.training_thread.is_alive():
                parallel_logger.warning(
                    "RL training thread did not shut down gracefully."
                )
        if PROMETHEUS_AVAILABLE:
            PARALLEL_METRICS["rl_tuner_status"].set(0)


RL_TUNER: Optional[RayRLlibConcurrencyTuner] = None
if GLOBAL_PARALLEL_CONFIG.rl_tuner.enabled and RLLIB_AVAILABLE:
    RL_TUNER = RayRLlibConcurrencyTuner(GLOBAL_PARALLEL_CONFIG.rl_tuner)
    atexit.register(lambda: RL_TUNER.stop() if RL_TUNER else None)


def get_available_resources() -> Dict[str, Any]:
    return {
        "cpu_cores": os.cpu_count() or 1,
        "memory_gb": round(psutil.virtual_memory().available / (1024**3), 2),
        "gpus": (
            len(os.environ.get("NVIDIA_VISIBLE_DEVICES", "").split(","))
            if "NVIDIA_VISIBLE_DEVICES" in os.environ
            else 0
        ),
    }


def auto_tune_concurrency(num_tasks: int) -> int:
    if RL_TUNER and RL_TUNER.config.enabled and RL_TUNER.check_liveness():
        resources = get_available_resources()
        return RL_TUNER.get_optimal_concurrency(resources, num_tasks)
    else:
        return auto_tune_concurrency_heuristic(num_tasks)


def auto_tune_concurrency_heuristic(num_tasks: int) -> int:
    resources = get_available_resources()
    concurrency = min(resources["cpu_cores"], num_tasks)
    if GLOBAL_PARALLEL_CONFIG.max_local_workers:
        concurrency = min(concurrency, GLOBAL_PARALLEL_CONFIG.max_local_workers)
    return max(concurrency, 1)


class ProgressReporter:
    def __init__(self, total_tasks: int, job_id: Optional[str] = None):
        self.total_tasks = total_tasks
        self.completed_tasks = 0
        self.failed_tasks = 0
        self.start_time = time.time()
        self.job_id = job_id or f"job-{uuid.uuid4().hex[:8]}"
        self.last_throughput = 0
        self.last_failures = 0
        self._lock = threading.Lock()
        parallel_logger.info(f"Starting job {self.job_id} with {total_tasks} tasks.")

    def task_completed(self, success: bool = True, job_latency: Optional[float] = None):
        with self._lock:
            if success:
                self.completed_tasks += 1
                if PROMETHEUS_AVAILABLE:
                    PARALLEL_METRICS["simulation_tasks_total"].labels(
                        status="completed"
                    ).inc()
            else:
                self.failed_tasks += 1
                if PROMETHEUS_AVAILABLE:
                    PARALLEL_METRICS["simulation_tasks_total"].labels(
                        status="failed"
                    ).inc()

            if PROMETHEUS_AVAILABLE and job_latency is not None:
                PARALLEL_METRICS["job_latency"].labels(
                    backend_name="local_asyncio"
                ).observe(job_latency)

            done = self.completed_tasks + self.failed_tasks
            percentage = (done / self.total_tasks) * 100

            elapsed = time.time() - self.start_time
            throughput = self.completed_tasks / elapsed if elapsed > 0 else 0

            eta_seconds = (
                ((self.total_tasks - done) / throughput) if throughput > 0 else 0
            )
            eta = (
                time.strftime("%H:%M:%S", time.gmtime(eta_seconds))
                if throughput > 0
                else "N/A"
            )

            parallel_logger.info(
                f"Progress({self.job_id}): {done}/{self.total_tasks} ({percentage:.1f}%) | "
                f"Success: {self.completed_tasks}, Failed: {self.failed_tasks} | "
                f"Throughput: {throughput:.2f} tasks/s | ETA: {eta}"
            )

    def finish(self):
        elapsed = time.time() - self.start_time
        throughput = self.completed_tasks / elapsed if elapsed > 0 else 0
        self.last_throughput = throughput
        self.last_failures = self.failed_tasks
        parallel_logger.info(
            f"Job {self.job_id} finished in {elapsed:.2f}s. "
            f"Final throughput: {throughput:.2f} tasks/s. "
            f"Success: {self.completed_tasks}, Failed: {self.failed_tasks}."
        )
        if RL_TUNER and RL_TUNER.config.enabled:
            RL_TUNER.record_feedback(self.last_throughput, self.last_failures)


# --- Backend Implementations ---


@register_backend("local_asyncio", is_available=True)
async def execute_local_asyncio(
    simulation_function: Callable, configurations: List[Dict[str, Any]], **kwargs
) -> List[Dict[str, Any]]:
    num_workers = auto_tune_concurrency(len(configurations))
    parallel_logger.info(f"Using 'local_asyncio' backend with {num_workers} workers.")
    semaphore = asyncio.Semaphore(num_workers)
    reporter = ProgressReporter(len(configurations))

    async def run_task(config):
        async with semaphore:
            start_task_time = time.time()
            try:
                # 5. Resource Limits: Enforce resource limits for local_asyncio
                process = psutil.Process()
                mem_info_start = process.memory_info()

                result = await simulation_function(config)

                mem_info_end = process.memory_info()
                if mem_info_end.rss - mem_info_start.rss > 2 * 1024**3:
                    raise RuntimeError("Task exceeded memory limit.")

                reporter.task_completed(
                    success=True, job_latency=time.time() - start_task_time
                )
                return result
            except Exception as e:
                parallel_logger.error(
                    f"Task failed for config {config.get('id', 'N/A')}: {e}",
                    exc_info=True,
                )
                reporter.task_completed(
                    success=False, job_latency=time.time() - start_task_time
                )
                return {"status": "FAILED", "error": str(e), "config": config}

    tasks = [run_task(conf) for conf in configurations]
    results = await asyncio.gather(*tasks)
    reporter.finish()
    return results


@register_backend("kubernetes", is_available=K8S_AVAILABLE)
async def execute_kubernetes(
    simulation_function: Callable,
    configurations: List[Dict[str, Any]],
    config: ParallelConfig,
    **kwargs,
) -> List[Dict[str, Any]]:
    if not K8S_AVAILABLE:
        raise RuntimeError(
            "Kubernetes client not installed. Cannot use Kubernetes backend."
        )

    try:
        try:
            k8s_config.load_incluster_config()
            parallel_logger.info("Loaded in-cluster Kubernetes config.")
        except k8s_config.ConfigException:
            k8s_config.load_kube_config()
            parallel_logger.info("Loaded Kubernetes config from kube_config.")

        core_v1 = k8s_client.CoreV1Api()
        batch_v1 = k8s_client.BatchV1Api()
    except Exception as e:
        alert_ops(f"Failed to initialize Kubernetes client: {e}", level="CRITICAL")
        raise RuntimeError(f"Failed to initialize Kubernetes client: {e}")

    namespace = kwargs.get("namespace", config.backend_configs.kubernetes_namespace)
    image = kwargs.get("image", config.backend_configs.kubernetes_image)
    if not image:
        alert_ops(
            "Kubernetes simulation image not configured. Cannot run K8s jobs.",
            level="CRITICAL",
        )
        raise ValueError(
            "Kubernetes simulation image (K8S_SIMULATION_IMAGE env var or config) must be set."
        )

    job_timeout = kwargs.get("timeout", config.backend_configs.kubernetes_job_timeout)
    reporter = ProgressReporter(len(configurations))

    async def run_k8s_job(cfg: Dict, job_idx: int):
        job_id = f"{reporter.job_id}-{job_idx}"
        job_name = f"sim-job-{job_id}"

        command = cfg.get("command", ["python", "/app/runner.py"])
        env_vars = [
            k8s_client.V1EnvVar(name="SIMULATION_CONFIG_JSON", value=json.dumps(cfg))
        ]
        resources = cfg.get(
            "resources",
            {
                "requests": {"cpu": "1", "memory": "1Gi"},
                "limits": {"cpu": "2", "memory": "2Gi"},
            },
        )

        container = k8s_client.V1Container(
            name="simulation-pod",
            image=image,
            command=command,
            env=env_vars,
            resources=k8s_client.V1ResourceRequirements(**resources),
        )

        pod_template = k8s_client.V1PodTemplateSpec(
            metadata=k8s_client.V1ObjectMeta(labels={"job-name": job_name}),
            spec=k8s_client.V1PodSpec(restart_policy="Never", containers=[container]),
        )

        job_spec = k8s_client.V1JobSpec(
            template=pod_template, backoff_limit=1, ttl_seconds_after_finished=300
        )
        job_body = k8s_client.V1Job(
            api_version="batch/v1",
            kind="Job",
            metadata=k8s_client.V1ObjectMeta(name=job_name),
            spec=job_spec,
        )

        try:
            start_job_time = time.time()
            batch_v1.create_namespaced_job(body=job_body, namespace=namespace)
            parallel_logger.info(
                f"Submitted Kubernetes Job '{job_name}' in namespace '{namespace}'."
            )

            start_time = time.time()
            while time.time() - start_time < job_timeout:
                await asyncio.sleep(15)
                job_status = batch_v1.read_namespaced_job_status(job_name, namespace)

                if job_status.status.succeeded:
                    pod_list = core_v1.list_namespaced_pod(
                        namespace, label_selector=f"job-name={job_name}", limit=1
                    )
                    if not pod_list.items:
                        parallel_logger.error(
                            f"Could not find pod for successful job {job_name}"
                        )
                        reporter.task_completed(success=False)
                        return {
                            "status": "RESULT_ERROR",
                            "job_name": job_name,
                            "error": "Could not find pod to retrieve logs.",
                        }
                    pod_name = pod_list.items[0].metadata.name
                    logs = core_v1.read_namespaced_pod_log(pod_name, namespace)

                    try:
                        result_json = json.loads(logs)
                        reporter.task_completed(
                            success=True, job_latency=time.time() - start_job_time
                        )
                        return result_json
                    except json.JSONDecodeError:
                        reporter.task_completed(
                            success=False, job_latency=time.time() - start_job_time
                        )
                        parallel_logger.error(
                            f"Failed to parse JSON from pod logs for job {job_name}. Logs: {logs[:200]}..."
                        )
                        return {
                            "status": "RESULT_ERROR",
                            "job_name": job_name,
                            "error": "Failed to parse JSON from pod logs",
                            "logs_snippet": logs[:500],
                        }

                if job_status.status.failed:
                    reason = (
                        job_status.status.conditions[0].message
                        if job_status.status.conditions
                        else "Unknown failure"
                    )
                    reporter.task_completed(
                        success=False, job_latency=time.time() - start_job_time
                    )
                    parallel_logger.warning(
                        f"Kubernetes Job '{job_name}' failed. Reason: {reason}"
                    )
                    return {
                        "status": "FAILED",
                        "job_name": job_name,
                        "reason": reason,
                        "config": cfg,
                    }

            reporter.task_completed(
                success=False, job_latency=time.time() - start_job_time
            )
            parallel_logger.warning(
                f"Kubernetes Job '{job_name}' timed out after {job_timeout} seconds."
            )
            return {"status": "TIMEOUT", "job_name": job_name, "config": cfg}

        except K8sApiException as e:
            reporter.task_completed(
                success=False, job_latency=time.time() - start_job_time
            )
            parallel_logger.error(
                f"Kubernetes API error for job {job_name}: {e.reason}", exc_info=True
            )
            alert_ops(
                f"Kubernetes API error for job {job_name}: {e.reason}", level="ERROR"
            )
            if AUDIT_LOGGER_AVAILABLE:
                dlt_logger = DLTLogger.from_environment()
                await dlt_logger.add_entry(
                    kind="parallel",
                    name="k8s_job_error",
                    detail={"job_name": job_name, "error": str(e)},
                    agent_id="parallel_k8s",
                )
            return {
                "status": "ERROR",
                "job_name": job_name,
                "error": f"Kubernetes API error: {e.reason}",
                "config": cfg,
            }
        except Exception as e:
            reporter.task_completed(
                success=False, job_latency=time.time() - start_job_time
            )
            parallel_logger.error(
                f"Unexpected error running K8s job {job_name}: {e}", exc_info=True
            )
            alert_ops(
                f"Unexpected error running K8s job {job_name}: {e}", level="ERROR"
            )
            if AUDIT_LOGGER_AVAILABLE:
                dlt_logger = DLTLogger.from_environment()
                await dlt_logger.add_entry(
                    kind="parallel",
                    name="k8s_job_error",
                    detail={"job_name": job_name, "error": str(e)},
                    agent_id="parallel_k8s",
                )
            return {
                "status": "ERROR",
                "job_name": job_name,
                "error": f"Unexpected error: {e}",
                "config": cfg,
            }

    tasks = [run_k8s_job(conf, i) for i, conf in enumerate(configurations)]
    results = await asyncio.gather(*tasks)
    reporter.finish()
    return results


@register_backend("aws_batch", is_available=BOTO3_AVAILABLE)
async def execute_aws_batch(
    simulation_function: Callable,
    configurations: List[Dict[str, Any]],
    config: ParallelConfig,
    **kwargs,
) -> List[Dict[str, Any]]:
    if not BOTO3_AVAILABLE:
        raise RuntimeError("Boto3 is not installed. Cannot use AWS Batch backend.")

    job_queue = kwargs.get("job_queue", config.backend_configs.aws_batch_job_queue)
    job_definition = kwargs.get(
        "job_definition", config.backend_configs.aws_batch_job_definition
    )
    s3_bucket = kwargs.get("s3_bucket", config.backend_configs.aws_batch_s3_bucket)

    if not all([job_queue, job_definition, s3_bucket]):
        alert_ops(
            "Missing required AWS Batch configuration (job_queue, job_definition, s3_bucket).",
            level="CRITICAL",
        )
        raise ValueError(
            "Missing required AWS Batch configuration (job_queue, job_definition, s3_bucket)."
        )

    try:
        batch_client = boto3.client("batch")
        s3_client = boto3.client("s3")
    except Exception as e:
        alert_ops(f"Failed to initialize AWS clients (Batch/S3): {e}", level="CRITICAL")
        raise RuntimeError(f"Failed to initialize AWS clients: {e}")

    reporter = ProgressReporter(len(configurations))
    job_timeout = kwargs.get("timeout", config.backend_configs.aws_batch_job_timeout)

    submitted_jobs = {}
    for i, cfg in enumerate(configurations):
        job_id = f"{reporter.job_id}-{i}"
        job_name = f"sim-job-{job_id}"
        s3_result_key = f"results/{reporter.job_id}/{job_id}.json"

        container_overrides = {
            "environment": [
                {"name": "SIMULATION_CONFIG_JSON", "value": json.dumps(cfg)},
                {"name": "S3_RESULT_BUCKET", "value": s3_bucket},
                {"name": "S3_RESULT_KEY", "value": s3_result_key},
            ]
        }

        try:
            response = batch_client.submit_job(
                jobName=job_name,
                jobQueue=job_queue,
                jobDefinition=job_definition,
                containerOverrides=container_overrides,
            )
            submitted_jobs[response["jobId"]] = (s3_result_key, cfg)
            parallel_logger.info(
                f"Submitted AWS Batch Job '{job_name}' with ID: {response['jobId']}"
            )
            if AUDIT_LOGGER_AVAILABLE:
                dlt_logger = DLTLogger.from_environment()
                await dlt_logger.add_entry(
                    kind="parallel",
                    name="aws_job_submission",
                    detail={"job_name": job_name, "job_id": response["jobId"]},
                    agent_id="parallel_aws",
                )
        except ClientError as e:
            reporter.task_completed(success=False)
            parallel_logger.error(f"Failed to submit AWS Batch job for config {i}: {e}")
            alert_ops(
                f"Failed to submit AWS Batch job for config {i}: {e}", level="ERROR"
            )
            if AUDIT_LOGGER_AVAILABLE:
                dlt_logger = DLTLogger.from_environment()
                await dlt_logger.add_entry(
                    kind="parallel",
                    name="aws_job_error",
                    detail={"job_name": job_name, "error": str(e)},
                    agent_id="parallel_aws",
                )

    results = []
    start_time = time.time()
    while submitted_jobs and time.time() - start_time < job_timeout:
        await asyncio.sleep(30)
        job_ids_to_check = list(submitted_jobs.keys())

        try:
            desc = batch_client.describe_jobs(jobs=job_ids_to_check)
            for job in desc["jobs"]:
                job_id = job["jobId"]
                if job["status"] == "SUCCEEDED":
                    s3_key, _ = submitted_jobs.pop(job_id)
                    try:
                        obj = s3_client.get_object(Bucket=s3_bucket, Key=s3_key)
                        result_data = json.loads(obj["Body"].read())
                        results.append(result_data)
                        reporter.task_completed(success=True)
                        s3_client.delete_object(Bucket=s3_bucket, Key=s3_key)
                        if AUDIT_LOGGER_AVAILABLE:
                            dlt_logger = DLTLogger.from_environment()
                            await dlt_logger.add_entry(
                                kind="parallel",
                                name="aws_job_success",
                                detail={"job_id": job_id},
                                agent_id="parallel_aws",
                            )
                    except (ClientError, json.JSONDecodeError) as e:
                        results.append(
                            {"status": "RESULT_ERROR", "error": str(e), "jobId": job_id}
                        )
                        reporter.task_completed(success=False)
                        parallel_logger.error(
                            f"Failed to retrieve/parse result from S3 for job {job_id}: {e}"
                        )
                        alert_ops(
                            f"Failed to retrieve/parse S3 result for job {job_id}: {e}",
                            level="ERROR",
                        )
                        if AUDIT_LOGGER_AVAILABLE:
                            dlt_logger = DLTLogger.from_environment()
                            await dlt_logger.add_entry(
                                kind="parallel",
                                name="aws_job_error",
                                detail={"job_id": job_id, "error": str(e)},
                                agent_id="parallel_aws",
                            )
                elif job["status"] == "FAILED":
                    _, cfg = submitted_jobs.pop(job_id)
                    reason = job.get("statusReason", "Unknown failure reason.")
                    results.append(
                        {"status": "FAILED", "reason": reason, "config": cfg}
                    )
                    reporter.task_completed(success=False)
                    parallel_logger.warning(
                        f"AWS Batch Job '{job_id}' failed. Reason: {reason}"
                    )
                    alert_ops(
                        f"AWS Batch Job '{job_id}' failed. Reason: {reason}",
                        level="WARNING",
                    )
                    if AUDIT_LOGGER_AVAILABLE:
                        dlt_logger = DLTLogger.from_environment()
                        await dlt_logger.add_entry(
                            kind="parallel",
                            name="aws_job_failure",
                            detail={"job_id": job_id, "reason": reason},
                            agent_id="parallel_aws",
                        )
        except ClientError as e:
            parallel_logger.error(
                f"Error describing AWS Batch jobs: {e}. Assuming jobs failed.",
                exc_info=True,
            )
            alert_ops(
                f"Error describing AWS Batch jobs: {e}. Assuming jobs failed.",
                level="ERROR",
            )
            if AUDIT_LOGGER_AVAILABLE:
                dlt_logger = DLTLogger.from_environment()
                await dlt_logger.add_entry(
                    kind="parallel",
                    name="aws_job_error",
                    detail={"error": str(e)},
                    agent_id="parallel_aws",
                )
            for job_id in job_ids_to_check:
                if job_id in submitted_jobs:
                    _, cfg = submitted_jobs.pop(job_id)
                    results.append(
                        {"status": "MONITORING_ERROR", "jobId": job_id, "config": cfg}
                    )
                    reporter.task_completed(success=False)

    if submitted_jobs:
        for job_id, (_, cfg) in submitted_jobs.items():
            results.append({"status": "TIMEOUT", "jobId": job_id, "config": cfg})
            reporter.task_completed(success=False)
            parallel_logger.warning(
                f"AWS Batch Job '{job_id}' timed out after {job_timeout} seconds."
            )
            alert_ops(f"AWS Batch Job '{job_id}' timed out.", level="WARNING")
            if AUDIT_LOGGER_AVAILABLE:
                dlt_logger = DLTLogger.from_environment()
                await dlt_logger.add_entry(
                    kind="parallel",
                    name="aws_job_timeout",
                    detail={"job_id": job_id},
                    agent_id="parallel_aws",
                )

    reporter.finish()
    return results


async def run_parallel_simulations(
    simulation_function: Callable,
    configurations: List[Dict[str, Any]],
    parallel_backend: Optional[str] = None,
    **kwargs,
) -> List[Dict[str, Any]]:
    selected_backend = parallel_backend or GLOBAL_PARALLEL_CONFIG.default_backend
    executor = _parallel_backends.get(selected_backend)

    available_backends = [
        name for name, is_avail in _backend_availability.items() if is_avail
    ]
    if not available_backends:
        alert_ops(
            "No parallel execution backends are available. Aborting startup.",
            level="CRITICAL",
        )
        raise RuntimeError(
            "No parallel execution backends are available. Please check dependencies and configuration."
        )

    if not executor or not _backend_availability.get(selected_backend, False):
        parallel_logger.error(
            f"Selected backend '{selected_backend}' is not available or unknown. Falling back to default: '{GLOBAL_PARALLEL_CONFIG.default_backend}'."
        )
        alert_ops(
            f"Selected backend '{selected_backend}' is unavailable. Falling back to '{GLOBAL_PARALLEL_CONFIG.default_backend}'.",
            level="WARNING",
        )
        selected_backend = GLOBAL_PARALLEL_CONFIG.default_backend
        executor = _parallel_backends.get(selected_backend)
        if not executor or not _backend_availability.get(selected_backend, False):
            alert_ops(
                f"Default backend '{selected_backend}' is also unavailable. Aborting.",
                level="CRITICAL",
            )
            raise RuntimeError(
                f"Neither selected backend '{parallel_backend}' nor default backend '{selected_backend}' is available."
            )

    if PROMETHEUS_AVAILABLE:
        PARALLEL_METRICS["backend_usage_total"].labels(
            backend_name=selected_backend
        ).inc()

    parallel_logger.info(
        f"Executing {len(configurations)} tasks with backend: '{selected_backend}'."
    )
    start_time = time.time()

    results = await executor(
        simulation_function, configurations, config=GLOBAL_PARALLEL_CONFIG, **kwargs
    )

    end_time = time.time()
    parallel_logger.info(f"All tasks completed in {end_time - start_time:.2f} seconds.")
    return results


if __name__ == "__main__":

    async def example_simulation(config: Dict[str, Any]) -> Dict[str, Any]:
        sim_id = config.get("id", "unknown")
        duration = config.get("duration", 1)
        parallel_logger.info(f"Running example simulation {sim_id} for {duration}s...")
        await asyncio.sleep(duration)
        if config.get("should_fail", False):
            raise ValueError(f"Simulation {sim_id} was designed to fail.")
        return {
            "status": "completed",
            "id": sim_id,
            "output": f"result_{sim_id}",
            "duration": duration,
        }

    async def main():
        import argparse

        parser = argparse.ArgumentParser(description="Parallel Simulation Runner")
        parser.add_argument(
            "--backend",
            type=str,
            help="Backend to use (e.g., local_asyncio, kubernetes, aws_batch). Overrides default config.",
        )
        parser.add_argument(
            "--tasks", type=int, default=10, help="Number of tasks to run"
        )
        parser.add_argument(
            "--fail-rate",
            type=float,
            default=0.1,
            help="Approximate fraction of tasks to fail",
        )
        args = parser.parse_args()

        configs = []
        for i in range(args.tasks):
            should_fail = np.random.random() < args.fail_rate
            configs.append(
                {
                    "id": i,
                    "duration": round(np.random.uniform(0.5, 2.0), 2),
                    "should_fail": should_fail,
                }
            )

        backend_to_use = (
            args.backend if args.backend else GLOBAL_PARALLEL_CONFIG.default_backend
        )
        parallel_logger.info(
            f"Starting {args.tasks} simulations with backend: {backend_to_use}"
        )

        results = await run_parallel_simulations(
            simulation_function=example_simulation,
            configurations=configs,
            parallel_backend=backend_to_use,
        )

        parallel_logger.info("--- All simulations finished. Final Results ---")
        successful_runs = sum(1 for r in results if r.get("status") == "completed")
        failed_runs = len(results) - successful_runs
        parallel_logger.info(f"Total successful: {successful_runs}/{len(results)}")
        parallel_logger.info(f"Total failed/error: {failed_runs}/{len(results)}")

    asyncio.run(main())
