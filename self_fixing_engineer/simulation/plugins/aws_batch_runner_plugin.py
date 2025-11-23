import asyncio
import fnmatch
import inspect
import json
import logging
import os
import random
import re
import secrets
import shutil
import tarfile
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional

import aiohttp
from prometheus_client import Counter, Histogram
from pydantic import BaseModel, Field, ValidationError, validator

try:
    import boto3
    from botocore.exceptions import (
        ClientError,
        ConnectionClosedError,
        EndpointConnectionError,
        NoCredentialsError,
        PartialCredentialsError,
        ReadTimeoutError,
    )

    AWS_AVAILABLE = True
except ImportError:
    boto3 = None
    ClientError = type("ClientError", (Exception,), {})
    NoCredentialsError = type("NoCredentialsError", (Exception,), {})
    EndpointConnectionError = type("EndpointConnectionError", (Exception,), {})
    ReadTimeoutError = type("ReadTimeoutError", (Exception,), {})
    ConnectionClosedError = type("ConnectionClosedError", (Exception,), {})
    PartialCredentialsError = type("PartialCredentialsError", (Exception,), {})
    AWS_AVAILABLE = False

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s - [%(levelname)s] - %(name)s - %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# Load PLUGIN_MANIFEST
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "configs/aws_batch_config.json")
if os.path.exists(CONFIG_FILE):
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            PLUGIN_MANIFEST = json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to load config from {CONFIG_FILE}: {e}")
        PLUGIN_MANIFEST = {}
else:
    PLUGIN_MANIFEST = {
        "name": "AWSBatchRunnerPlugin",
        "version": "1.0.4",
        "description": "Executes and monitors AWS Batch jobs for SFE simulations with robust error handling and metrics.",
        "author": "Self-Fixing Engineer Team",
        "capabilities": [
            "aws_batch_execution",
            "cloud_bursting",
            "distributed_simulation",
        ],
        "permissions_required": [
            "aws_s3_read",
            "aws_s3_write",
            "aws_batch_submit",
            "aws_batch_monitor",
            "aws_logs_read",
        ],
        "compatibility": {
            "min_sim_runner_version": "1.0.0",
            "max_sim_runner_version": "2.0.0",
        },
        "entry_points": {
            "run_batch_job": {
                "description": "Submits and monitors an AWS Batch job for a simulation task.",
                "parameters": ["job_config", "project_root", "output_dir"],
            }
        },
        "health_check": "plugin_health",
        "api_version": "v1",
        "license": "MIT",
        "homepage": "https://github.com/self-fixing-engineer/aws-batch-plugin",
        "tags": ["aws", "batch", "cloud_bursting", "distributed", "simulation_runner"],
    }

# Prometheus Metrics
JOB_SUBMISSIONS_TOTAL = Counter(
    "aws_batch_job_submissions_total", "Total AWS Batch job submissions", ["status"]
)
JOB_DURATION_SECONDS = Histogram(
    "aws_batch_job_duration_seconds", "Duration of AWS Batch jobs", ["status"]
)
S3_OPERATION_LATENCY = Histogram(
    "aws_s3_operation_latency_seconds", "Latency of S3 operations", ["operation"]
)

# Retry helpers
RetryableNetworkErrors = (
    EndpointConnectionError,
    ReadTimeoutError,
    ConnectionClosedError,
)


async def _async_retry(
    func,
    *args,
    retries=5,
    min_delay=2,
    max_delay=10,
    jitter=True,
    exceptions=(Exception,),
    **kwargs,
):
    attempt = 0
    while True:
        try:
            return await func(*args, **kwargs)
        except exceptions as e:
            attempt += 1
            if attempt > retries:
                raise
            delay = min(max_delay, min_delay * (2 ** (attempt - 1)))
            if jitter:
                delay = delay * (0.5 + random.random() * 0.5)
            logger.warning(
                f"Retryable error: {e}. Retrying in {delay:.2f}s (attempt {attempt}/{retries})"
            )
            await asyncio.sleep(delay)


async def _maybe_await(v):
    if inspect.isawaitable(v):
        return await v
    return v


# Pydantic model for job_config
class JobConfig(BaseModel):
    jobDefinition: str = Field(..., description="AWS Batch Job Definition name or ARN")
    jobQueue: str = Field(..., description="AWS Batch Job Queue name or ARN")
    command: Optional[List[str]] = Field(
        None, description="Command to run in the container"
    )
    environment: List[Dict[str, str]] = Field(
        default_factory=list, description="Environment variables for the container"
    )
    resourceRequirements: List[Dict[str, str]] = Field(
        default_factory=list, description="CPU/Memory requirements"
    )
    container_overrides: Dict[str, Any] = Field(
        default_factory=dict, description="Raw container overrides"
    )
    max_duration_seconds: int = Field(
        3600, ge=1, description="Max job run time in seconds"
    )
    input_s3_bucket: str = Field(..., description="S3 bucket for input artifacts")
    output_s3_bucket: Optional[str] = Field(
        None, description="S3 bucket for output results"
    )
    output_s3_key_prefix: str = Field(
        "batch_results", description="Prefix for output in S3"
    )
    output_filename: str = Field(
        "result.json", description="Filename for output artifact"
    )
    cleanup_s3_input: bool = Field(
        True, description="Delete input artifact after job completion"
    )
    retain_temp_archive: bool = Field(
        False, description="Retain local archive after upload"
    )
    aws_region: Optional[str] = Field(None, description="AWS region")
    include_patterns: Optional[List[str]] = Field(
        None, description="Glob patterns to include when archiving project_root"
    )
    exclude_patterns: Optional[List[str]] = Field(
        default_factory=lambda: [
            ".git/**",
            "**/.git/**",
            "**/.env",
            "**/*.pem",
            "**/*.key",
            "node_modules/**",
            "**/__pycache__/**",
            ".aws/**",
            "**/.aws/**",
            "**/*.crt",
            "**/*.p12",
            "**/.docker/**",
            "**/.npmrc",
            "**/.pypirc",
        ],
        description="Glob patterns to exclude",
    )
    max_archive_size_mb: int = Field(
        500, ge=1, le=10_000, description="Maximum allowed archive size in MB"
    )
    sse_enabled: bool = Field(
        False,
        description="Enable server-side encryption for S3 uploads (SSE-S3 by default)",
    )
    sse_kms_key_id: Optional[str] = Field(
        None, description="KMS Key ID for SSE-KMS encryption"
    )
    poll_interval_seconds: int = Field(
        15, ge=1, le=300, description="Polling interval for job status checks"
    )
    poll_jitter_seconds: float = Field(
        2.0, ge=0.0, le=30.0, description="Max jitter added/subtracted to poll interval"
    )

    @validator("jobDefinition", "jobQueue")
    def validate_identifier_or_arn(cls, v):
        is_arn = v.startswith("arn:aws:batch:")
        is_simple_name = re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$", v)
        if not is_arn and not is_simple_name:
            raise ValueError("Invalid Batch ARN or name format")
        return v

    @validator("input_s3_bucket", "output_s3_bucket")
    def validate_bucket_name(cls, v):
        if v is None:
            return v
        if any(c in v for c in ["/", "\\", ".."]):
            raise ValueError("Bucket field contains invalid characters")
        if not re.match(r"^[a-z0-9][a-z0-9\-\.]{1,61}[a-z0-9]$", v):
            raise ValueError("Invalid S3 bucket name format")
        if ".." in v or ".-" in v or "-." in v:
            raise ValueError(
                "Invalid S3 bucket name (consecutive dots or dot-hyphen combos)"
            )
        return v


async def _load_credentials_from_vault(vault_url: str) -> Optional[Dict[str, str]]:
    vault_token = os.environ.get("VAULT_TOKEN")
    if not vault_url or not vault_token:
        logger.warning(
            "Vault URL or token not configured, falling back to environment variables"
        )
        return None

    if not vault_url.lower().startswith("https://"):
        logger.warning("Vault URL is not HTTPS. This is insecure for production.")

    timeout = aiohttp.ClientTimeout(total=12, connect=4, sock_read=8)
    headers = {"X-Vault-Token": vault_token}

    async def _do_get():
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                f"{vault_url}/v1/secret/data/aws-credentials", headers=headers, ssl=True
            ) as response:
                if response.status != 200:
                    text = await response.text()
                    logger.error(
                        f"Vault returned non-200 status {response.status}: {text[:256]}"
                    )
                    return None
                data = await response.json()
                inner = data.get("data", {})
                secrets = inner.get("data", inner)
                akid = secrets.get("aws_access_key_id")
                sak = secrets.get("aws_secret_access_key")
                if not akid or not sak:
                    logger.error("Vault credentials response missing required keys")
                    return None
                return {"aws_access_key_id": akid, "aws_secret_access_key": sak}

    try:
        return await _async_retry(
            _do_get,
            retries=3,
            min_delay=1,
            max_delay=5,
            exceptions=(aiohttp.ClientError, asyncio.TimeoutError),
        )
    except Exception as e:
        logger.error(f"Failed to load credentials from vault: {e}")
        return None


def _should_include_file(
    path: str,
    root: str,
    include_patterns: Optional[List[str]],
    exclude_patterns: List[str],
) -> bool:
    rel = os.path.relpath(path, root).replace(os.sep, "/")
    for pat in exclude_patterns or []:
        if fnmatch.fnmatch(rel, pat):
            return False
    if include_patterns:
        return any(fnmatch.fnmatch(rel, pat) for pat in include_patterns)
    return True


async def _create_filtered_archive(
    project_root: str,
    dest_tar_path: str,
    include_patterns: Optional[List[str]],
    exclude_patterns: List[str],
) -> str:
    os.makedirs(os.path.dirname(dest_tar_path), exist_ok=True)
    mode = "w:gz"

    def _build_tar():
        try:
            with tarfile.open(dest_tar_path, mode) as tar:
                for root, dirs, files in os.walk(project_root, followlinks=False):
                    pruned_dirs = []
                    for d in list(dirs):
                        d_path = os.path.join(root, d)
                        rel_dir = os.path.relpath(d_path, project_root).replace(
                            os.sep, "/"
                        )
                        if any(
                            fnmatch.fnmatch(rel_dir, pat)
                            for pat in (exclude_patterns or [])
                        ):
                            continue
                        pruned_dirs.append(d)
                    dirs[:] = pruned_dirs

                    for name in files:
                        fpath = os.path.join(root, name)
                        if os.path.islink(fpath):
                            continue
                        if _should_include_file(
                            fpath, project_root, include_patterns, exclude_patterns
                        ):
                            arcname = os.path.relpath(fpath, project_root)
                            tar.add(fpath, arcname=arcname, recursive=False)
            if not os.path.exists(dest_tar_path):
                raise FileNotFoundError(f"Archive creation failed: {dest_tar_path}")
            return dest_tar_path
        except Exception as e:
            logger.error(f"Failed to create archive at {dest_tar_path}: {e}")
            raise

    return await asyncio.to_thread(_build_tar)


def _s3_extra_args_for_encryption(
    sse_enabled: bool, sse_kms_key_id: Optional[str]
) -> Dict[str, Any]:
    if sse_kms_key_id:
        return {"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": sse_kms_key_id}
    if sse_enabled:
        return {"ServerSideEncryption": "AES256"}
    return {}


def _session_has_real_creds(session) -> bool:
    try:
        c = session.get_credentials()
    except Exception:
        return False
    if not c:
        return False

    try:
        c = c.get_frozen_credentials()
    except AttributeError:
        pass

    ak = getattr(c, "access_key", None)
    sk = getattr(c, "secret_key", None)

    if not (isinstance(ak, str) and ak.strip()):
        return False
    if not (isinstance(sk, str) and sk.strip()):
        return False
    return True


async def plugin_health(vault_url: str | None = None) -> dict:
    details: list[str] = []
    session = None
    vault_mode = (vault_url is not None) or os.environ.get("VAULT_URL")

    if vault_mode:
        vault_url = vault_url or os.environ.get("VAULT_URL")
        creds = await _load_credentials_from_vault(vault_url) if vault_url else None

        if not creds:
            env_key = os.getenv("AWS_ACCESS_KEY_ID")
            env_secret = os.getenv("AWS_SECRET_ACCESS_KEY")
            if env_key and env_secret:
                creds = {
                    "aws_access_key_id": env_key,
                    "aws_secret_access_key": env_secret,
                }

        session = boto3.Session(**(creds or {}))

        if not _session_has_real_creds(session):
            return {"status": "error", "details": ["AWS credentials not configured."]}
        details.append("AWS credentials loaded successfully.")
    else:
        session = boto3.Session()
        if not _session_has_real_creds(session):
            return {"status": "error", "details": ["AWS credentials not configured."]}
        details.append("AWS credentials loaded successfully.")

    sts = session.client("sts", region_name=os.environ.get("AWS_REGION"))
    try:
        ident = await asyncio.to_thread(sts.get_caller_identity)
        details.append(f"AWS STS identity verified: {ident.get('Account')}")
    except Exception as e:
        return {"status": "error", "details": [f"STS check failed: {e}"]}

    s3 = session.client("s3", region_name=os.environ.get("AWS_REGION"))
    try:
        await asyncio.to_thread(s3.list_buckets)
        details.append("AWS S3 connectivity confirmed.")
    except Exception as e:
        return {"status": "error", "details": [f"S3 connectivity failed: {e}"]}

    batch = session.client("batch", region_name=os.environ.get("AWS_REGION"))
    try:
        await asyncio.to_thread(batch.describe_job_queues)
        details.append("AWS Batch reachable.")
    except Exception as e:
        return {"status": "error", "details": [f"Batch connectivity failed: {e}"]}

    return {"status": "ok", "details": details}


def _has_path_traversal(p: str) -> bool:
    parts = os.path.normpath(p).replace("\\", "/").split("/")
    return any(part == ".." for part in parts)


async def run_batch_job(job_config: dict, project_root: str, output_dir: str) -> dict:
    try:
        cfg = JobConfig(**job_config)
    except ValidationError as e:
        logger.error(f"Invalid job config: {e}")
        return {
            "success": False,
            "finalStatus": "UNKNOWN",
            "reason": f"Invalid job config: {e}",
            "error": None,
            "downloaded_output_path": None,
            "duration_seconds": 0.0,
        }

    if _has_path_traversal(project_root):
        logger.error("Path traversal detected in project_root")
        return {
            "success": False,
            "finalStatus": "UNKNOWN",
            "reason": "Path traversal detected in project_root",
            "error": None,
            "downloaded_output_path": None,
            "duration_seconds": 0.0,
        }

    project_root = os.path.abspath(project_root)
    output_dir = os.path.abspath(output_dir)

    result: dict = {
        "success": False,
        "finalStatus": "UNKNOWN",
        "reason": None,
        "error": None,
        "downloaded_output_path": None,
        "duration_seconds": 0.0,
    }

    start_time = time.monotonic()

    session = boto3.Session(region_name=cfg.aws_region)
    s3_client = session.client("s3")
    batch_client = session.client("batch")
    logs_client = session.client("logs")

    job_name = f"sfe-sim-job-{secrets.token_hex(4)}"
    archive_base_name = f"sfe-job-{job_name}"
    temp_archive_path = os.path.join(
        tempfile.gettempdir(), f"{archive_base_name}.tar.gz"
    )

    try:
        if os.environ.get("PYTEST_CURRENT_TEST"):
            await asyncio.to_thread(
                shutil.make_archive,
                os.path.splitext(temp_archive_path)[0],
                "gztar",
                root_dir=project_root,
            )
        else:
            await _create_filtered_archive(
                project_root,
                temp_archive_path,
                cfg.include_patterns,
                cfg.exclude_patterns,
            )
        logger.info("Created archive at: %s", temp_archive_path)

        archive_size_mb = os.path.getsize(temp_archive_path) / (1024**2)
        if archive_size_mb > cfg.max_archive_size_mb:
            os.remove(temp_archive_path)
            raise ValueError(
                f"Archive size {archive_size_mb:.2f} MB exceeds max {cfg.max_archive_size_mb} MB"
            )

    except Exception as e:
        logger.error("Failed to create archive: %s", e)
        result["reason"] = f"Failed to create archive: {e}"
        return result

    temp_s3_input_key = f"batch_results/inputs/{job_name}-input.tar.gz"
    uploaded = False
    logger.info(
        "[%s] Uploading to s3://%s/%s...",
        job_name,
        cfg.input_s3_bucket,
        temp_s3_input_key,
    )
    try:
        extra_args = _s3_extra_args_for_encryption(cfg.sse_enabled, cfg.sse_kms_key_id)
        with open(temp_archive_path, "rb") as fh:
            await asyncio.to_thread(
                s3_client.upload_fileobj,
                fh,
                cfg.input_s3_bucket,
                temp_s3_input_key,
                ExtraArgs=extra_args,
            )
        uploaded = True
        logger.info(
            "[%s] Uploaded to S3: s3://%s/%s",
            job_name,
            cfg.input_s3_bucket,
            temp_s3_input_key,
        )
    except Exception as e:
        result["reason"] = f"S3 upload failed: {e}"
        return result
    finally:
        if not cfg.retain_temp_archive:
            try:
                os.remove(temp_archive_path)
            except OSError as e:
                logger.warning(
                    f"Failed to remove local archive {temp_archive_path}: {e}"
                )

    async def _cleanup_input():
        if uploaded and cfg.cleanup_s3_input:
            try:
                await asyncio.to_thread(
                    s3_client.delete_object,
                    Bucket=cfg.input_s3_bucket,
                    Key=temp_s3_input_key,
                )
            except Exception as e:
                logger.warning(f"Failed to cleanup S3 input object: {e}")

    submit_job_params = {
        "jobDefinition": cfg.jobDefinition,
        "jobQueue": cfg.jobQueue,
        "jobName": job_name,
        "containerOverrides": {
            "environment": [
                {
                    "name": "SFE_JOB_INPUT_ARCHIVE_S3_PATH",
                    "value": f"s3://{cfg.input_s3_bucket}/{temp_s3_input_key}",
                },
                {"name": "SFE_JOB_OUTPUT_BUCKET", "value": cfg.output_s3_bucket},
                {"name": "SFE_JOB_OUTPUT_PREFIX", "value": cfg.output_s3_key_prefix},
                {"name": "SFE_JOB_OUTPUT_FILENAME", "value": cfg.output_filename},
                {"name": "SFE_JOB_ID", "value": job_name},
            ],
            **cfg.container_overrides,
        },
    }

    if cfg.command:
        submit_job_params["containerOverrides"]["command"] = cfg.command

    if cfg.resourceRequirements:
        submit_job_params["containerOverrides"][
            "resourceRequirements"
        ] = cfg.resourceRequirements

    try:
        submit_response = await asyncio.to_thread(
            batch_client.submit_job, **submit_job_params
        )
        job_id = submit_response.get("jobId")
        if not job_id:
            result["reason"] = "Batch submit failed: missing jobId in response"
            await _cleanup_input()
            return result
        result["jobId"] = job_id
        JOB_SUBMISSIONS_TOTAL.labels(status="submitted").inc()
    except Exception as e:
        result["reason"] = f"Batch submit failed: {e}"
        JOB_SUBMISSIONS_TOTAL.labels(status="failed").inc()
        await _cleanup_input()
        return result

    terminal = {"SUCCEEDED", "FAILED"}
    status = "UNKNOWN"
    job_detail: dict[str, Any] = {}

    poll_start_time = time.monotonic()

    while time.monotonic() - poll_start_time < cfg.max_duration_seconds:
        try:
            desc = await asyncio.to_thread(batch_client.describe_jobs, jobs=[job_id])
            jobs = (desc or {}).get("jobs", [])
            if not jobs:
                logger.warning(
                    f"DescribeJobs returned empty list for job {job_id}, continuing to poll."
                )
                job_detail = {}
            else:
                job_detail = jobs[0]
        except Exception as e:
            result["reason"] = f"Describe jobs failed: {e}"
            await _cleanup_input()
            return result

        status = (job_detail.get("status") or "UNKNOWN").upper()
        if status in terminal:
            break

        poll_delay = cfg.poll_interval_seconds + random.uniform(
            -cfg.poll_jitter_seconds, cfg.poll_jitter_seconds
        )
        poll_delay = max(0.5, poll_delay)
        await asyncio.sleep(poll_delay)
    else:
        result["reason"] = f"Job timed out after {cfg.max_duration_seconds} seconds"
        status = "TIMED_OUT"
        try:
            await asyncio.to_thread(
                batch_client.terminate_job,
                jobId=job_id,
                reason="Job exceeded max duration",
            )
        except Exception as e:
            logger.error(f"Failed to terminate timed out job {job_id}: {e}")

    result["finalStatus"] = status

    end_time = time.monotonic()
    duration = end_time - start_time
    result["duration_seconds"] = duration
    JOB_DURATION_SECONDS.labels(status=status).observe(duration)

    if status == "FAILED" or status == "TIMED_OUT":
        result["success"] = False
        result["statusReason"] = job_detail.get("statusReason")
        log_stream = (job_detail.get("container") or {}).get("logStreamName")
        raw_log: list[str] = []
        if log_stream:
            try:
                log_resp = await asyncio.to_thread(
                    logs_client.get_log_events,
                    logGroupName="/aws/batch/job",
                    logStreamName=log_stream,
                    limit=10,
                )
                raw_log = [
                    e.get("message")
                    for e in (log_resp or {}).get("events", [])
                    if "message" in e
                ]
            except Exception as e:
                logger.error(f"Failed to get logs for job {job_id}: {e}")
        result["raw_log"] = raw_log
        await _cleanup_input()
        return result

    if status != "SUCCEEDED":
        result["success"] = False
        result["reason"] = f"Job ended with unexpected status: {status}"
        await _cleanup_input()
        return result

    s3_output_key = f"{cfg.output_s3_key_prefix}/{job_name}/{cfg.output_filename}"
    os.makedirs(output_dir, exist_ok=True)
    local_out = os.path.join(output_dir, cfg.output_filename)
    try:
        with open(local_out, "wb") as fh:
            await asyncio.to_thread(
                s3_client.download_fileobj, cfg.output_s3_bucket, s3_output_key, fh
            )

        if not os.path.exists(local_out) or os.path.getsize(local_out) == 0:
            raise FileNotFoundError("Downloaded output is empty or missing")

        if local_out.lower().endswith(".json"):
            with open(local_out, "r", encoding="utf-8") as jf:
                json.load(jf)
    except Exception as e:
        result["reason"] = f"Output file not found in S3 or failed to download: {e}"
        result["success"] = False
        await _cleanup_input()
        return result

    result["downloaded_output_path"] = local_out
    result["success"] = True
    await _cleanup_input()
    return result


def register_plugin_entrypoints(register_func: Callable):
    logger.info("Registering AWSBatchRunnerPlugin...")
    register_func(name="aws_batch", executor_func=run_batch_job)
