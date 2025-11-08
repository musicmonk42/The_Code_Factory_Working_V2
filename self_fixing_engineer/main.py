#!/usr/bin/env python3
"""
main.py - Enterprise-Grade Entrypoint for Self-Fixing Engineer (SFE)

Features:
- Modes: CLI / API (FastAPI+Uvicorn) / WEB (Streamlit)
- Robust startup validation, graceful shutdown with signals, and retry on transient failures
- Prometheus metrics (standalone server and optional in-API /__sfe/metrics)
- Health & readiness endpoints: /__sfe/healthz and /__sfe/readyz (API mode)
- Optional uvloop, OTEL-friendly spans, JSON logging with trace/span propagation fields
- Lazy imports per mode so missing deps in one mode don’t break others
- CORS support via API_CORS_ORIGINS env var
- Root path support (--root-path / API_ROOT_PATH)
- Sentry (optional) via SENTRY_DSN; resilient fallbacks for all optional deps

Usage:
  python main.py --mode cli
  python main.py --mode api --host 0.0.0.0 --port 8080 --root-path /sfe
  python main.py --mode web

Env:
  APP_ENV             : production|development (default: development)
  REDIS_URL           : redis://localhost:6379/0 (required in prod)
  AUDIT_LOG_PATH      : ./audit_trail.log (required)
  METRICS_PORT        : e.g., 9091 (exposes Prometheus metrics on separate port)
  EXPOSE_METRICS_IN_API : 1 to also expose /__sfe/metrics from API app
  USE_UVLOOP          : 1 to enable uvloop (non-Windows)
  API_ROOT_PATH       : root path for FastAPI behind a proxy (e.g., /sfe)
  API_CORS_ORIGINS    : comma-separated origins for CORS (optional)
  SENTRY_DSN          : if set, enables Sentry reporting
  SFE_API_WORKERS     : desired worker count. NOTE: programmatic uvicorn.Server ignores workers; use CLI for multi-workers.

Flags:
  --log-json          : output JSON logs instead of text
  --metrics-port N    : override METRICS_PORT env
  --reload            : dev hot-reload (API mode)
  --root-path PATH    : override API_ROOT_PATH

"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Callable, Optional

VERSION = "1.2.0"

# -------------------------
# Optional uvloop (Linux/macOS)
# -------------------------

def _maybe_enable_uvloop() -> None:
    if os.getenv("USE_UVLOOP", "0").strip().lower() not in {"1", "true", "yes", "on"}:
        return
    if sys.platform.startswith("win"):
        return
    try:
        import uvloop  # type: ignore
        uvloop.install()
    except Exception as e:
        print(f"[WARN] uvloop not enabled: {e}", file=sys.stderr)


# -------------------------
# Logging (text or JSON) with OTEL context fields
# -------------------------
class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "lvl": record.levelname,
            "name": record.name,
            "msg": record.getMessage(),
            "version": VERSION,
        }
        # OTEL trace/span ids if available
        try:
            from opentelemetry import trace  # type: ignore
            span = trace.get_current_span()
            if span is not None:
                ctx = span.get_span_context()
                if ctx and getattr(ctx, "trace_id", 0):
                    payload["trace_id"] = f"{ctx.trace_id:032x}"
                    payload["span_id"] = f"{ctx.span_id:016x}"
        except Exception:
            pass
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, ensure_ascii=False)


def _init_logging(log_json: bool) -> logging.Logger:
    logger = logging.getLogger("sfe.main")
    if logger.handlers:
        return logger

    handler = logging.StreamHandler(sys.stdout)
    if log_json:
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)

    # Attempt project logging enrichment (non-fatal)
    try:
        from config import setup_logging as config_setup_logging  # type: ignore
        config_setup_logging()
    except Exception:
        pass
    return logger


# Parse --log-json early so logging shape is consistent for arg errors
_pre = argparse.ArgumentParser(add_help=False)
_pre.add_argument("--log-json", action="store_true", default=False)
_pre_args, _ = _pre.parse_known_args()
logger = _init_logging(_pre_args.log_json)


# -------------------------
# Prometheus metrics (safe fallbacks)
# -------------------------
class _DummyMetric:
    def labels(self, *_, **__):
        return self
    def inc(self, *_, **__):
        return None
    def observe(self, *_, **__):
        return None


def _init_metrics():
    try:
        from prometheus_client import Counter, Histogram, start_http_server  # type: ignore
        STARTUP_ATTEMPTS = Counter("sfe_startup_attempts_total", "Total SFE startup attempts", ["mode"])
        STARTUP_LATENCY = Histogram("sfe_startup_latency_seconds", "SFE startup latency", ["mode"])
        STARTUP_FAILURES = Counter("sfe_startup_failures_total", "Total SFE startup failures", ["mode"])
        return STARTUP_ATTEMPTS, STARTUP_LATENCY, STARTUP_FAILURES, start_http_server
    except Exception as e:
        logger.warning("Prometheus client not available (%s). Metrics disabled.", e)
        return _DummyMetric(), _DummyMetric(), _DummyMetric(), (lambda _port: None)


STARTUP_ATTEMPTS, STARTUP_LATENCY, STARTUP_FAILURES, _start_http_server = _init_metrics()


# -------------------------
# OTEL tracer (optional)
# -------------------------
try:
    from opentelemetry import trace  # type: ignore
    _tracer = trace.get_tracer(__name__)
except Exception:
    class _NoOpTracer:
        def start_as_current_span(self, *_a, **_k):
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
    _tracer = _NoOpTracer()


# -------------------------
# Sentry (optional)
# -------------------------

def _init_sentry():
    dsn = os.getenv("SENTRY_DSN")
    if not dsn:
        return None
    try:
        import sentry_sdk  # type: ignore
        sentry_sdk.init(dsn=dsn, traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.0")))
        logger.info("Sentry initialized")
        return sentry_sdk
    except Exception as e:
        logger.warning("Sentry init failed: %s", e)
        return None


_sentry = _init_sentry()


# -------------------------
# Audit logger (optional)
# -------------------------

def _init_audit_logger():
    try:
        from audit_log import AuditLogger  # type: ignore
        return AuditLogger.from_environment()
    except Exception as e:
        logger.warning("AuditLogger unavailable (%s). Using noop audit logger.", e)
        class _NoopAudit:
            async def add_entry(self, **_k):
                return None
            async def close(self):
                return None
        return _NoopAudit()


audit_logger = _init_audit_logger()


# -------------------------
# Helpers
# -------------------------

def _windows_event_loop_policy_fix():
    if sys.platform.startswith("win"):
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined]
        except Exception:
            pass


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


async def _maybe_await(fn: Callable[..., Any], *a, **k):
    res = fn(*a, **k)
    if asyncio.iscoroutine(res):
        return await res
    return res


async def _quick_redis_check(redis_url: str, timeout_s: float = 0.3) -> bool:
    if not redis_url:
        return True
    try:
        import redis.asyncio as redis  # type: ignore
        client = redis.from_url(redis_url, socket_timeout=timeout_s, socket_connect_timeout=timeout_s, decode_responses=True)
        try:
            await asyncio.wait_for(client.ping(), timeout=timeout_s)
            await client.close()
            return True
        except Exception:
            await client.close()
            return False
    except Exception:
        # Redis lib not installed or other issue: don't block readiness on optional deps
        return True


# -------------------------
# Startup validation
# -------------------------

async def startup_validation():
    logger.info("SFE validating startup (version=%s, git=%s)", VERSION, os.getenv("GIT_COMMIT", "n/a"))
    with _tracer.start_as_current_span("startup_validation"):
        try:
            from config import GlobalConfigManager  # type: ignore
            cfg = GlobalConfigManager.get_config()

            # In production, enforce presence of critical fields
            env = os.getenv("APP_ENV", "development").lower()
            required = ["REDIS_URL", "AUDIT_LOG_PATH"] if env == "production" else ["AUDIT_LOG_PATH"]
            missing = [f for f in required if not getattr(cfg, f, None)]
            if missing:
                raise ValueError(f"Missing required config fields: {', '.join(missing)}")

            await audit_logger.add_entry(
                event_category="system",
                event_type="startup",
                details={"message": "SFE platform started", "env": env, "version": VERSION},
                agent_id="sfe_main",
            )
            logger.info("Startup validation OK (env=%s)", env)
        except Exception as e:
            logger.error("Startup validation failed: %s", e, exc_info=True)
            STARTUP_FAILURES.labels(mode="startup").inc()
            try:
                _tracer.start_as_current_span("startup_validation_failed").record_exception(e)  # fire-and-forget
            except Exception:
                pass
            try:
                await audit_logger.add_entry(
                    event_category="system",
                    event_type="startup_failed",
                    details={"error": str(e)},
                    agent_id="sfe_main",
                )
            finally:
                raise


# -------------------------
# Metrics server
# -------------------------

def start_metrics_server(metrics_port: Optional[int] = None):
    port = metrics_port if metrics_port is not None else os.getenv("METRICS_PORT")
    if not port:
        return
    try:
        port = int(port)  # type: ignore[assignment]
        _start_http_server(port)
        logger.info("Prometheus metrics server started on port %s", port)
    except Exception as e:
        logger.error("Failed to start metrics server: %s", e, exc_info=True)


# -------------------------
# Retry decorator (Tenacity)
# -------------------------
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type


def _retry_decorator():
    def _on_retry(retry_state):
        try:
            exc = retry_state.outcome.exception()  # type: ignore[attr-defined]
        except Exception:
            exc = None
        logger.warning("Startup attempt %s failed: %s", getattr(retry_state, "attempt_number", "?"), exc)
    return retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        before_sleep=_on_retry,
        reraise=True,
    )


# -------------------------
# Mode runners
# -------------------------
@_retry_decorator()
async def run_cli():
    STARTUP_ATTEMPTS.labels(mode="cli").inc()
    start = datetime.now()
    with _tracer.start_as_current_span("run_cli"):
        try:
            from cli import main_cli_loop  # type: ignore
        except Exception as e:
            logger.error("Failed to import CLI: %s", e, exc_info=True)
            raise
        await main_cli_loop()
    STARTUP_LATENCY.labels(mode="cli").observe((datetime.now() - start).total_seconds())


@_retry_decorator()
async def run_api(host: str = "0.0.0.0", port: int = 8000, reload: bool = False, root_path: str = ""):
    STARTUP_ATTEMPTS.labels(mode="api").inc()
    try:
        import uvicorn  # type: ignore
        from fastapi import FastAPI, APIRouter  # type: ignore
        from api import create_app as create_fastapi_app  # type: ignore
    except Exception as e:
        logger.error("API deps unavailable: %s", e, exc_info=True)
        raise

    app: FastAPI = create_fastapi_app()

    # Root path
    app.root_path = root_path or os.getenv("API_ROOT_PATH", "")

    # CORS (optional)
    cors = os.getenv("API_CORS_ORIGINS")
    if cors:
        try:
            from fastapi.middleware.cors import CORSMiddleware  # type: ignore
            origins = [o.strip() for o in cors.split(",") if o.strip()]
            app.add_middleware(
                CORSMiddleware,
                allow_origins=origins,
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"],
            )
            logger.info("CORS enabled for origins: %s", origins)
        except Exception as e:
            logger.warning("Failed to enable CORS: %s", e)

    # Health / readiness / metrics endpoints
    router = APIRouter()

    @router.get("/__sfe/healthz")
    async def _healthz():
        return {
            "status": "ok",
            "mode": "api",
            "version": VERSION,
            "time": datetime.utcnow().isoformat() + "Z",
            "root_path": getattr(app, "root_path", ""),
        }

    @router.get("/__sfe/readyz")
    async def _readyz():
        # Basic readiness includes optional Redis ping if REDIS_URL present
        redis_ok = True
        try:
            from config import GlobalConfigManager  # type: ignore
            cfg = GlobalConfigManager.get_config()
            redis_ok = await _quick_redis_check(getattr(cfg, "REDIS_URL", ""))
        except Exception:
            pass
        return {
            "status": "ok" if redis_ok else "degraded",
            "checks": {"redis": bool(redis_ok)},
            "version": VERSION,
        }

    # Optional in-app metrics exposure
    if _env_bool("EXPOSE_METRICS_IN_API", False):
        try:
            from prometheus_client import generate_latest, CONTENT_TYPE_LATEST  # type: ignore
            from fastapi.responses import Response  # type: ignore

            @router.get("/__sfe/metrics")
            async def _metrics():
                data = generate_latest()
                return Response(content=data, media_type=CONTENT_TYPE_LATEST)
            logger.info("Mounted /__sfe/metrics in API app")
        except Exception as e:
            logger.warning("Failed to mount /__sfe/metrics: %s", e)

    app.include_router(router)

    # Lifespan for metrics
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        start = datetime.now()
        logger.info("Starting SFE FastAPI application")
        yield
        logger.info("Shutting down SFE FastAPI application")
        STARTUP_LATENCY.labels(mode="api").observe((datetime.now() - start).total_seconds())

    try:
        app.router.lifespan_context = lifespan  # type: ignore[attr-defined]
    except Exception:
        pass

    # Workers note
    workers = int(os.getenv("SFE_API_WORKERS", "1"))
    if workers > 1:
        logger.warning(
            "SFE_API_WORKERS=%s set. Programmatic uvicorn.Server ignores workers. Use 'uvicorn api:app --workers %s' for true multi-process.",
            workers, workers,
        )

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        reload=reload,
        lifespan="on",
        log_level="info",
    )
    server = uvicorn.Server(config)

    start = datetime.now()
    await server.serve()
    STARTUP_LATENCY.labels(mode="api").observe((datetime.now() - start).total_seconds())


@_retry_decorator()
async def run_web():
    STARTUP_ATTEMPTS.labels(mode="web").inc()
    try:
        from web_app import run as run_streamlit_app  # type: ignore
    except Exception as e:
        logger.error("Web app module unavailable: %s", e, exc_info=True)
        raise

    start = datetime.now()
    if hasattr(asyncio, "to_thread"):
        await asyncio.to_thread(run_streamlit_app)
    else:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, run_streamlit_app)
    STARTUP_LATENCY.labels(mode="web").observe((datetime.now() - start).total_seconds())


# -------------------------
# Signal handling
# -------------------------

def _install_signal_handlers(cancel: asyncio.Event):
    loop = asyncio.get_running_loop()

    def _signal_handler(sig_name: str):
        logger.info("Received %s; requesting graceful shutdown...", sig_name)
        cancel.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler, sig.name)  # type: ignore[arg-type]
        except NotImplementedError:
            signal.signal(sig, lambda *_: _signal_handler(sig.name))  # type: ignore[arg-type]


# -------------------------
# Main
# -------------------------

async def main():
    _maybe_enable_uvloop()
    _windows_event_loop_policy_fix()

    parser = argparse.ArgumentParser(description="Self-Fixing Engineer (SFE) Entrypoint")
    parser.add_argument("--mode", choices=["cli", "api", "web"], default="cli")
    parser.add_argument("--host", default=os.getenv("SFE_API_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("SFE_API_PORT", "8000")))
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--root-path", default=os.getenv("API_ROOT_PATH", ""))
    parser.add_argument("--log-json", action="store_true", default=_pre_args.log_json)
    parser.add_argument("--metrics-port", type=int, default=None, help="Override METRICS_PORT env")
    args = parser.parse_args()

    if args.log_json != _pre_args.log_json:
        logging.getLogger().handlers.clear()
        _ = _init_logging(args.log_json)

    start_metrics_server(args.metrics_port)

    cancel_event = asyncio.Event()
    _install_signal_handlers(cancel_event)

    try:
        await startup_validation()

        if args.mode == "cli":
            await run_cli()
        elif args.mode == "api":
            await run_api(host=args.host, port=args.port, reload=args.reload, root_path=args.root_path)
        elif args.mode == "web":
            await run_web()

        # API/WEB typically block; CLI may complete and return. If we get here without a
        # cancel signal and not in CLI mode, wait for a signal to terminate.
        if not cancel_event.is_set() and args.mode != "cli":
            await cancel_event.wait()

    except Exception as e:
        logger.critical("SFE platform failed: %s", e, exc_info=True)
        STARTUP_FAILURES.labels(mode=args.mode).inc()
        try:
            await audit_logger.add_entry(
                event_category="system",
                event_type="critical_failure",
                details={"error": str(e), "mode": args.mode},
                agent_id="sfe_main",
            )
        finally:
            sys.exit(1)
    finally:
        try:
            await audit_logger.close()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
