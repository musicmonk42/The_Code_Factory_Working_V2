# plugins/gcp_cloud_run_runner_plugin.py

import os
import asyncio
import json
import logging
import time
import tempfile
import uuid
import random
from typing import Dict, Any, Callable, Optional, List
from pydantic import BaseModel, Field, validator, ValidationError
import re
import aiohttp
import tarfile
from pathlib import Path

# Conditional imports for Google Cloud Libraries
try:
    from google.cloud import storage
    from google.cloud import run_v2
    from google.cloud.logging_v2 import Client as LoggingClient
    from google.api_core.exceptions import (
        GoogleAPIError,
        NotFound,
        Conflict,
        QuotaExceeded,
        InvalidArgument,
    )
    from google.oauth2 import service_account

    try:
        # Optional: ADC fallback
        from google.auth import default as google_auth_default  # type: ignore
    except Exception:
        google_auth_default = None  # type: ignore
    from tenacity import (
        retry,
        stop_after_attempt,
        wait_exponential,
        retry_if_exception_type,
    )

    GCP_AVAILABLE = True
except ImportError:
    storage = None
    run_v2 = None
    LoggingClient = None
    GoogleAPIError = type("GoogleAPIError", (Exception,), {})
    NotFound = type("NotFound", (Exception,), {})
    Conflict = type("Conflict", (Exception,), {})
    QuotaExceeded = type("QuotaExceeded", (Exception,), {})
    InvalidArgument = type("InvalidArgument", (Exception,), {})
    service_account = None
    google_auth_default = None  # type: ignore

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

    GCP_AVAILABLE = False

# Logger setup (plain formatting; production systems can route to JSON/structured externally)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s - [%(levelname)s] - %(name)s - %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# Load PLUGIN_MANIFEST from config file
CONFIG_FILE = os.path.join(
    os.path.dirname(__file__), "configs/gcp_cloud_run_config.json"
)
if os.path.exists(CONFIG_FILE):
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            PLUGIN_MANIFEST = json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to load config from {CONFIG_FILE}: {e}")
        PLUGIN_MANIFEST = {}
else:
    PLUGIN_MANIFEST = {
        "name": "GCPCloudRunRunnerPlugin",
        "version": "1.2.0",
        "description": "Executes and monitors Google Cloud Run jobs for SFE simulations with robust error handling and metrics.",
        "author": "Self-Fixing Engineer Team",
        "capabilities": [
            "gcp_cloud_run_execution",
            "cloud_bursting",
            "distributed_simulation",
        ],
        "permissions_required": [
            "gcp_cloud_run_invoke",
            "gcp_gcs_read",
            "gcp_gcs_write",
            "gcp_logging_read",
        ],
        "compatibility": {
            "min_sim_runner_version": "1.0.0",
            "max_sim_runner_version": "2.0.0",
        },
        "entry_points": {
            "run_cloud_run_job": {
                "description": "Submits and monitors a Google Cloud Run job for a simulation task.",
                "parameters": ["job_config", "project_root", "output_dir"],
            }
        },
        "health_check": "plugin_health",
        "api_version": "v1",
        "license": "MIT",
        "homepage": "https://github.com/self-fixing-engineer/gcp-cloud-run-plugin",
        "tags": ["gcp", "cloud_run", "serverless", "distributed", "simulation_runner"],
    }

# Prometheus Metrics (optional, safe creators)
PROMETHEUS_AVAILABLE = False
try:
    from prometheus_client import Counter, Histogram  # default REGISTRY

    PROMETHEUS_AVAILABLE = True
except Exception as _e:
    logger.warning(f"Prometheus client not available. Metrics disabled: {_e}")

_METRICS: Dict[str, Any] = {}


def _noop_counter():
    class _Noop:
        def labels(self, *args, **kwargs):
            return self

        def inc(self, *args, **kwargs):
            pass

    return _Noop()


def _noop_hist():
    class _Noop:
        def labels(self, *args, **kwargs):
            return self

        def observe(self, *args, **kwargs):
            pass

    return _Noop()


def _safe_counter(name: str, doc: str, labelnames: tuple = ()):
    if not PROMETHEUS_AVAILABLE:
        return _noop_counter()
    if name in _METRICS:
        return _METRICS[name]
    try:
        m = Counter(name, doc, labelnames=labelnames)
        _METRICS[name] = m
        return m
    except ValueError:
        logger.warning(
            f"Metric '{name}' already registered. Using no-op for this instance."
        )
        m = _noop_counter()
        _METRICS[name] = m
        return m


def _safe_hist(name: str, doc: str, labelnames: tuple = (), buckets=None):
    if not PROMETHEUS_AVAILABLE:
        return _noop_hist()
    if name in _METRICS:
        return _METRICS[name]
    try:
        m = Histogram(name, doc, labelnames=labelnames, buckets=buckets or Histogram.DEFAULT_BUCKETS)  # type: ignore
        _METRICS[name] = m
        return m
    except ValueError:
        logger.warning(
            f"Metric '{name}' already registered. Using no-op for this instance."
        )
        m = _noop_hist()
        _METRICS[name] = m
        return m


# Low-cardinality labels
JOB_SUBMISSIONS_TOTAL = _safe_counter(
    "gcp_cloud_run_job_submissions_total",
    "Total Cloud Run job submissions",
    ("status",),
)
JOB_DURATION_SECONDS = _safe_hist(
    "gcp_cloud_run_job_duration_seconds", "Duration of Cloud Run jobs", ("status",)
)
GCS_OPERATION_LATENCY = _safe_hist(
    "gcp_gcs_operation_latency_seconds", "Latency of GCS operations", ("operation",)
)
CREDENTIAL_SOURCE_TOTAL = _safe_counter(
    "gcp_credential_source_total", "Credential source usage", ("source",)
)

# Pydantic models for validation
_BUCKET_RE = re.compile(
    r"^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$"
)  # simplified GCS bucket name
_LOCATION_RE = re.compile(r"^[a-z]+(?:-[a-z0-9]+)+$")  # e.g., us-central1


class EnvVar(BaseModel):
    name: str = Field(..., min_length=1)
    value: str = Field(...)


class JobConfig(BaseModel):
    project_id: str = Field(..., description="Google Cloud Project ID")
    location: str = Field(..., description="GCP region (e.g., us-central1)")
    job_definition_name: Optional[str] = Field(
        None, description="Full resource name of existing Cloud Run job"
    )
    image_url: str = Field(
        ..., description="Docker image URL (e.g., gcr.io/my-project/image:latest)"
    )
    command: Optional[List[str]] = Field(
        None, description="Command to run in the container"
    )
    args: Optional[List[str]] = Field(
        None, description="Arguments for the container command"
    )
    env_vars: List[EnvVar] = Field(
        default_factory=list, description="Environment variables for the container"
    )
    cpu_limit: Optional[str] = Field(None, description="CPU limit (e.g., 1 or 1000m)")
    memory_limit: Optional[str] = Field(None, description="Memory limit (e.g., 512Mi)")
    timeout_seconds: int = Field(
        600, ge=1, description="Max job execution time in seconds"
    )
    max_retries: int = Field(0, ge=0, description="Max container retries")
    parallelism: int = Field(1, ge=1, description="Number of concurrent tasks")
    task_count: int = Field(1, ge=1, description="Total tasks to run")
    input_gcs_bucket: str = Field(..., description="GCS bucket for input artifacts")
    output_gcs_bucket: Optional[str] = Field(
        None, description="GCS bucket for output results"
    )
    output_gcs_key_prefix: str = Field(
        "cloud_run_results", description="Prefix for output in GCS"
    )
    output_filename: str = Field(
        "result.json", description="Filename for output artifact"
    )
    cleanup_gcs_input: bool = Field(
        True, description="Delete input artifact after job completion"
    )
    retain_temp_archive: bool = Field(
        False, description="Retain local archive after upload"
    )

    @validator("image_url")
    def validate_image_url(cls, v):
        # Allow gcr.io and {region}-docker.pkg.dev with multiple path segments and optional tag/digest
        image_re = re.compile(
            r"^((gcr\.io)|([a-z0-9-]+-docker\.pkg\.dev))/[a-z0-9\-_.]+(?:/[a-z0-9\-_.]+)+(@sha256:[a-f0-9]{64}|:[\w.\-]+)?$"
        )
        if not image_re.match(v):
            raise ValueError(
                "Image URL must be from a trusted GCP registry (gcr.io or *-docker.pkg.dev)"
            )
        return v

    @validator("project_id")
    def validate_project_id(cls, v):
        if not re.match(r"^[a-z][a-z0-9-]{4,61}[a-z0-9]$", v):
            raise ValueError("Invalid GCP project_id format")
        return v

    @validator("location")
    def validate_location(cls, v):
        if not _LOCATION_RE.match(v):
            raise ValueError("Invalid GCP location format (e.g., us-central1)")
        return v

    @validator("input_gcs_bucket", "output_gcs_bucket", always=True)
    def validate_bucket(cls, v):
        if v is None:
            return v
        if not _bucket_valid(v):
            raise ValueError("Invalid GCS bucket name")
        return v


def _bucket_valid(name: str) -> bool:
    return bool(_BUCKET_RE.match(name)) and not (
        ".." in name or ".-" in name or "-." in name
    )


# Secure credential loading (vault integration example)
async def _load_credentials_from_vault() -> Optional["service_account.Credentials"]:
    if not GCP_AVAILABLE or service_account is None:
        return None
    vault_url = os.environ.get("VAULT_URL")
    vault_token = os.environ.get("VAULT_TOKEN")
    if not vault_url or not vault_token:
        logger.info("Vault not configured. Skipping vault credentials.")
        return None
    # Enforce HTTPS
    if not vault_url.lower().startswith("https://"):
        logger.warning(
            "VAULT_URL is not HTTPS. Refusing to fetch credentials over insecure channel."
        )
        return None
    timeout = aiohttp.ClientTimeout(total=8)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        headers = {"X-Vault-Token": vault_token}
        try:
            # Expecting KV v2 style payload containing the service account JSON under data.data.sa_json
            async with session.get(
                f"{vault_url.rstrip('/')}/v1/secret/data/gcp-credentials",
                headers=headers,
            ) as response:
                response.raise_for_status()
                payload = await response.json()
                data = payload.get("data", {}).get("data", {})
                sa_json = data.get("sa_json")
                if isinstance(sa_json, dict):
                    creds = service_account.Credentials.from_service_account_info(
                        sa_json
                    )
                    CREDENTIAL_SOURCE_TOTAL.labels(source="vault").inc()
                    return creds
                creds_path = data.get("credentials_path")
                if creds_path and os.path.exists(creds_path):
                    creds = service_account.Credentials.from_service_account_file(
                        creds_path
                    )
                    CREDENTIAL_SOURCE_TOTAL.labels(source="vault").inc()
                    return creds
                logger.warning(
                    "Vault response did not include usable credentials (sa_json or credentials_path)."
                )
        except Exception as e:
            logger.error(f"Failed to load credentials from vault: {e}")
    return None


def _load_credentials_local() -> Optional["service_account.Credentials"]:
    if not GCP_AVAILABLE or service_account is None:
        return None
    path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    if path and os.path.exists(path):
        try:
            creds = service_account.Credentials.from_service_account_file(path)
            CREDENTIAL_SOURCE_TOTAL.labels(source="file").inc()
            return creds
        except Exception as e:
            logger.error(
                f"Failed to load GOOGLE_APPLICATION_CREDENTIALS from {path}: {e}"
            )
    # ADC fallback
    if google_auth_default:
        try:
            creds, _ = google_auth_default()
            CREDENTIAL_SOURCE_TOTAL.labels(source="adc").inc()
            return creds
        except Exception:
            pass
    CREDENTIAL_SOURCE_TOTAL.labels(source="none").inc()
    return None


async def _get_credentials() -> Optional["service_account.Credentials"]:
    creds = await _load_credentials_from_vault()
    if creds:
        return creds
    return _load_credentials_local()


# --- Plugin Health Check ---
async def plugin_health() -> Dict[str, Any]:
    """
    Performs a health check on the plugin's Google Cloud dependencies and configuration.
    """
    status = "ok"
    details: List[str] = []

    if not GCP_AVAILABLE:
        status = "error"
        details.append(
            "Google Cloud client libraries not found. GCP Cloud Run execution is impossible."
        )
        logger.error(details[-1])
        return {"status": status, "details": details}

    # Project/location from env for health scope
    project_id = os.environ.get("GCP_PROJECT_ID")
    location = os.environ.get("GCP_LOCATION")
    if not project_id or not location:
        status = "degraded"
        details.append(
            "GCP_PROJECT_ID or GCP_LOCATION not set; limited health checks will be performed."
        )

    # Check credentials
    credentials = await _get_credentials()
    if not credentials:
        status = "error"
        details.append("Google Cloud credentials not configured")
        logger.error(details[-1])
        return {"status": status, "details": details}
    details.append("Google Cloud credentials loaded successfully.")

    # Check GCS connectivity
    try:
        gcs_client = storage.Client(credentials=credentials)
        await asyncio.to_thread(
            lambda: next(iter(gcs_client.list_buckets(page_size=1)), None)
        )
        details.append("GCS connectivity confirmed.")
    except GoogleAPIError as e:
        status = "error"
        details.append(f"GCS connectivity failed: {e}")
        logger.error(details[-1])
    except Exception as e:
        status = "degraded"
        details.append(f"GCS connectivity check failed: {e}")
        logger.warning(details[-1])

    # Check Cloud Run Jobs client
    try:
        jobs_client = run_v2.JobsClient(credentials=credentials)
        if project_id and location:
            parent = f"projects/{project_id}/locations/{location}"
            await asyncio.to_thread(
                lambda: next(iter(jobs_client.list_jobs(parent=parent)), None)
            )
        details.append("Cloud Run Jobs client usable.")
    except GoogleAPIError as e:
        status = "error"
        details.append(f"Cloud Run Jobs client check failed: {e}")
        logger.error(details[-1])
    except Exception as e:
        status = "degraded"
        details.append(f"Cloud Run Jobs client check encountered an error: {e}")
        logger.warning(details[-1])

    logger.info(f"Plugin health check: {status}")
    return {"status": status, "details": details}


# Helpers
def _abspath(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))


def _tar_directory_to_temp(project_root: str) -> str:
    """
    Creates a gzipped tar archive of project_root excluding common heavy/irrelevant dirs.
    """
    base = f"sfe-run-job-{uuid.uuid4().hex[:8]}"
    temp_dir = Path(tempfile.gettempdir())
    archive_path = temp_dir / f"{base}.tar.gz"

    excludes = set(
        os.getenv(
            "GCR_ARCHIVE_EXCLUDES",
            ".git,.venv,venv,node_modules,__pycache__,.mypy_cache,.pytest_cache,.tox,.DS_Store",
        ).split(",")
    )
    project_root_path = Path(project_root).resolve()

    def _is_excluded(p: Path) -> bool:
        for ex in excludes:
            ex = ex.strip()
            if not ex:
                continue
            if ex.startswith("/"):
                if project_root_path.joinpath(ex[1:]) in p.parents or p.match(ex):
                    return True
            else:
                if ex in p.parts or p.name == ex:
                    return True
        return False

    with tarfile.open(archive_path, "w:gz") as tar:
        for root, dirs, files in os.walk(project_root_path):
            root_path = Path(root)
            # Filter directories in-place to skip walking excluded ones
            dirs[:] = [d for d in dirs if not _is_excluded(root_path / d)]
            for f in files:
                file_path = root_path / f
                if _is_excluded(file_path):
                    continue
                tar.add(
                    file_path, arcname=str(file_path.relative_to(project_root_path))
                )

    return str(archive_path)


# --- GCP Cloud Run Runner Logic ---
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=4, max=10),
    retry=retry_if_exception_type((GoogleAPIError, QuotaExceeded)),
)
async def run_cloud_run_job(
    job_config: Dict[str, Any], project_root: str, output_dir: str, **kwargs
) -> Dict[str, Any]:
    """
    Submits, monitors, and retrieves results from a Google Cloud Run job.
    """
    start_time_overall = time.monotonic()

    if not GCP_AVAILABLE:
        error_msg = "Google Cloud client libraries not found."
        logger.error(error_msg)
        return {"success": False, "reason": error_msg}

    # Validate job_config
    try:
        config = JobConfig(**job_config)
    except ValidationError as e:
        error_msg = f"Invalid job config: {e}"
        logger.error(error_msg)
        JOB_SUBMISSIONS_TOTAL.labels(status="validation_error").inc()
        return {"success": False, "reason": error_msg}

    # Sanitize paths
    project_root = _abspath(project_root)
    output_dir = _abspath(output_dir)
    # Ensure output directory exists
    try:
        os.makedirs(output_dir, exist_ok=True)
    except Exception as e:
        error_msg = f"Failed to create output_dir: {output_dir} ({e})"
        logger.error(error_msg)
        return {"success": False, "reason": error_msg}

    credentials = await _get_credentials()
    gcs_client = storage.Client(credentials=credentials)
    jobs_client = run_v2.JobsClient(credentials=credentials)
    exec_client = (
        run_v2.ExecutionsClient(credentials=credentials)
        if hasattr(run_v2, "ExecutionsClient")
        else jobs_client
    )
    logging_client = LoggingClient(credentials=credentials) if LoggingClient else None

    job_id: Optional[str] = None
    execution_name: Optional[str] = None
    job_name_to_use: Optional[str] = None
    job_created_for_cleanup: bool = False

    result: Dict[str, Any] = {
        "success": False,
        "reason": "Job submission failed",
        "jobId": "N/A",
        "executionName": "N/A",
        "finalStatus": "UNKNOWN",
        "statusReason": None,
        "output_gcs_location": None,
        "downloaded_output_path": None,
        "raw_log": None,
        "error": None,
        "duration_seconds": 0.0,
    }

    temp_archive_path: Optional[str] = None
    temp_gcs_input_key: Optional[str] = None

    try:
        JOB_SUBMISSIONS_TOTAL.labels(status="attempt").inc()

        # Step 1: Package and upload
        temp_archive_path = await asyncio.to_thread(
            _tar_directory_to_temp, project_root
        )
        logger.info(f"Project archived: {temp_archive_path}")

        # Place under input prefix
        archive_base_name = os.path.basename(temp_archive_path)
        if archive_base_name.endswith(".tar.gz"):
            archive_base_name = archive_base_name[:-7]
        temp_gcs_input_key = (
            f"{config.output_gcs_key_prefix}/inputs/{archive_base_name}.tar.gz"
        )
        logger.info(
            f"Uploading to gs://{config.input_gcs_bucket}/{temp_gcs_input_key}..."
        )
        upload_start = time.monotonic()

        @retry(
            stop=stop_after_attempt(5),
            wait=wait_exponential(min=2, max=10),
            retry=retry_if_exception_type(GoogleAPIError),
        )
        async def upload_archive_to_gcs():
            bucket = gcs_client.bucket(config.input_gcs_bucket)
            blob = bucket.blob(temp_gcs_input_key)
            with open(temp_archive_path, "rb") as f:
                await asyncio.to_thread(blob.upload_from_file, f)
            logger.info(
                f"Uploaded to GCS: gs://{config.input_gcs_bucket}/{temp_gcs_input_key}"
            )

        await upload_archive_to_gcs()
        GCS_OPERATION_LATENCY.labels(operation="upload").observe(
            time.monotonic() - upload_start
        )

        # Step 2: Prepare Cloud Run Job definition
        job_name_to_use = (
            config.job_definition_name
            or f"projects/{config.project_id}/locations/{config.location}/jobs/{archive_base_name}"
        )
        job_id = os.path.basename(job_name_to_use)

        # Build container template
        env_list = [
            run_v2.EnvVar(name=ev.name, value=ev.value) for ev in config.env_vars
        ] + [
            run_v2.EnvVar(
                name="SFE_JOB_INPUT_ARCHIVE_GCS_PATH",
                value=f"gs://{config.input_gcs_bucket}/{temp_gcs_input_key}",
            ),
            run_v2.EnvVar(
                name="SFE_JOB_OUTPUT_GCS_BUCKET",
                value=config.output_gcs_bucket or config.input_gcs_bucket,
            ),
            run_v2.EnvVar(
                name="SFE_JOB_OUTPUT_GCS_KEY_PREFIX",
                value=f"{config.output_gcs_key_prefix}/outputs/{job_id}",
            ),
            run_v2.EnvVar(name="SFE_JOB_OUTPUT_FILENAME", value=config.output_filename),
            run_v2.EnvVar(name="SFE_JOB_ID", value=job_id),
        ]

        container_template = run_v2.Container(
            image=config.image_url,
            command=config.command,
            args=config.args,
            env=env_list,
        )
        # Set resource limits via limits map if provided
        limits: Dict[str, str] = {}
        if config.cpu_limit:
            limits["cpu"] = str(config.cpu_limit)
        if config.memory_limit:
            limits["memory"] = str(config.memory_limit)
        if limits:
            try:
                container_template.resources = run_v2.ResourceRequirements(limits=limits)  # type: ignore
            except Exception:
                logger.debug(
                    "Skipping resource limits assignment; API mismatch or not supported."
                )

        task_template = run_v2.TaskTemplate(
            containers=[container_template],
            max_retries=config.max_retries,
            timeout=f"{config.timeout_seconds}s",
        )
        # ExecutionTemplate holds parallelism/task_count and wraps TaskTemplate
        try:
            execution_template = run_v2.ExecutionTemplate(
                template=task_template,
                parallelism=config.parallelism,
                task_count=config.task_count,
            )
        except Exception:
            # Fallback for older library field name
            execution_template = run_v2.ExecutionTemplate(task_template=task_template, parallelism=config.parallelism, task_count=config.task_count)  # type: ignore

        job_obj = run_v2.Job(template=execution_template)

        if not config.job_definition_name:
            job_request = run_v2.CreateJobRequest(
                parent=f"projects/{config.project_id}/locations/{config.location}",
                job_id=job_id,
                job=job_obj,
            )
            logger.info(f"Creating new Cloud Run Job: {job_id}...")

            @retry(
                stop=stop_after_attempt(3),
                wait=wait_exponential(min=2, max=10),
                retry=retry_if_exception_type((GoogleAPIError, Conflict)),
            )
            async def create_cloud_run_job_def():
                try:
                    op = await asyncio.to_thread(
                        jobs_client.create_job, request=job_request
                    )
                    job_def_response = await asyncio.to_thread(op.result)
                    logger.info(f"Created job definition: {job_def_response.name}")
                    return job_def_response.name
                except Conflict:
                    logger.warning(
                        f"Job definition '{job_id}' exists. Deleting and recreating..."
                    )
                    await asyncio.to_thread(
                        jobs_client.delete_job, name=job_name_to_use
                    )
                    await asyncio.sleep(5)
                    raise

            job_name_to_use = await create_cloud_run_job_def()
            job_created_for_cleanup = True

        # Step 3: Start execution
        start_execution_request = run_v2.RunJobRequest(name=job_name_to_use)
        logger.info(f"Starting execution for: {job_name_to_use}...")

        @retry(
            stop=stop_after_attempt(5),
            wait=wait_exponential(min=2, max=10),
            retry=retry_if_exception_type(GoogleAPIError),
        )
        async def start_cloud_run_execution():
            op = await asyncio.to_thread(
                jobs_client.run_job, request=start_execution_request
            )
            execution_response = await asyncio.to_thread(op.result)
            return execution_response

        execution_response = await start_cloud_run_execution()
        execution_name = execution_response.name
        result.update(
            {
                "executionName": execution_name,
                "jobId": job_name_to_use,
                "reason": "Job execution started.",
            }
        )
        logger.info(f"Started execution: {execution_name} (job_id={job_id})")

        # Step 4: Monitor execution (with jitter)
        exec_state = "UNKNOWN"
        final_status_reason = None
        monitor_start = time.monotonic()
        max_monitor_seconds = int(config.timeout_seconds * 1.5 + 300)
        while True:
            if exec_state in ["SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"]:
                break
            if time.monotonic() - monitor_start > max_monitor_seconds:
                exec_state = "MONITORING_TIMED_OUT"
                break
            await asyncio.sleep(12 + random.uniform(-3, 3))

            @retry(
                stop=stop_after_attempt(5),
                wait=wait_exponential(min=2, max=10),
                retry=retry_if_exception_type(GoogleAPIError),
            )
            async def get_execution_status():
                # Prefer ExecutionsClient if available
                if hasattr(exec_client, "get_execution"):
                    return await asyncio.to_thread(
                        exec_client.get_execution, name=execution_name
                    )
                return await asyncio.to_thread(jobs_client.get_execution, name=execution_name)  # type: ignore[attr-defined]

            execution_info = await get_execution_status()
            try:
                exec_state = execution_info.state.name  # type: ignore[attr-defined]
            except Exception:
                exec_state = str(getattr(execution_info, "state", "UNKNOWN"))
            final_status_reason = (
                execution_info.conditions[0].message
                if getattr(execution_info, "conditions", None)
                else None
            )
            logger.info(
                f"Execution '{execution_name}' status: {exec_state} ({final_status_reason})"
            )
            if exec_state in ["SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"]:
                started = getattr(execution_info, "start_time", None)
                completed = getattr(execution_info, "completion_time", None)
                try:
                    dur = (
                        (completed - started).total_seconds()
                        if started and completed
                        else (time.monotonic() - monitor_start)
                    )
                except Exception:
                    dur = time.monotonic() - monitor_start
                result.update(
                    {
                        "finalStatus": exec_state,
                        "statusReason": final_status_reason,
                        "duration_seconds": float(dur),
                    }
                )
                break

        if exec_state not in ["SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"]:
            final_status_reason = (
                f"Monitoring timed out after {max_monitor_seconds} seconds."
            )
            logger.error(
                f"Execution {execution_name} monitoring timed out. Status: {exec_state}"
            )
            result.update(
                {
                    "finalStatus": exec_state,
                    "statusReason": final_status_reason,
                    "reason": final_status_reason,
                }
            )

        # Step 5: Process results
        if exec_state == "SUCCEEDED":
            result.update({"success": True, "reason": "Cloud Run job succeeded."})
            output_gcs_key = f"{config.output_gcs_key_prefix}/outputs/{job_id}/{config.output_filename}"
            result["output_gcs_location"] = (
                f"gs://{config.output_gcs_bucket or config.input_gcs_bucket}/{output_gcs_key}"
            )
            local_output_path = os.path.join(
                output_dir, f"{job_id}-{config.output_filename}"
            )
            os.makedirs(os.path.dirname(local_output_path), exist_ok=True)
            download_start = time.monotonic()

            @retry(
                stop=stop_after_attempt(5),
                wait=wait_exponential(min=2, max=10),
                retry=retry_if_exception_type(GoogleAPIError),
            )
            async def download_job_results():
                logger.info(
                    f"Downloading results from gs://{config.output_gcs_bucket or config.input_gcs_bucket}/{output_gcs_key}..."
                )
                bucket = gcs_client.bucket(
                    config.output_gcs_bucket or config.input_gcs_bucket
                )
                blob = bucket.blob(output_gcs_key)
                with open(local_output_path, "wb") as f:
                    await asyncio.to_thread(blob.download_to_file, f)
                logger.info(f"Results downloaded to {local_output_path}")

            try:
                await download_job_results()
                result["downloaded_output_path"] = local_output_path
                GCS_OPERATION_LATENCY.labels(operation="download").observe(
                    time.monotonic() - download_start
                )
            except NotFound:
                logger.warning(f"Output file {output_gcs_key} not found in GCS.")
                result.update(
                    {
                        "reason": result["reason"] + " Output file not found.",
                        "success": False,
                    }
                )
            except GoogleAPIError as e:
                logger.error(f"Failed to download results: {e}")
                result.update(
                    {
                        "reason": result["reason"] + f" Download failed: {e}",
                        "success": False,
                    }
                )

        # Fetch logs on failure if logging client available
        if exec_state == "FAILED" and logging_client:
            try:
                filter_str = f'resource.type="cloud_run_job" resource.labels.job_name="{job_id}" severity>=ERROR'
                logs_iter = await asyncio.to_thread(
                    logging_client.list_entries, filter_=filter_str, page_size=10
                )
                raw_logs = []
                for entry in logs_iter:
                    payload = getattr(entry, "payload", "")
                    raw_logs.append(
                        str(payload)[:2000]
                    )  # truncate to avoid huge payloads
                    if len(raw_logs) >= 10:
                        break
                result["raw_log"] = raw_logs
                logger.info(f"Retrieved {len(raw_logs)} error logs for failed job.")
            except Exception as e:
                logger.warning(f"Failed to retrieve logs: {e}")

    except QuotaExceeded as e:
        logger.warning(
            f"Quota exceeded: {e}. Will reduce resources and retry via decorator."
        )
        # Mutate job_config so that next retry sees reduced values
        if not job_config.get("_reduced_resources_once"):
            if job_config.get("cpu_limit") in ("1000m", "1"):
                job_config["cpu_limit"] = "500m"
            if job_config.get("memory_limit") in ("1Gi", "1024Mi"):
                job_config["memory_limit"] = "512Mi"
            # Also reduce parallelism/task_count by half, minimum 1
            try:
                job_config["parallelism"] = max(
                    1, int(job_config.get("parallelism", 1)) // 2 or 1
                )
                job_config["task_count"] = max(
                    1, int(job_config.get("task_count", 1)) // 2 or 1
                )
            except Exception:
                job_config["parallelism"] = 1
                job_config["task_count"] = max(1, int(job_config.get("task_count", 1)))
            job_config["_reduced_resources_once"] = True
        raise
    except InvalidArgument as e:
        error_msg = f"Invalid job configuration: {e}"
        logger.error(error_msg)
        result.update({"reason": error_msg, "error": error_msg})
    except GoogleAPIError as e:
        error_msg = f"GCP API error: {e}"
        logger.error(error_msg, exc_info=True)
        result.update({"reason": error_msg, "error": error_msg})
    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        logger.error(error_msg, exc_info=True)
        result.update({"reason": error_msg, "error": error_msg})
    finally:
        # Ensure duration is set for metrics
        if not result.get("duration_seconds"):
            result["duration_seconds"] = float(time.monotonic() - start_time_overall)

        # Cleanup local archive
        if (
            temp_archive_path
            and os.path.exists(temp_archive_path)
            and not config.retain_temp_archive
        ):
            try:
                await asyncio.to_thread(os.remove, temp_archive_path)
                logger.debug(f"Cleaned up local archive: {temp_archive_path}")
            except Exception as e:
                logger.debug(f"Failed to remove local archive: {e}")
        # Cleanup GCS input (best-effort) if requested
        if temp_gcs_input_key and config.cleanup_gcs_input:
            try:
                delete_start = time.monotonic()
                bucket = gcs_client.bucket(config.input_gcs_bucket)
                blob = bucket.blob(temp_gcs_input_key)
                await asyncio.to_thread(blob.delete)
                GCS_OPERATION_LATENCY.labels(operation="delete").observe(
                    time.monotonic() - delete_start
                )
                logger.debug(
                    f"Cleaned up GCS input: gs://{config.input_gcs_bucket}/{temp_gcs_input_key}"
                )
            except NotFound:
                logger.debug(f"GCS input {temp_gcs_input_key} already deleted.")
            except Exception as e:
                logger.warning(f"Failed to delete GCS input: {e}")
        # Cleanup ephemeral job definition
        if (
            job_created_for_cleanup
            and not config.retain_temp_archive
            and job_name_to_use
        ):
            try:
                logger.info(f"Deleting ephemeral job definition: {job_name_to_use}...")
                op = await asyncio.to_thread(
                    jobs_client.delete_job, name=job_name_to_use
                )
                await asyncio.to_thread(op.result)
                logger.info(f"Deleted job definition: {job_name_to_use}")
            except NotFound:
                logger.debug(f"Job definition {job_name_to_use} already deleted.")
            except Exception as e:
                logger.warning(f"Failed to delete job definition: {e}")
        # Metrics
        JOB_SUBMISSIONS_TOTAL.labels(
            status="success" if result["success"] else "failure"
        ).inc()
        JOB_DURATION_SECONDS.labels(
            status=result.get("finalStatus", "UNKNOWN")
        ).observe(float(result.get("duration_seconds") or 0.0))

    return result


# --- Auto-registration ---
def register_plugin_entrypoints(register_func: Callable):
    """
    Registers the plugin with the core simulation system.

    Args:
        register_func (Callable): Function to register execution backends.
    """
    logger.info("Registering GCPCloudRunRunnerPlugin...")
    register_func(name="gcp_cloud_run", executor_func=run_cloud_run_job)
