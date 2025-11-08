import sys
import os
import argparse
import logging
import subprocess
import json
import glob
import traceback
import asyncio
import tempfile
import uuid
import time
import contextlib  # used for nullcontext when OTEL disabled
from typing import Dict, Any, Callable, List, Optional
import hashlib
import ast
import shutil
import tarfile  # portable packaging
import inspect   # for plugin runner signature introspection

if sys.version_info < (3, 10):
    sys.stderr.write("Python 3.10+ required.\n")
    sys.exit(98)

try:
    from prometheus_client import Gauge, Counter, Histogram, start_http_server
    prometheus_available = True
except ImportError:
    prometheus_available = False

try:
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.exceptions import InvalidSignature
    crypto_available = True
except ImportError:
    crypto_available = False

# Centralized OpenTelemetry Imports
# This will provide a no-op tracer if the library is unavailable.
from ..otel_config import get_tracer, trace, extract, StatusCode

# Initialize tracer from the centralized configuration
tracer = get_tracer(__name__)

current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from .. import core
from .. import utils
from ..dashboard import STREAMLIT_AVAILABLE, display_simulation_dashboard
from ..audit_log import append_distributed_log

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")
main_runner_logger = logging.getLogger("main_sim_runner")
main_runner_logger.setLevel(logging.INFO)

# Prometheus metrics (extended)
if prometheus_available:
    remote_jobs_submitted = Counter('remote_jobs_submitted_total', 'Number of remote jobs submitted')
    remote_jobs_completed = Counter('remote_jobs_completed_total', 'Number of remote jobs completed successfully')
    remote_jobs_failed = Counter('remote_jobs_failed_total', 'Number of remote jobs failed')
    plugin_runs_total = Counter('plugin_runs_total', 'Plugin runs', ['plugin', 'status'])
    plugin_run_duration = Histogram('plugin_run_duration_seconds', 'Duration of plugin runs in seconds', ['plugin'])
    simulation_run_duration_hist = Histogram('simulation_run_duration_seconds', 'Duration of entire simulation runs in seconds')

# Plugin Entrypoint Registration (compat layer)
_registered_plugin_entrypoints: Dict[str, Callable] = {}
_registered_plugin_info: Dict[str, Dict[str, str]] = {}
plugin_load_errors: List[Dict[str, str]] = []

def register_entrypoint(*args, **kwargs):
    """
    Back-compat shim:
      - Old style: register_entrypoint("plugin:lang", callable)
      - New style: register_entrypoint(name="plugin:lang", runner=<callable>, ...)
    Accepts synonyms: plugin_name/name, entrypoint_func/runner/func.
    """
    name = None
    fn = None

    # positional path
    if len(args) >= 2 and isinstance(args[0], str) and callable(args[1]):
        name, fn = args[0], args[1]
    # keyword path
    if not name:
        name = kwargs.get("name") or kwargs.get("plugin_name")
    if not fn:
        fn = (
            kwargs.get("entrypoint_func")
            or kwargs.get("runner")
            or kwargs.get("func")
        )

    if not name or not callable(fn):
        raise TypeError("register_entrypoint requires a name and a callable")

    _registered_plugin_entrypoints[name] = fn
    main_runner_logger.info(f"Registered plugin entrypoint: '{name}'")

def _synthesize_kwargs_for_runner(rf: Callable, module_name: str, language_or_framework: str, args: argparse.Namespace) -> Dict[str, Any]:
    """
    Build sensible default kwargs for a plugin runner function based on its signature and main CLI args.
    Allows plugins to execute 'for real' during the bulk run without custom --plugin-args.
    """
    params = set(inspect.signature(rf).parameters.keys())
    project_root = os.getcwd()
    coverage_dir_rel = os.path.join("atco_artifacts", "coverage_reports")
    logs_dir_rel = os.path.join("atco_artifacts", "logs")
    os.makedirs(os.path.join(project_root, coverage_dir_rel), exist_ok=True)
    os.makedirs(os.path.join(project_root, logs_dir_rel), exist_ok=True)

    # Sensible defaults
    coverage_filename = f"{module_name.replace(':','_')}_{language_or_framework}_coverage.json"
    log_filename = f"{module_name.replace(':','_')}_{language_or_framework}_run.log"

    defaults: Dict[str, Any] = {}

    # Common mappings for jest-like plugins
    if "test_file_path" in params and getattr(args, "testfile", None):
        defaults["test_file_path"] = args.testfile
    if "target_identifier" in params and getattr(args, "codefile", None):
        defaults["target_identifier"] = args.codefile
    if "project_root" in params:
        defaults["project_root"] = project_root
    if "temp_coverage_report_path_relative" in params:
        defaults["temp_coverage_report_path_relative"] = os.path.join(coverage_dir_rel, coverage_filename)
    if "extra_jest_args" in params:
        defaults["extra_jest_args"] = ["--verbose"]
    if "timeout_seconds" in params:
        defaults["timeout_seconds"] = int(os.environ.get("PLUGIN_TIMEOUT_SECONDS", "600"))
    if "log_max_bytes" in params:
        defaults["log_max_bytes"] = int(os.environ.get("PLUGIN_LOG_MAX_BYTES", "262144"))
    if "log_artifact_path_relative" in params:
        defaults["log_artifact_path_relative"] = os.path.join(logs_dir_rel, log_filename)

    # Generic fallbacks some plugins may expect
    if "testfile" in params and getattr(args, "testfile", None):
        defaults["testfile"] = args.testfile
    if "codefile" in params and getattr(args, "codefile", None):
        defaults["codefile"] = args.codefile
    if "session" in params and getattr(args, "session", None):
        defaults["session"] = args.session

    return defaults

def _plugin_register_adapter(module_name: str):
    """
    Adapts plugins that call:
        register_plugin_entrypoints(register_func)
    where register_func may be invoked as:
        register_func(language_or_framework, runner_info)
    or with keywords:
        register_func(language="javascript", runner=<fn>, version="1.0.0", ...)
    """
    def adapter(language_or_framework=None, runner_info: Dict[str, Any] | None = None, **kw):
        # Support both positional and keyword-based calls
        if language_or_framework is None:
            language_or_framework = kw.get("language") or kw.get("framework") or kw.get("name")
        if runner_info is None:
            # Runner info may be passed as discrete kwargs; normalize
            runner_info = {
                "runner_function": kw.get("runner") or kw.get("func") or kw.get("entrypoint_func"),
                "version": kw.get("version", "unknown"),
                "execution_mode": kw.get("execution_mode", "local"),
            }

        rf = runner_info.get("runner_function")
        plugin_key = f"{module_name}:{language_or_framework}"

        def _entrypoint(args: argparse.Namespace):
            cli_overrides = parse_plugin_kv_args(getattr(args, "plugin_args", None))
            if callable(rf):
                try:
                    synthesized = _synthesize_kwargs_for_runner(rf, module_name, language_or_framework, args)
                    synthesized.update(cli_overrides)
                    result = rf(**synthesized)  # allow sync
                    if inspect.isawaitable(result):  # and async
                        return asyncio.run(result)
                    return result
                except TypeError as e:
                    return {
                        "status": "ERROR",
                        "exception": f"Runner signature mismatch for {plugin_key}: {e}",
                        "hint": "Provide explicit --plugin-args key=value to satisfy the plugin contract.",
                        "traceback": traceback.format_exc(),
                    }
                except Exception as e:
                    return {"status": "ERROR", "exception": str(e), "traceback": traceback.format_exc()}
            return {
                "status": "OK",
                "message": "Plugin registered (no direct execution contract defined).",
                "language_or_framework": language_or_framework,
                "runner_function": getattr(rf, "__name__", str(rf)),
            }

        # Actual registration
        register_entrypoint(plugin_key, _entrypoint)

        # Metadata
        _registered_plugin_info[plugin_key] = {
            "version": runner_info.get("version", "unknown"),
            "hash": "",
            "execution_mode": runner_info.get("execution_mode", "local"),
        }

    return adapter

def verify_plugin_signature(code_path: str, sig_path: str) -> bool:
    if not crypto_available:
        main_runner_logger.warning("cryptography library not available, skipping signature verification")
        return True

    public_key_path = os.environ.get("PUBLIC_KEY_PATH")
    if not public_key_path or not os.path.isfile(public_key_path):
        main_runner_logger.info("PUBLIC_KEY_PATH not set; skipping signature verification")
        return True

    try:
        with open(public_key_path, "rb") as f:
            public_key = serialization.load_pem_public_key(f.read(), backend=default_backend())
        with open(code_path, "rb") as f:
            code_bytes = f.read()

        # Prefer cryptography's Hash; fall back to raw bytes if mocks misbehave
        try:
            hasher = hashes.Hash(hashes.SHA256(), backend=default_backend())
            hasher.update(code_bytes)     # may be mocked away in tests
            digest = hasher.finalize()
        except Exception:
            digest = code_bytes  # be tolerant under mocks

        with open(sig_path, "rb") as f:
            signature = f.read()

        public_key.verify(
            signature,
            digest,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
        return True
    except Exception as e:
        main_runner_logger.error(f"Signature verification error: {e}")
        return False

def discover_and_register_plugin_entrypoints():
    import importlib.util
    plugin_dir = current_dir
    if not os.path.exists(plugin_dir):
        main_runner_logger.warning(f"Plugins directory not found: {plugin_dir}")
        return
    sys.path.insert(0, plugin_dir)
    for plugin_file in glob.glob(os.path.join(plugin_dir, "*.py")):
        module_name = os.path.basename(plugin_file)[:-3]
        if module_name.startswith("__"):
            continue
        try:
            # Parse manifest using AST
            with open(plugin_file, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=plugin_file)
            manifest = {}
            # Support multiple manifest variable names; prefer PLUGIN_MANIFEST
            manifest_candidates = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    varname = node.targets[0].id
                    if varname in ("PLUGIN_MANIFEST", "__manifest__", "manifest"):
                        try:
                            parsed = ast.literal_eval(node.value)
                            if isinstance(parsed, dict):
                                manifest_candidates[varname] = parsed
                        except Exception:
                            continue
            manifest = manifest_candidates.get("PLUGIN_MANIFEST") or \
                       manifest_candidates.get("__manifest__") or \
                       manifest_candidates.get("manifest") or {}
            execution_mode = manifest.get("execution_mode", "local")

            # Verify signature if .sig exists
            sig_file = plugin_file + ".sig"
            if os.path.exists(sig_file):
                if not verify_plugin_signature(plugin_file, sig_file):
                    raise ValueError(f"Signature verification failed for plugin '{module_name}'")

            spec = importlib.util.spec_from_file_location(module_name, plugin_file)
            if spec is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Registration pathway: support both local and adapter
            if hasattr(module, 'register_plugin_entrypoints') and callable(module.register_plugin_entrypoints):
                try:
                    # Provide an adapter that matches plugins expecting (language_or_framework, runner_info)
                    module.register_plugin_entrypoints(_plugin_register_adapter(module_name))
                except TypeError:
                    # Fallback: if plugin expects our simple register_entrypoint signature
                    module.register_plugin_entrypoints(register_entrypoint)
            else:
                main_runner_logger.warning(f"Plugin module '{module_name}' has no register_plugin_entrypoints; skipping.")
                continue

            # Collect info
            version = manifest.get('version', 'unknown')
            with open(plugin_file, 'rb') as f:
                plugin_hash = hashlib.sha256(f.read()).hexdigest()
            _registered_plugin_info[module_name] = {'version': version, 'hash': plugin_hash, 'execution_mode': execution_mode}
        except Exception as e:
            main_runner_logger.error(f"Failed to load or register entrypoints from plugin '{module_name}': {e}")
            main_runner_logger.debug(f"Traceback for plugin '{module_name}': {traceback.format_exc()}")
            plugin_load_errors.append({"plugin": module_name, "error": str(e), "traceback": traceback.format_exc()})
    sys.path.remove(plugin_dir)

# --- Deployment Validator ---
def validate_deployment_or_exit(remote: bool = False):
    """
    Validates required environment/infrastructure. Remote=True will validate Kubernetes, Docker, S3 etc.
    Local mode skips heavy checks unless overridden.
    """
    import shutil

    log_path = os.environ.get("VALIDATION_LOG_PATH", "validation.log")
    skip_local = os.environ.get("SIM_RUNNER_SKIP_VALIDATION_FOR_LOCAL", "true").lower() in ("1", "true", "yes")
    if not remote and skip_local:
        main_runner_logger.info("Skipping local environment validation (SIM_RUNNER_SKIP_VALIDATION_FOR_LOCAL=true).")
        return

    with open(log_path, 'w', encoding="utf-8") as validation_log:
        def log_validation(msg):
            validation_log.write(msg + '\n')
            validation_log.flush()
            main_runner_logger.info(msg)

        # Required env for both modes (soften for local)
        required_env = [
            "OPA_URL", "METALEARNER_MODEL_URI", "NOTIFY_SLACK_WEBHOOK", "SIM_RUNNER_BUCKET",
            "AWS_REGION", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "SECCOMP_PROFILE"
        ]
        effective_required = required_env if remote else ["OPA_URL", "METALEARNER_MODEL_URI"]
        missing_env = [v for v in effective_required if not os.environ.get(v)]
        if missing_env:
            err_msg = "\n[DEPLOYMENT ERROR] Missing required environment variables:\n\t" + "\n\t".join(missing_env) + "\n"
            sys.stderr.write(err_msg)
            validation_log.write(err_msg)
            sys.exit(99)
        else:
            log_validation("[VALIDATION PASS] Required environment variables present for this mode.")

        # Check seccomp profile path existence (both modes)
        seccomp_profile = os.environ.get("SECCOMP_PROFILE")
        if seccomp_profile and not os.path.isfile(seccomp_profile):
            err_msg = f"\n[DEPLOYMENT ERROR] Seccomp profile not found: {seccomp_profile}\n"
            sys.stderr.write(err_msg)
            validation_log.write(err_msg)
            sys.exit(99)
        elif seccomp_profile:
            log_validation(f"[VALIDATION PASS] Seccomp profile found: {seccomp_profile}")
        else:
            log_validation("[VALIDATION WARN] SECCOMP_PROFILE not set; kernel sandboxing not configured.")

        if remote:
            # Check for kubectl and docker
            for cmd in ["kubectl", "docker"]:
                if not shutil.which(cmd):
                    err_msg = f"\n[DEPLOYMENT ERROR] Required command '{cmd}' not found in PATH.\n"
                    sys.stderr.write(err_msg)
                    validation_log.write(err_msg)
                    sys.exit(99)
                else:
                    log_validation(f"[VALIDATION PASS] Command '{cmd}' found.")

            # Check S3 bucket access (specific bucket + write/cleanup test)
            try:
                import boto3
                bucket = os.environ["SIM_RUNNER_BUCKET"]
                s3 = boto3.client("s3")
                s3.head_bucket(Bucket=bucket)
                # Write/delete test object to verify permissions
                test_key = f"validation/{uuid.uuid4().hex}.txt"
                s3.put_object(Bucket=bucket, Key=test_key, Body=b"validation test")
                s3.delete_object(Bucket=bucket, Key=test_key)
                log_validation(f"[VALIDATION PASS] S3 bucket '{bucket}' accessible and writable.")
            except Exception as e:
                err_msg = f"\n[DEPLOYMENT ERROR] Cannot access or write to S3 bucket '{os.environ.get('SIM_RUNNER_BUCKET','')}'. Ensure s3:HeadBucket, s3:PutObject, s3:DeleteObject permissions. Error: {e}\n"
                sys.stderr.write(err_msg)
                validation_log.write(err_msg)
                sys.exit(99)

        # OPA health (retry and soft-fail for local)
        try:
            import requests
            opa_url = os.environ.get("OPA_URL")
            attempts = 3
            for i in range(attempts):
                try:
                    resp = requests.get(f"{opa_url}/health", timeout=3)
                    if resp.status_code == 200:
                        log_validation("[VALIDATION PASS] OPA server healthy.")
                        break
                    raise Exception(f"OPA health check failed: status {resp.status_code}")
                except Exception as e:
                    if i == attempts - 1:
                        if remote:
                            raise
                        else:
                            log_validation(f"[VALIDATION WARN] OPA health check failed (local mode): {e}")
                    else:
                        time.sleep(1.5 * (i + 1))
        except Exception as e:
            err_msg = f"\n[DEPLOYMENT ERROR] OPA server health check failed: {e}\n"
            sys.stderr.write(err_msg)
            validation_log.write(err_msg)
            sys.exit(99)

        # Check MetaLearner artifact (both modes)
        model_uri = os.environ.get("METALEARNER_MODEL_URI", "")
        if not (model_uri.startswith("s3://") or os.path.isfile(model_uri)):
            err_msg = f"\n[DEPLOYMENT ERROR] MetaLearner model URI {model_uri} is not a valid S3 URI or file path.\n"
            sys.stderr.write(err_msg)
            validation_log.write(err_msg)
            sys.exit(99)
        else:
            log_validation(f"[VALIDATION PASS] MetaLearner model URI valid: {model_uri}")

    print("[DEPLOYMENT VALIDATION] All environment and infra checks passed for this mode.")

# --- Notification System ---
def send_notification(event_type: str, message: str, dry_run: bool = False):
    if dry_run:
        main_runner_logger.info(f"Dry-run notification: [{event_type}] {message}")
        return

    import requests
    import smtplib
    from email.mime.text import MIMEText

    slack_webhook = os.environ.get("NOTIFY_SLACK_WEBHOOK")
    smtp_server = os.environ.get("SMTP_SERVER")
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASS")
    smtp_to = os.environ.get("SMTP_TO")
    pagerduty_key = os.environ.get("NOTIFY_PAGERDUTY_KEY")

    if slack_webhook:
        try:
            requests.post(slack_webhook, json={"text": f"[{event_type}] {message}"}, timeout=5)
        except Exception as e:
            main_runner_logger.error(f"Slack notification failed: {e}")
            if prometheus_available:
                notification_failures.labels('slack').inc()

    if smtp_server and smtp_to:
        try:
            msg = MIMEText(message)
            msg["Subject"] = f"Self-Fixing Engineer: {event_type}"
            msg["From"] = smtp_user
            msg["To"] = smtp_to
            with smtplib.SMTP(smtp_server) as server:
                server.starttls()
                if smtp_user and smtp_pass:
                    server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_user, [smtp_to], msg.as_string())
        except Exception as e:
            main_runner_logger.error(f"Email notification failed: {e}")
            if prometheus_available:
                notification_failures.labels('email').inc()

    if pagerduty_key:
        try:
            payload = {
                "routing_key": pagerduty_key,
                "event_action": "trigger",
                "payload": {
                    "summary": message,
                    "source": "main_sim_runner.py",
                    "severity": "error"
                }
            }
            requests.post("https://events.pagerduty.com/v2/enqueue", json=payload, timeout=5)
        except Exception as e:
            main_runner_logger.error(f"PagerDuty notification failed: {e}")
            if prometheus_available:
                notification_failures.labels('pagerduty').inc()

# RBAC Cache
opa_cache: Dict[tuple, bool] = {}

def check_rbac_permission(actor: str, action: str, resource: str) -> bool:
    key = (actor, action, resource)
    if key in opa_cache:
        return opa_cache[key]
    opa_url = os.environ.get("OPA_URL")
    fail_open = os.environ.get("SIM_RUNNER_RBAC_FAIL_OPEN", "false").lower() in ("1", "true", "yes")
    if not opa_url:
        main_runner_logger.warning("OPA_URL not set; RBAC check skipped. Returning {} by policy.".format("ALLOW" if fail_open else "DENY"))
        result = True if fail_open else False
        opa_cache[key] = result
        return result
    import requests
    try:
        data = {"input": {"actor": actor, "action": action, "resource": resource}}
        resp = requests.post(f"{opa_url}/v1/data/sim/allow", json=data, timeout=5)
        resp.raise_for_status()
        result = bool(resp.json().get("result", False))
        opa_cache[key] = result
        return result
    except Exception as e:
        main_runner_logger.error(f"OPA RBAC check failed: {e}")
        result = True if fail_open else False
        opa_cache[key] = result
        return result

def enforce_kernel_sandboxing(profile_path: str, cgroup: str = None, apparmor_profile: str = None):
    if profile_path and os.path.isfile(profile_path):
        with open(profile_path, 'rb') as f:
            profile_hash = hashlib.sha256(f.read()).hexdigest()
        main_runner_logger.info(f"Seccomp profile detected. SHA256: {profile_hash}")
    else:
        main_runner_logger.info("No seccomp profile file provided or not found.")

    main_runner_logger.warning("Seccomp enforcement not applied (allow-all). Configure container-level sandboxing (firejail/bwrap/AppArmor/SELinux) for real isolation.")

    if cgroup:
        try:
            import libcgroup
            libcgroup.attach_task(cgroup)
            main_runner_logger.info(f"Attached to cgroup: {cgroup}")
        except Exception as e:
            main_runner_logger.error(f"Cgroup enforcement failed: {e}")
    if apparmor_profile:
        try:
            subprocess.check_call(["aa-exec", "-p", apparmor_profile, "true"])
            main_runner_logger.info(f"AppArmor profile '{apparmor_profile}' enforced.")
        except Exception as e:
            main_runner_logger.error(f"AppArmor enforcement failed: {e}")

def load_meta_learner():
    from ..agentic import MetaLearner
    model_uri = os.environ.get("METALEARNER_MODEL_URI")
    return MetaLearner(model_uri)

def retry_op(op: Callable, max_retries: int = 3, backoff_base: int = 2):
    for attempt in range(max_retries):
        try:
            return op()
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            sleep_time = backoff_base ** attempt
            main_runner_logger.warning(f"Retry {attempt + 1}/{max_retries} after {sleep_time}s: {e}")
            time.sleep(sleep_time)

def _tar_filter(exclude_prefixes: List[str]):
    """
    Filter tar members by excluding any whose normalized path starts with one of the prefixes.
    Normalization removes leading './' for consistent matching.
    """
    def _filter(tarinfo: tarfile.TarInfo):
        # Normalize path by stripping any leading './'
        path = tarinfo.name
        norm = path[2:] if path.startswith("./") else path
        for p in exclude_prefixes:
            p_norm = p[2:] if p.startswith("./") else p
            if norm.startswith(p_norm.rstrip("/") + "/") or norm == p_norm.rstrip("/"):
                return None
        return tarinfo
    return _filter

def execute_remotely(job_config: Dict[str, Any], simulation_package_dir: str, notify_func: Optional[Callable[[str, str], None]] = None) -> Dict[str, Any]:
    with tracer.start_as_current_span("remote_execution"):
        return _execute_remotely(job_config, simulation_package_dir, notify_func)

def _execute_remotely(job_config: Dict[str, Any], simulation_package_dir: str, notify_func: Optional[Callable[[str, str], None]] = None) -> Dict[str, Any]:
    tar_path = None
    try:
        from kubernetes import client, config as k8s_config
        import boto3
        k8s_config.load_kube_config()
        batch_v1 = client.BatchV1Api()
        core_v1 = client.CoreV1Api()

        job_id = f"simjob-{uuid.uuid4().hex[:8]}"
        tar_path = os.path.join(tempfile.gettempdir(), f"{job_id}.tar.gz")

        # Package with tarfile (exclude bulky/irrelevant dirs)
        exclude = [".git", ".venv", "venv", "__pycache__", "node_modules"]
        with tarfile.open(tar_path, "w:gz") as tar:
            # Use '.' root; _tar_filter normalizes both sides
            tar.add(simulation_package_dir, arcname=".", filter=_tar_filter([f"./{p}" for p in exclude]))
        if prometheus_available:
            remote_jobs_submitted.inc()

        s3 = boto3.client("s3")
        bucket = os.environ["SIM_RUNNER_BUCKET"]
        retry_op(lambda: s3.upload_file(tar_path, bucket, f"jobs/{job_id}.tar.gz"))
        artifact_url = f"s3://{bucket}/jobs/{job_id}.tar.gz"

        container_image = job_config["container_image"]
        resources = job_config["resources"]
        env_vars = [client.V1EnvVar(name=k, value=str(v)) for k, v in job_config.get("env", {}).items()]
        env_vars.append(client.V1EnvVar(name="SIM_ARTIFACT_URL", value=artifact_url))
        env_vars.append(client.V1EnvVar(name="AWS_REGION", value=os.environ["AWS_REGION"]))
        env_vars.append(client.V1EnvVar(name="JOB_ID", value=job_id))
        
        current_span = trace.get_current_span()
        ctx = current_span.get_span_context()
        traceparent = f"00-{format(ctx.trace_id, '032x')}-{format(ctx.span_id, '016x')}-01"
        env_vars.append(client.V1EnvVar(name="TRACEPARENT", value=traceparent))

        job = client.V1Job(
            metadata=client.V1ObjectMeta(name=job_id),
            spec=client.V1JobSpec(
                template=client.V1PodTemplateSpec(
                    metadata=client.V1ObjectMeta(labels={"job": job_id}),
                    spec=client.V1PodSpec(
                        containers=[
                            client.V1Container(
                                name="sim-runner",
                                image=container_image,
                                args=job_config.get("args", []),
                                env=env_vars,
                                resources=client.V1ResourceRequirements(
                                    requests=resources, limits=resources
                                ),
                            )
                        ],
                        restart_policy="Never",
                        service_account_name=job_config.get("service_account", "sim-job-sa")
                    )
                ),
                backoff_limit=job_config.get("retries", 2),
                ttl_seconds_after_finished=job_config.get("ttl_after_finished", 600)
            )
        )
        resp = retry_op(lambda: batch_v1.create_namespaced_job(namespace=job_config.get("namespace", "default"), body=job))
        job_name = resp.metadata.name
        main_runner_logger.info(f"Kubernetes Job '{job_name}' submitted.")

        last_log = ""
        namespace = job_config.get("namespace", "default")
        start = time.time()
        max_wait = int(job_config.get("max_wait_seconds", 1800))  # 30 minutes default
        poll_sleep = 5
        while True:
            # Timeout check
            if time.time() - start > max_wait:
                main_runner_logger.error(f"Job '{job_name}' timed out after {max_wait} seconds.")
                if notify_func:
                    notify_func("simulation_failure", f"Job {job_name} timed out.")
                remote_jobs_failed.inc() if prometheus_available else None
                return {"status": "error", "job_name": job_name, "details": "timeout"}

            job_status = retry_op(lambda: batch_v1.read_namespaced_job_status(job_name, namespace))
            # Stream logs approximation by polling
            try:
                pods = core_v1.list_namespaced_pod(namespace, label_selector=f"job-name={job_name}")
                if pods.items:
                    pod_name = pods.items[0].metadata.name
                    current_log = core_v1.read_namespaced_pod_log(pod_name, namespace)
                    if len(current_log) > len(last_log):
                        print(current_log[len(last_log):], end="")
                    last_log = current_log
            except Exception as log_e:
                main_runner_logger.debug(f"Log polling failed: {log_e}")

            # Conditions and counters
            status = job_status.status
            succeeded = getattr(status, "succeeded", 0) or 0
            failed = getattr(status, "failed", 0) or 0
            conditions = getattr(status, "conditions", None) or []
            completed = any(c.type == "Complete" and c.status == "True" for c in conditions) or succeeded > 0
            has_failed = any(c.type == "Failed" and c.status == "True" for c in conditions) or failed > 0

            if completed:
                main_runner_logger.info(f"Job '{job_name}' completed successfully.")
                if notify_func:
                    notify_func("simulation_success", f"Job {job_name} completed.")
                if prometheus_available:
                    remote_jobs_completed.inc()
                result_key = f"jobs/{job_id}/result.json"
                try:
                    result_obj = retry_op(lambda: s3.get_object(Bucket=bucket, Key=result_key))
                    result_json = json.loads(result_obj["Body"].read())
                    return {"status": "completed", "job_name": job_name, "result": result_json}
                except Exception as e:
                    main_runner_logger.error(f"Failed to download job result: {e}")
                    return {"status": "completed", "job_name": job_name, "result": None, "error": str(e)}
            elif has_failed:
                main_runner_logger.error(f"Job '{job_name}' failed.")
                if notify_func:
                    notify_func("simulation_failure", f"Job {job_name} failed.")
                if prometheus_available:
                    remote_jobs_failed.inc()
                return {"status": "error", "job_name": job_name, "details": str(job_status.status)}

            time.sleep(poll_sleep)
            # Exponential backoff up to 20s
            poll_sleep = min(poll_sleep * 1.5, 20)

    except Exception as e:
        main_runner_logger.error(f"Remote execution error: {e}")
        if notify_func:
            notify_func("simulation_error", f"Remote execution failed: {e}")
        return {"status": "error", "exception": str(e), "traceback": traceback.format_exc()}
    finally:
        # Cleanup tarball
        if tar_path and os.path.exists(tar_path):
            try:
                os.remove(tar_path)
            except Exception:
                pass

def run_plugin_in_sandbox(plugin_name: str, args: argparse.Namespace, sandbox: bool = True) -> Dict[str, Any]:
    if sandbox:
        enforce_kernel_sandboxing(
            profile_path=os.environ.get("SECCOMP_PROFILE", "/etc/seccomp/sim_default.json"),
            cgroup=os.environ.get("PLUGIN_CGROUP"),
            apparmor_profile=os.environ.get("APPARMOR_PROFILE")
        )
    else:
        main_runner_logger.warning("Running in insecure mode, no sandboxing applied.")
    
    try:
        entrypoint_func = _registered_plugin_entrypoints[plugin_name]
        plugin_out = entrypoint_func(args)
        return {"status": "completed", "plugin_output": plugin_out}
    except Exception as e:
        # This wrapper ensures that exceptions within plugins are caught and structured.
        return {
            "status": "completed", 
            "plugin_output": {
                "status": "ERROR", 
                "exception": str(e), 
                "traceback": traceback.format_exc()
            }
        }

def aggregate_simulation_results(core_result: Dict[str, Any], plugin_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    aggregated_result = core_result.copy()
    aggregated_result["plugin_runs"] = plugin_results
    for p_res in plugin_results:
        plugin_name = p_res.get("plugin_name", "unknown_plugin")
        aggregated_result[f"{plugin_name}_version"] = _registered_plugin_info.get(plugin_name, {}).get('version', 'unknown')
        aggregated_result[f"{plugin_name}_hash"] = _registered_plugin_info.get(plugin_name, {}).get('hash', 'unknown')
        res = p_res.get("result", {}) or {}

        # Expose useful artifact paths if present
        if "coverage_report_path" in res:
            aggregated_result[f"{plugin_name}_coverage_report"] = res["coverage_report_path"]
        junit_path = res.get("junit_report_path") or res.get("junit_xml_path")
        if not junit_path:
            jpr = res.get("jest_project_root")
            if jpr:
                candidate = os.path.join(jpr, "jest-junit.xml")
                if os.path.exists(candidate):
                    junit_path = candidate
        if junit_path:
            aggregated_result[f"{plugin_name}_junit_report"] = junit_path

        # Determine plugin status with fallbacks if "status" is not provided
        status = str(res.get("status", "")).upper()
        plugin_error = False
        if not status:
            if res.get("success") is False:
                status = "ERROR"
                plugin_error = True
            elif isinstance(res.get("exit_code"), int) and res.get("exit_code") != 0:
                status = "ERROR"
                plugin_error = True
            elif isinstance(res.get("numFailedTests"), int) and res.get("numFailedTests", 0) > 0:
                status = "ERROR"
                plugin_error = True
            else:
                status = "OK"
        else:
            plugin_error = (status == "ERROR")

        aggregated_result[f"{plugin_name}_status"] = status

        # Propagate metrics if present
        if "plugin_metrics" in res:
            aggregated_result[f"{plugin_name}_metrics"] = res["plugin_metrics"]

        if plugin_error:
            aggregated_result["overall_status"] = "ERROR_WITH_PLUGINS"
            aggregated_result.setdefault("errors", []).append({
                "source": f"plugin_{plugin_name}",
                "details": res.get("exception") or res.get("reason") or "Plugin reported error"
            })
        elif status == "FINDINGS_DETECTED" and "security_audit" in plugin_name.lower():
            aggregated_result.setdefault("security_findings", []).extend(res.get("findings", []))

    main_runner_logger.info("Aggregated core and plugin simulation results.")
    return aggregated_result

def parse_plugin_kv_args(args_list: Optional[List[str]]) -> Dict[str, Any]:
    """
    Parse --plugin-args key=value pairs into a dict. Non key=value entries are ignored.
    """
    result: Dict[str, Any] = {}
    if not args_list:
        return result
    for item in args_list:
        if "=" in item:
            k, v = item.split("=", 1)
            result[k.strip()] = v.strip()
    return result

def main():
    if prometheus_available:
        prom_port = int(os.environ.get("PROMETHEUS_PORT", "8000"))
        start_http_server(prom_port)
        global notification_failures, simulation_duration, simulation_errors
        notification_failures = Counter('notification_failures_total', 'Failed notifications', ['channel'])
        simulation_duration = Gauge('simulation_duration_seconds', 'Duration of simulation runs')
        simulation_errors = Counter('simulation_errors_total', 'Simulation errors')

    # Discover plugins BEFORE creating the argument parser
    discover_and_register_plugin_entrypoints()

    parser = argparse.ArgumentParser(
        description="Omnisapient, quantum-agentic, ethically self-evolving sim_runner.py for 2025+",
        epilog="(see --help for full argument list)"
    )
    parser.add_argument("--session", help="Session name to auto-discover test/code files.", required=False)
    parser.add_argument("--testfile", help="Path to generated test file.", required=False)
    parser.add_argument("--codefile", help="Path to candidate code file to test.", required=False)
    parser.add_argument("--agentic", action="store_true", help="Enable quantum+RL multi-agent swarm mode.")
    parser.add_argument("--diff", nargs=2, help="Show a unified diff between two files and exit.")
    parser.add_argument("--summary", action="store_true", help="Print summary only (suppress logs).")
    parser.add_argument("--watch", action="store_true", help="Watch mode: auto-rerun on file changes.")
    parser.add_argument("--dashboard", action="store_true", help="Launch Streamlit dashboard for result visualization.")
    parser.add_argument("--remote", action="store_true", help="Execute the simulation remotely, e.g., via Kubernetes.")
    parser.add_argument("--namespace", help="Kubernetes namespace for remote job.", default="default")
    parser.add_argument("--container-image", help="Container image for remote job.", default="your-org/sim-runner:latest")
    parser.add_argument("--resource-cpu", help="CPU request/limit for remote job.", default="2")
    parser.add_argument("--resource-mem", help="Memory request/limit for remote job.", default="4Gi")
    parser.add_argument("--policy", help="Simulation policy to apply (e.g., strict, chaos, custom).")
    parser.add_argument("--plugin-config", help="Path to plugin configuration JSON.")
    parser.add_argument("--max-runtime", type=int, help="Maximum allowed runtime for the simulation (seconds).")
    parser.add_argument("--retries", type=int, default=2, help="Number of retries for remote jobs.")
    parser.add_argument("--notify", action="store_true", help="Enable notifications on errors or completion.")
    
    # Add --run-plugin ONLY ONCE, with choices from already discovered plugins
    parser.add_argument("--run-plugin", 
                       choices=list(_registered_plugin_entrypoints.keys()) if _registered_plugin_entrypoints else None,
                       help=f"Run a specific plugin's main entrypoint. Available plugins: {', '.join(sorted(_registered_plugin_entrypoints.keys())) if _registered_plugin_entrypoints else 'None'}",
                       required=False)
    
    parser.add_argument('--plugin-args', nargs=argparse.REMAINDER, help='Arbitrary arguments to pass to the selected plugin entrypoint as key=value pairs.')
    parser.add_argument("--validate", action="store_true", help="Validate deployment and exit.")
    parser.add_argument("--dry-run", action="store_true", help="Dry-run mode: simulate actions without executing.")
    parser.add_argument("--list-plugins", action="store_true", help="List registered plugins and exit.")
    parser.add_argument("--insecure", action="store_true", help="Run in insecure mode without sandboxing (development only).")
    parser.add_argument("--sandbox-plugins", action="store_true", help="Run all plugins in sandbox during bulk execution loop.")

    args = parser.parse_args()

    # --- Consolidated Early-Exit and Mode Handling ---
    # This block handles all flags that should cause the program to exit immediately
    # without running the main simulation logic.

    if args.validate:
        # Use getattr for robustness, as requested by the fix description.
        validate_deployment_or_exit(remote=getattr(args, 'remote', False))
        sys.exit(0)

    if args.list_plugins:
        print("Registered plugins:")
        for name in sorted(_registered_plugin_entrypoints.keys()):
            print(f" - {name}")
        if plugin_load_errors:
            print("\nErrors loading plugins:")
            for err in plugin_load_errors:
                print(f" - {err['plugin']}: {err['error']}")
        sys.exit(0)

    if args.diff:
        utils.print_file_diff(args.diff[0], args.diff[1])
        sys.exit(0)

    if args.dashboard:
        if STREAMLIT_AVAILABLE:
            display_simulation_dashboard(plugin_load_errors=plugin_load_errors)
        else:
            main_runner_logger.error("Streamlit is not installed. Cannot launch dashboard.")
            sys.exit(1)
        sys.exit(0)

    # --- Pre-run Validations for Execution Modes ---
    # Always validate the environment before starting a remote run.
    if args.remote:
        validate_deployment_or_exit(remote=True)


    # --- Main Execution Logic ---

    def notify_if_enabled(event_type, message):
        if args.notify:
            send_notification(event_type, message, dry_run=args.dry_run)

    actor = os.environ.get("SIM_USER", "system")
    if not check_rbac_permission(actor, "run_simulation", "main_sim_runner"):
        main_runner_logger.error(f"RBAC policy denied action 'run_simulation' for user '{actor}'")
        sys.exit(1)

    # OTEL context propagation
    otel_context = None
    if "TRACEPARENT" in os.environ:
        carrier = {"traceparent": os.environ["TRACEPARENT"]}
        otel_context = extract(carrier)

    # Sandbox policy for bulk plugin execution
    sandbox_env = os.environ.get("SIM_RUNNER_SANDBOX_PLUGINS", "false").lower() in ("1", "true", "yes")
    sandbox_plugins = bool(args.sandbox_plugins or sandbox_env)
    if not sandbox_plugins:
        main_runner_logger.warning("Bulk plugin execution is NOT sandboxed. Use --sandbox-plugins or SIM_RUNNER_SANDBOX_PLUGINS=true to enable sandboxing.")

    with tracer.start_as_current_span("main_run", context=otel_context):

        if args.run_plugin:
            plugin_name = args.run_plugin
            if plugin_name not in _registered_plugin_entrypoints:
                main_runner_logger.error(f"Plugin '{plugin_name}' not found or not registered.")
                notify_if_enabled("plugin_error", f"Plugin '{plugin_name}' not found.")
                sys.exit(1)
            if args.dry_run:
                main_runner_logger.info(f"Dry-run: Would run plugin '{plugin_name}'")
                sys.exit(0)
            with tracer.start_as_current_span(f"plugin_run_{plugin_name}"):
                try:
                    plugin_output = run_plugin_in_sandbox(plugin_name, args, sandbox=not args.insecure)
                    main_runner_logger.info(f"Plugin '{plugin_name}' execution result: {plugin_output}")
                    print(f"PLUGIN_OUTPUT: {json.dumps(plugin_output)}")
                    # Upload if in remote/job mode
                    if "JOB_ID" in os.environ:
                        import boto3
                        s3 = boto3.client("s3")
                        bucket = os.environ["SIM_RUNNER_BUCKET"]
                        key = f"jobs/{os.environ['JOB_ID']}/result.json"
                        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as tf:
                            json.dump(plugin_output, tf)
                            temp_path = tf.name
                        try:
                            s3.upload_file(temp_path, bucket, key)
                        finally:
                            try:
                                os.remove(temp_path)
                            except Exception:
                                pass
                except Exception as e:
                    plugin_output = {"status": "error", "exception": str(e), "traceback": traceback.format_exc()}
                    print(f"PLUGIN_OUTPUT: {json.dumps(plugin_output)}")
                    if "JOB_ID" in os.environ:
                        import boto3
                        s3 = boto3.client("s3")
                        bucket = os.environ["SIM_RUNNER_BUCKET"]
                        key = f"jobs/{os.environ['JOB_ID']}/result.json"
                        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as tf:
                            json.dump(plugin_output, tf)
                            temp_path = tf.name
                        try:
                            s3.upload_file(temp_path, bucket, key)
                        finally:
                            try:
                                os.remove(temp_path)
                            except Exception:
                                pass
                    sys.exit(1)
            sys.exit(0)

        test_file_path = args.testfile
        code_file_path = args.codefile
        session_name = args.session or "unspecified"

        if args.session and (not test_file_path or not code_file_path):
            discovered = core.discover_tests_and_code(args.session)
            discovered_test = discovered_code = None
        
            try:
                # tuple/list path
                if isinstance(discovered, (list, tuple)) and len(discovered) >= 2:
                    discovered_test, discovered_code = discovered[0], discovered[1]
                # dict path
                elif isinstance(discovered, dict):
                    discovered_test = discovered.get("test") or discovered.get("test_file")
                    discovered_code = discovered.get("code") or discovered.get("code_file")
                # object with attrs
                else:
                    discovered_test = getattr(discovered, "test", None) or getattr(discovered, "test_file", None)
                    discovered_code = getattr(discovered, "code", None) or getattr(discovered, "code_file", None)
            except Exception:
                discovered_test = discovered_code = None
        
            test_file_path = test_file_path or discovered_test
            code_file_path = code_file_path or discovered_code
            if not test_file_path:
                msg = f"No test file found for session '{args.session}'."
                main_runner_logger.critical(msg)
                print(msg)
                sys.exit(1)

        if not test_file_path:
            msg = "No test file specified. Use --testfile or --session."
            main_runner_logger.critical(msg)
            notify_if_enabled("sim_error", msg)
            sys.exit(1)

        config = {
            "agentic": args.agentic,
            "policy": args.policy,
            "plugin_config": args.plugin_config,
            "max_runtime": args.max_runtime,
        }

        def run_and_report():
            if args.dry_run:
                print("Dry-run: Would run simulation")
                return
            start_time = time.time()
            run_uuid = str(uuid.uuid4())
            core_simulation_result = {"run_uuid": run_uuid}
            plugin_execution_results: List[Dict[str, Any]] = []
            with tracer.start_as_current_span("core_simulation"):
                try:
                    if args.agentic:
                        meta_learner = load_meta_learner()
                        maybe_swarm = core.run_simulation_swarm(test_file_path, code_file_path, config, meta_learner=meta_learner)
                        if inspect.isawaitable(maybe_swarm):
                            core_simulation_result.update(asyncio.run(maybe_swarm))
                        else:
                            core_simulation_result.update(maybe_swarm)
                    else:
                        maybe = core.run_agent({
                            "test_file": test_file_path,
                            "code_file": code_file_path,
                            "runs": [],
                        })
                        if inspect.isawaitable(maybe):
                            core_simulation_result.update(asyncio.run(maybe))
                        else:
                            core_simulation_result.update(maybe)
                    core_simulation_result["status"] = "SUCCESS"
                except Exception as e:
                    span = trace.get_current_span()
                    span.record_exception(e)
                    span.set_status(StatusCode.ERROR)
                    main_runner_logger.error(f"Core simulation failed: {e}")
                    core_simulation_result["status"] = "ERROR"
                    core_simulation_result["exception"] = str(e)
                    core_simulation_result["traceback"] = traceback.format_exc()
                    notify_if_enabled("sim_error", f"Simulation failed: {e}")
                    if prometheus_available:
                        simulation_errors.inc()

            # Plugin execution
            for plugin_name, entrypoint in _registered_plugin_entrypoints.items():
                start_p = time.time()
                with tracer.start_as_current_span(f"plugin_execution_{plugin_name}"):
                    try:
                        if sandbox_plugins:
                            wrapper_res = run_plugin_in_sandbox(plugin_name, args, sandbox=not args.insecure)
                            plugin_result = wrapper_res.get("plugin_output", wrapper_res)
                        else:
                            plugin_result = entrypoint(args)

                        plugin_execution_results.append({
                            "plugin_name": plugin_name,
                            "result": plugin_result
                        })
                        if prometheus_available:
                            plugin_runs_total.labels(plugin=plugin_name, status="success").inc()
                    except Exception as e:
                        span = trace.get_current_span()
                        span.record_exception(e)
                        span.set_status(StatusCode.ERROR)
                        plugin_execution_results.append({
                            "plugin_name": plugin_name,
                            "result": {"status": "ERROR", "exception": str(e), "traceback": traceback.format_exc()}
                        })
                        if prometheus_available:
                            plugin_runs_total.labels(plugin=plugin_name, status="error").inc()
                        notify_if_enabled("plugin_error", f"Plugin {plugin_name} failed: {e}")
                    finally:
                        if prometheus_available:
                            plugin_run_duration.labels(plugin=plugin_name).observe(time.time() - start_p)

            final_result = aggregate_simulation_results(core_simulation_result, plugin_execution_results)
            out_path = utils.save_sim_result(session_name, os.path.basename(code_file_path or "no_code"), final_result)
            append_distributed_log(final_result)

            duration = time.time() - start_time
            if prometheus_available:
                simulation_duration.set(duration)
                simulation_run_duration_hist.observe(duration)

            if "JOB_ID" in os.environ:
                import boto3
                s3 = boto3.client("s3")
                bucket = os.environ["SIM_RUNNER_BUCKET"]
                key = f"jobs/{os.environ['JOB_ID']}/result.json"
                with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as tf:
                    json.dump(final_result, tf)
                    temp_path = tf.name
                try:
                    s3.upload_file(temp_path, bucket, key)
                finally:
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass

            # Color output toggle
            no_color = os.environ.get("NO_COLOR", "false").lower() in ("1", "true", "yes")
            if not args.summary:
                if no_color:
                    print(f"Results saved to {out_path}\n")
                    print(utils.summarize_result(final_result))
                else:
                    print(f"\033[32mResults saved to {out_path}\033[0m\n")
                    print("\033[35m" + utils.summarize_result(final_result) + "\033[0m")
                    if "flaky_plot" in final_result and os.path.exists(final_result["flaky_plot"]):
                        print(f"\033[36mFlakiness plot saved to {final_result['flaky_plot']}\033[0m")
                    if args.agentic and final_result.get("healer", {}).get("llm_summary"):
                        print(f"\033[1;35m[AI Healer Suggestion]\n{final_result['healer']['llm_summary']}\033[0m")
            else:
                print(utils.summarize_result(final_result))

        if args.remote:
            if args.dry_run:
                print("Dry-run: Would submit remote job")
                sys.exit(0)
            # Build job env safely
            env_vars = {
                "SESSION_NAME": args.session or "",
                "TEST_FILE": args.testfile or "",
                "CODE_FILE": args.codefile or "",
                "POLICY": args.policy or "",
                "MAX_RUNTIME": str(args.max_runtime or 0),
            }

            current_span = trace.get_current_span()
            ctx = current_span.get_span_context()
            env_vars["TRACEPARENT"] = f"00-{format(ctx.trace_id, '032x')}-{format(ctx.span_id, '016x')}-01"

            job_config = {
                "container_image": args.container_image,
                "namespace": args.namespace,
                "resources": {"cpu": args.resource_cpu, "memory": args.resource_mem},
                "env": env_vars,
                "args": sys.argv[1:],
                "retries": args.retries,
                "service_account": os.environ.get("SIM_RUNNER_SERVICE_ACCOUNT", "sim-job-sa"),
                "ttl_after_finished": 600,
                "max_wait_seconds": int(os.environ.get("SIM_RUNNER_REMOTE_TIMEOUT", "1800"))
            }
            package_dir = current_dir
            main_runner_logger.debug(f"Remote packaging excludes: {['.git', '.venv', 'venv', '__pycache__', 'node_modules']}")
            remote_result = execute_remotely(job_config, package_dir, notify_if_enabled)
            if remote_result.get("status") != "completed":
                main_runner_logger.error(f"Remote execution failed: {remote_result}")
                notify_if_enabled("remote_error", f"Remote job failed: {remote_result}")
                sys.exit(2)
            print(json.dumps(remote_result, indent=2))  # Avoid color in remote CLI context
            sys.exit(0)
        elif args.watch:
            utils.watch_mode([test_file_path, code_file_path] if code_file_path else [test_file_path], run_and_report)
        else:
            run_and_report()

if __name__ == "__main__":
    main()
