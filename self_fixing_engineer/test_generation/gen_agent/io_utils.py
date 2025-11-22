import os
import json
import logging
import gzip
import tenacity
from functools import lru_cache
from typing import Dict, Any, Optional, DefaultDict, Set
import shutil
import asyncio
from collections import defaultdict
from pathlib import Path
import tempfile
from contextlib import contextmanager
import datetime
from datetime import timezone

# --- Logging must be available before any module-level side effects ---
logger = logging.getLogger(__name__)

# --- Prometheus metrics (safe registration) ---
_warned_metrics: Set[str] = set()
try:
    from prometheus_client import Histogram, Counter, REGISTRY
    _PROM_OK = True
except Exception as e:  # missing lib or other import issues
    logger.warning("Prometheus not available (%s). Metrics disabled.", e)
    _PROM_OK = False
    Histogram = Counter = REGISTRY = None  # type: ignore

class _NoopMetric:
    def labels(self, *_, **__): return self
    def observe(self, *_, **__): pass
    def inc(self, *_, **__): pass
    @contextmanager
    def time(self, *_, **__):
        yield


def _register_metric(factory, *args, **kwargs):
    """Register a metric, but fall back to a no-op on duplicate/any error."""
    if not _PROM_OK:
        return _NoopMetric()
    metric_name = args[0] if args else "<unknown>"
    # Fixed: Use safer approach to check for existing metrics
    try:
        if hasattr(REGISTRY, '_names_to_collectors') and REGISTRY._names_to_collectors.get(metric_name):
            # FIX: Directly return the existing metric to prevent registration warnings.
            if metric_name not in _warned_metrics:
                logger.debug(
                    "Prometheus metric '%s' already registered. Returning existing.",
                    metric_name
                )
                _warned_metrics.add(metric_name)
            return REGISTRY._names_to_collectors.get(metric_name)
    except AttributeError:
        # If _names_to_collectors doesn't exist in future versions, continue with registration
        pass
    try:
        # Always bind to the default REGISTRY explicitly to avoid surprises
        return factory(*args, registry=REGISTRY, **kwargs)
    except Exception as err:
        # Most commonly: ValueError: Duplicated timeseries in CollectorRegistry
        if metric_name not in _warned_metrics:
            logger.warning(
                "Prometheus metric '%s' already registered or failed (%s). Using no-op.",
                metric_name, err
            )
            _warned_metrics.add(metric_name)
        return _NoopMetric()

# Create metrics with duplicate-safe registration
io_write_duration = _register_metric(
    Histogram, 'io_write_duration_seconds', 'Duration of IO write operations', ['file']
)
io_read_duration = _register_metric(
    Histogram, 'io_read_duration_seconds', 'Duration of IO read operations', ['file']
)
io_write_bytes = _register_metric(
    Counter, 'io_write_bytes_total', 'Total bytes written by io utils', ['file']
)


try:
    import pandas as pd
except ImportError:
    pd = None


from .runtime import AIOFILES_AVAILABLE, FILELOCK_AVAILABLE, AUDIT_LOGGER_AVAILABLE, audit_logger, redact_sensitive
from test_generation.orchestrator.config import PROJECT_ROOT, CONFIG
from test_generation.orchestrator.audit import audit_event, _json_serializable_default
from test_generation.orchestrator.venvs import sanitize_path as _sanitize_path

# lazy imports; only import if runtime flags say it’s available (tolerate mismatches)
try:
    if 'FILELOCK_AVAILABLE' in globals() and FILELOCK_AVAILABLE:
        import filelock  # type: ignore
    else:
        filelock = None  # type: ignore
except Exception:
    filelock = None  # type: ignore
try:
    if 'AIOFILES_AVAILABLE' in globals() and AIOFILES_AVAILABLE:
        import aiofiles  # type: ignore
    else:
        aiofiles = None  # type: ignore
except Exception:
    aiofiles = None  # type: ignore


FEEDBACK_COMPRESS_BYTES = 1024 * 1024  # 1 MB

@contextmanager
def _noop_lock():
    """A context manager that does nothing, used as a fallback for filelock."""
    yield

# -------------------------------------------
# path helpers
# -------------------------------------------
def validate_and_resolve_path(path: str) -> str:
    """
    Validate path and return absolute normalized path, preventing directory traversal.
    Allows paths within the current working directory or the system's temp directory.
    """
    if not isinstance(path, str) or not path.strip():
        raise ValueError("Invalid path: empty path is not allowed.")

    if ".." in Path(path).parts:
        raise ValueError("Invalid path: Path components must not contain '..'.")

    resolved_path = Path(path).resolve()
    cwd = Path.cwd().resolve()
    temp_dir = Path(tempfile.gettempdir()).resolve()

    # Check if the resolved path is within the current working directory.
    try:
        resolved_path.relative_to(cwd)
        return str(resolved_path)  # Path is valid (within CWD)
    except ValueError:
        # If not in CWD, check if it's within the system's temp directory.
        try:
            resolved_path.relative_to(temp_dir)
            return str(resolved_path)  # Path is valid (within temp dir)
        except ValueError:
            # The path is outside both allowed directories, raise an error.
            raise ValueError(
                "Invalid path: Path traversal outside the current working directory or system temp directory is not allowed."
            )


def _canonical_lock_path(path: str) -> str:
    """
    Use a single lock filename for both .jsonl and .jsonl.gz variants.
    Ensures 'foo.jsonl' and 'foo.jsonl.gz' both → 'foo.jsonl.lock'.
    The logic is case-insensitive for the .gz extension.
    """
    base, ext = os.path.splitext(path)
    return f"{base}.lock" if ext.lower() == ".gz" else f"{path}.lock"

def _active_log_path(resolved_path: str) -> str:
    """
    Pick the actual file we should read/write:
    - if caller gave .gz, use it
    - else if a .gz sibling already exists, prefer the newer of the two
    - else use the plain path
    """
    if resolved_path.endswith(".gz"):
        return resolved_path
    gz = resolved_path + ".gz"
    if os.path.exists(gz):
        if not os.path.exists(resolved_path):
            return gz
        # both exist → prefer the newer (usually gz after migration)
        return gz if os.path.getmtime(gz) >= os.path.getmtime(resolved_path) else resolved_path
    return resolved_path

# backwards-compat alias
def validate_relative_path(path: str) -> str:
    try:
        safe_path = _sanitize_path(path, PROJECT_ROOT)
        return safe_path
    except ValueError as e:
        logger.error("Path validation failed for %s: %s", path, e, exc_info=True)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(audit_event("path_validation_failed", {"path": path, "error": str(e)}, critical=True))
        else:
            loop.create_task(audit_event("path_validation_failed", {"path": path, "error": str(e)}, critical=True))
        raise

class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "event": record.getMessage(),
            "level": record.levelname,
            "timestamp": datetime.datetime.now(timezone.utc).isoformat(),
            "details": redact_sensitive(getattr(record, "extra", {}) or {})
        }
        try:
            return json.dumps(log_entry, default=_json_serializable_default)
        except TypeError as e:
            logger.error("Failed to serialize log entry: %s", e, exc_info=True)
            return json.dumps({"event": "log_serialization_failed", "level": "ERROR", "error": str(e)})


# -------------------------------------------
# write
# -------------------------------------------
@tenacity.retry(
    stop=tenacity.stop_after_attempt(3),
    wait=tenacity.wait_exponential(multiplier=1, min=1, max=10),
    reraise=True
)
async def append_to_feedback_log(feedback_log_path: str, feedback_data: Dict[str, Any], config: Optional[Dict] = None) -> None:
    """
    Atomically append a line of JSON, supporting transparent on-the-fly gzip
    migration, and consistent locking across plain/gz variants.
    """
    if not isinstance(feedback_log_path, str):
        raise ValueError("Path must be a string")
        
    resolved = validate_and_resolve_path(feedback_log_path)
    target   = _active_log_path(resolved)

    dirpath = os.path.dirname(target)
    if dirpath:
        os.makedirs(dirpath, exist_ok=True)

    redacted = redact_sensitive(feedback_data)
    line = json.dumps(redacted, ensure_ascii=False, default=str, separators=(",", ":")) + "\n"
    line_bytes = len(line.encode('utf-8'))


    conf = config or {}
    size_trigger = int(os.getenv("FEEDBACK_COMPRESS_BYTES", str(FEEDBACK_COMPRESS_BYTES)))
    compress_requested = bool(conf.get("enable_compression", False))

    current_size = os.path.getsize(target) if os.path.exists(target) else 0
    potential_size = current_size + line_bytes
    
    should_migrate = (
        not target.endswith(".gz") and 
        (compress_requested or potential_size > size_trigger)
    )

    lock_path = _canonical_lock_path(target)
    
    lock_timeout = float(os.getenv("IO_LOCK_TIMEOUT", "10"))
    lock = (filelock.FileLock(lock_path, timeout=lock_timeout)
            if filelock is not None else _noop_lock())
    if filelock is None:
        logger.warning("Filelock not available. Concurrent writes may race.")

    with io_write_duration.labels(file=os.path.basename(target)).time():
        with lock:
            try:
                if should_migrate:
                    original = target
                    gz_path  = original + ".gz"
                    if os.path.exists(original):
                        tmp_gz = gz_path + ".tmp"
                        try:
                            with open(original, "rb") as fi, gzip.open(tmp_gz, "wb") as fo:
                                shutil.copyfileobj(fi, fo)
                            os.replace(tmp_gz, gz_path)
                            os.remove(original)
                            logger.info(f"Successfully migrated {original} to {gz_path}")
                        except Exception as e:
                            logger.error(f"Migration failed from {original} to {gz_path}: {e}", exc_info=True)
                        finally:
                            try:
                                if os.path.exists(tmp_gz):
                                    os.remove(tmp_gz)
                            except Exception:
                                pass
                    target = gz_path

                if target.endswith(".gz"):
                    def _write_gz(p: str, b: bytes) -> None:
                        try:
                            with gzip.open(p, "ab") as f:
                                f.write(b)
                                io_write_bytes.labels(file=os.path.basename(p)).inc(len(b))
                                try:
                                    f.flush()
                                    fileobj = getattr(f, "fileobj", None)
                                    if fileobj and hasattr(fileobj, "fileno"):
                                        os.fsync(fileobj.fileno())
                                except Exception:
                                    pass
                        except Exception as e:
                            logger.error(f"Failed to write to gzipped log {p}: {e}", exc_info=True)
                            raise
                    await asyncio.to_thread(_write_gz, target, line.encode("utf-8"))
                else:
                    if aiofiles is not None:
                        async with aiofiles.open(target, "a", encoding="utf-8") as f:
                            await f.write(line)
                            io_write_bytes.labels(file=os.path.basename(target)).inc(line_bytes)
                    else:
                        def _write_text(p: str, t: str) -> None:
                            with open(p, "a", encoding="utf-8") as f:
                                f.write(t)
                                io_write_bytes.labels(file=os.path.basename(p)).inc(len(t.encode('utf-8')))
                                f.flush()
                                os.fsync(f.fileno())
                        await asyncio.to_thread(_write_text, target, line)
            except Exception as e:
                logger.error("Failed to write feedback log %s: %s", target, e, exc_info=True)
                raise

    logger.info("Feedback logged → %s", target)
    if AUDIT_LOGGER_AVAILABLE:
        try:
            await audit_logger.log_event(
                event_type="feedback_log_write",
                details={"status": "success", "path": target},
                critical=False
            )
        except Exception:
            logger.debug("audit_logger.log_event failed", exc_info=True)


async def async_read_file(path: str) -> str:
    safe_path = validate_relative_path(path)
    with io_read_duration.labels(file=os.path.basename(safe_path)).time():
        try:
            if aiofiles is not None:
                async with aiofiles.open(safe_path, "r", encoding="utf-8") as f:
                    content = await f.read()
            else:
                with open(safe_path, "r", encoding="utf-8") as f:
                    content = f.read()
            await audit_event("file_read", {"path": safe_path, "size": len(content.encode("utf-8"))}, critical=False)
            return content
        except Exception as e:
            logger.error("Failed to read file %s: %s", safe_path, e, exc_info=True)
            await audit_event("file_read_failed", {"path": safe_path, "error": str(e)}, critical=True)
            raise

async def async_write_file(path: str, content: str) -> None:
    safe_path = validate_relative_path(path)
    with io_write_duration.labels(file=os.path.basename(safe_path)).time():
        try:
            if aiofiles is not None:
                async with aiofiles.open(safe_path, "w", encoding="utf-8") as f:
                    await f.write(content)
            else:
                with open(safe_path, "w", encoding="utf-8") as f:
                    f.write(content)
            io_write_bytes.labels(file=os.path.basename(safe_path)).inc(len(content.encode("utf-8")))
            await audit_event("file_write", {"path": safe_path, "size": len(content.encode("utf-8"))}, critical=False)
        except Exception as e:
            logger.error("Failed to write file %s: %s", safe_path, e, exc_info=True)
            await audit_event("file_write_failed", {"path": safe_path, "error": str(e)}, critical=True)
            raise

# -------------------------------------------
# read/summarize (cached)
# -------------------------------------------
async def summarize_feedback(feedback_log_path: str) -> Optional[Dict[str, Any]]:
    """
    Public API: summarize either plain or gz log; cache invalidates on mtime.
    """
    try:
        resolved = validate_and_resolve_path(feedback_log_path)
    except ValueError:
        return None
        
    path = _active_log_path(resolved)
    if not os.path.exists(path):
        if CONFIG.get("is_demo_mode", False):
            logger.info("Demo mode: Returning mock feedback summary")
            return {
                "avg_coverage": 80.0,
                "success_rate": 90.0,
                "total_runs": 10,
                "status_counts": {"PASS": 9, "FAIL": 1, "FLAKY": 0, "skipped": 0, "error": 0}
            }
        logger.error("Feedback log file does not exist: %s", path)
        await audit_event("feedback_summary_failed", {"path": path, "error": "File not found"}, critical=True)
        return None

    cache_token = (path, os.path.getmtime(path))
    return await asyncio.to_thread(_summarize_feedback_cached, feedback_log_path, cache_token)


@lru_cache(maxsize=128)
@tenacity.retry(
    stop=tenacity.stop_after_attempt(3),
    wait=tenacity.wait_exponential(multiplier=1, min=1, max=10),
    reraise=True
)
def _summarize_feedback_cached(feedback_log_path: str, cache_token: Any) -> Optional[Dict[str, Any]]:
    """
    Cached summary by file. Pass `cache_token` so the cache invalidates on updates.
    Accepts either plain or .gz path and auto-resolves the active file.
    """
    try:
        resolved = validate_and_resolve_path(feedback_log_path)
    except ValueError:
        return None

    path = _active_log_path(resolved)
    if not os.path.exists(path):
        return None

    if pd is not None:
        try:
            with io_read_duration.labels(file=os.path.basename(path)).time():
                compression = "gzip" if path.endswith(".gz") else "infer"
                
                tot = 0
                status_counts: DefaultDict[str, int] = defaultdict(int)
                cov_sum = 0.0
                cov_cnt = 0
                chunksize = int(os.getenv("FEEDBACK_PANDAS_CHUNKSIZE", "100000"))
                
                for chunk in pd.read_json(path, lines=True, compression=compression, chunksize=chunksize):
                    tot += len(chunk)
                    
                    sc = chunk.get("execution_status", pd.Series([], dtype="object")).fillna("skipped").value_counts()
                    for k, v in sc.items():
                        status_counts[str(k)] += v
                    
                    cov = chunk.get("final_scores")
                    if cov is not None:
                        c_series = cov.apply(lambda x: (x or {}).get("coverage") if isinstance(x, dict) else None)
                        c_series = c_series.dropna()
                        
                        if not c_series.empty:
                            cov_sum += c_series.sum()
                            cov_cnt += c_series.count()
                
                avg_coverage = (cov_sum / cov_cnt) if cov_cnt else 0.0
                success_rate = (status_counts.get("PASS", 0) / tot) * 100 if tot else 0.0
                
                for k in ("PASS", "FAIL", "FLAKY", "skipped", "error"):
                    status_counts.setdefault(k, 0)

                return {
                    "avg_coverage": avg_coverage,
                    "success_rate": success_rate,
                    "total_runs": tot,
                    "status_counts": status_counts
                }
        except Exception as e:
            logger.warning("Pandas read failed; falling back to manual parse: %s", e, exc_info=True)

    total_runs = 0
    status_counts = {"PASS": 0, "FAIL": 0, "FLAKY": 0, "skipped": 0, "error": 0}
    coverage_scores = []

    def _open(p):
        if p.endswith(".gz"):
            return gzip.open(p, "rt", encoding="utf-8", errors="replace")
        return open(p, "r", encoding="utf-8", errors="replace")

    with io_read_duration.labels(file=os.path.basename(path)).time():
        try:
            with _open(path) as f:
                for line in f:
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    total_runs += 1
                    status = str(data.get("execution_status") or "skipped")
                    status_counts[status] = status_counts.get(status, 0) + 1
                    fs = data.get("final_scores", {})
                    if isinstance(fs, dict) and "coverage" in fs:
                        try:
                            coverage_scores.append(float(fs["coverage"]))
                        except Exception:
                            pass
        except Exception as e:
            logger.error("Failed to summarize %s: %s", path, e, exc_info=True)
            return None

    avg_coverage = (sum(coverage_scores) / len(coverage_scores)) if coverage_scores else 0.0
    success_rate = (status_counts.get("PASS", 0) / total_runs) * 100 if total_runs else 0.0

    for k in ("PASS", "FAIL", "FLAKY", "skipped", "error"):
        status_counts.setdefault(k, 0)

    return {
        "avg_coverage": avg_coverage,
        "success_rate": success_rate,
        "total_runs": total_runs,
        "status_counts": status_counts
    }


# Test snippet for verification
async def test_io_utils():
    from test_generation.orchestrator.audit import audit_event
    try:
        # Test append_to_feedback_log
        test_data = {"execution_status": "PASS", "final_scores": {"coverage": 95.0}}
        test_file = "test_feedback.jsonl"
        
        # Cleanup any previous test file
        if os.path.exists(test_file):
            os.remove(test_file)
            
        await append_to_feedback_log(test_file, test_data)
        
        # Test summarize_feedback
        summary = await summarize_feedback(test_file)
        assert summary["total_runs"] == 1, "Feedback summary failed"
        assert summary["status_counts"]["PASS"] == 1, "Status count incorrect"
        assert summary["avg_coverage"] == 95.0, "Average coverage incorrect"
        
        # Test validate_relative_path
        safe_path = validate_relative_path("atco_artifacts/test.txt")
        assert "atco_artifacts" in safe_path, "Path validation failed"
        
        await audit_event("io_utils_test_success", {"test": "io_utils"}, critical=False)
    except Exception as e:
        await audit_event("io_utils_test_failed", {"error": str(e)}, critical=True)
        raise
    finally:
        # Final cleanup
        if os.path.exists("test_feedback.jsonl"):
            os.remove("test_feedback.jsonl")
        if os.path.exists("test_feedback.jsonl.lock"):
            os.remove("test_feedback.jsonl.lock")
        if os.path.exists("atco_artifacts/test.txt"):
            os.remove("atco_artifacts/test.txt")
        if os.path.exists("atco_artifacts"):
            shutil.rmtree("atco_artifacts")

# Completed for syntactic validity.