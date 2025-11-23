# api.py
from __future__ import annotations
import os
import sys
import json
import time
import asyncio
import logging
from typing import Dict, Any, Optional, Literal, TypedDict, Callable
from datetime import datetime
from flask import Flask, request, jsonify, g, current_app
from pathlib import Path
import uuid
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.exceptions import TooManyRequests, BadRequest
from asyncio import TimeoutError as AsyncTimeoutError
from functools import wraps

#
# Guard against optional dependencies at import time with specific logging
#
CORS = Limiter = JWTManager = PrometheusMetrics = get_swaggerui_blueprint = None
def jwt_required(*a, **k):
    return (lambda f: f)
def create_access_token(*a, **k):
    return "jwt-disabled"
def get_remote_address(*a, **k):
    return "unknown"

try:
    from flask_cors import CORS

    logging.info("Flask extension 'flask_cors' is available.")
except ImportError:
    logging.warning("Flask extension 'flask_cors' not installed. CORS disabled.")
    pass

LIMITER_AVAILABLE = False
try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address

    LIMITER_AVAILABLE = True
    logging.info("Flask extension 'flask_limiter' is available.")
except ImportError:
    logging.warning(
        "Flask extension 'flask_limiter' not installed. Rate limiting disabled."
    )
    pass

JWT_AVAILABLE = False
try:
    from flask_jwt_extended import JWTManager, jwt_required, create_access_token

    JWT_AVAILABLE = True
    logging.info("Flask extension 'flask_jwt_extended' is available.")
except ImportError:
    logging.warning(
        "Flask extension 'flask_jwt_extended' not installed. JWT authentication disabled."
    )
    pass

try:
    from prometheus_flask_exporter import PrometheusMetrics

    logging.info("Flask extension 'prometheus_flask_exporter' is available.")
except ImportError:
    logging.warning(
        "Flask extension 'prometheus_flask_exporter' not installed. Prometheus metrics disabled."
    )
    pass

try:
    from flask_swagger_ui import get_swaggerui_blueprint

    logging.info("Flask extension 'flask_swagger_ui' is available.")
except ImportError:
    logging.warning(
        "Flask extension 'flask_swagger_ui' not installed. Swagger UI disabled."
    )
    pass

try:
    import gunicorn.app.wsgiapp

    _GUNICORN_OK = True
except ImportError:
    _GUNICORN_OK = False

from pydantic import BaseModel, ValidationError, Field

# Using a single, consistent source for runtime imports
from test_generation.gen_agent.runtime import (
    FLASK_AVAILABLE,
    AUDIT_LOGGER_AVAILABLE,
    audit_logger,
    init_llm as runtime_init_llm,
    validate_session_inputs,
)
from test_generation.gen_agent.graph import build_graph, invoke_graph

logger = logging.getLogger(__name__)


class GenerateTestsRequest(BaseModel):
    model_config = {"extra": "forbid"}
    spec: str = Field(
        min_length=1, max_length=int(os.getenv("SPEC_MAX_CHARS", "20000"))
    )
    session: Optional[str] = None
    language: Literal["Python", "JavaScript", "TypeScript", "Java", "Rust"] = "Python"
    framework: Literal["pytest", "jest", "junit", "cargo", "unittest", "go test"] = (
        "pytest"
    )
    spec_format: Literal["gherkin", "openapi", "user_story"] = "gherkin"


TestAgentState = TypedDict(
    "TestAgentState",
    {
        "spec": str,
        "spec_format": str,
        "language": str,
        "framework": str,
        "plan": Dict,
        "test_code": str,
        "review": Dict,
        "execution_results": Dict,
        "security_report": str,
        "performance_script": str,
        "repair_attempts": int,
        "artifacts": Dict[str, str],
        "code_under_test": str,
    },
    total=False,
)


def with_jwt_required(func: Callable) -> Callable:
    if JWT_AVAILABLE:
        return jwt_required()(func)
    else:

        @wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        return wrapper


async def _run_async(coro, timeout=None):
    try:
        if timeout:
            return await asyncio.wait_for(coro, timeout)
        return await coro
    except asyncio.TimeoutError:
        raise
    except Exception as e:
        logger.error("Async execution failed: %s", str(e))
        raise


async def _generate_tests_logic(data: GenerateTestsRequest) -> Dict[str, Any]:
    initial_state: TestAgentState = {
        "spec": data.spec,
        "spec_format": data.spec_format,
        "language": data.language,
        "framework": data.framework,
        "repair_attempts": 0,
    }
    try:
        graph = current_app.config["_GRAPH"]
        config = {
            "configurable": {"thread_id": f"api-thread-{datetime.now().timestamp()}"}
        }
        final_state = await invoke_graph(graph, initial_state, config=config)
        return final_state
    except Exception as e:
        logger.error(f"Graph invocation failed: {e}", exc_info=True)
        raise e


def create_app(config: Dict[str, Any]) -> Flask:
    if not Flask:
        raise RuntimeError("Flask package is not installed.")

    app = Flask(__name__)
    app.config.from_mapping(config)

    if os.getenv("BEHIND_PROXY", "false").lower() == "true":
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    app.config["MAX_CONTENT_LENGTH"] = int(
        os.getenv("MAX_CONTENT_LENGTH", 2 * 1024 * 1024)
    )
    app.config.setdefault("JSONIFY_PRETTYPRINT_REGULAR", False)

    # --- LLM + graph initialization (resilient) ---
    try:
        llm = runtime_init_llm()
    except Exception as e:
        # exact message the test asserts against; single positional arg, no kwargs
        logging.getLogger(__name__).error(f"LLM init failed at startup: {e}")
        llm = None
    try:
        graph = build_graph(llm) if llm is not None else None
    except Exception as e:
        # optional: useful for diagnostics; not asserted by tests
        logging.getLogger(__name__).error(f"Graph build failed at startup: {e}")
        graph = None

    app.config["_LLM"] = llm
    app.config["_GRAPH"] = graph

    # --- Swagger spec + docs ---
    swagger_path = app.config.get("swagger_path") or str(
        Path(__file__).with_name("swagger.json")
    )

    def _load_swagger() -> Dict[str, Any]:
        if os.path.exists(swagger_path):
            with open(swagger_path, "r", encoding="utf-8") as f:
                return json.load(f)
        # Minimal default spec if none exists
        return {
            "openapi": "3.0.0",
            "info": {"title": "Test Generation API", "version": "1.0.0"},
            "paths": {
                "/health": {
                    "get": {
                        "summary": "Health",
                        "responses": {"200": {"description": "OK"}},
                    }
                },
                "/status": {
                    "get": {
                        "summary": "Status",
                        "responses": {"200": {"description": "OK"}},
                    }
                },
                "/generate-tests": {
                    "post": {
                        "summary": "Generate tests",
                        "responses": {"200": {"description": "OK"}},
                    }
                },
            },
        }

    @app.route("/swagger.json", methods=["GET"])
    def swagger_json():
        return jsonify(_load_swagger())

    SWAGGER_URL = "/api/docs"
    API_URL = "/swagger.json"
    if get_swaggerui_blueprint:
        swaggerui_blueprint = get_swaggerui_blueprint(
            SWAGGER_URL, API_URL, config={"app_name": "Test Generation Agent API"}
        )
        app.register_blueprint(swaggerui_blueprint, url_prefix=SWAGGER_URL)
    else:
        logger.warning("flask_swagger_ui not installed; API docs disabled.")

    if CORS:
        origins_env = os.getenv("CORS_ORIGINS")
        if origins_env:
            origins = [o.strip() for o in origins_env.split(",") if o.strip()]
            supports_credentials = (
                os.getenv("CORS_SUPPORTS_CREDENTIALS", "false").lower() == "true"
            )
            CORS(
                app,
                resources={
                    r"/*": {
                        "origins": origins,
                        "supports_credentials": supports_credentials,
                    }
                },
            )
        else:
            logger.info("CORS not configured; defaulting to same-origin.")

    limiter = None
    if Limiter and LIMITER_AVAILABLE:
        storage_uri = os.getenv("RATELIMIT_STORAGE_URI", "memory://")
        limiter = Limiter(
            key_func=get_remote_address,
            app=app,
            storage_uri=storage_uri,
            default_limits=["100 per minute"],
        )
        if os.getenv("ENV", "dev") == "prod" and storage_uri.startswith("memory://"):
            logger.warning(
                "Rate limit storage is in-memory in production; use a shared backend (e.g., redis://)."
            )

    if JWTManager and JWT_AVAILABLE:
        JWTManager(app)
    else:
        logger.warning("JWT not enabled; /generate-tests is NOT authenticated.")

    # FIX: Add a check to prevent unauthenticated access to endpoints in production.
    if os.getenv("ENV", "dev") == "prod" and not JWT_AVAILABLE:
        raise RuntimeError(
            "JWT authentication is required in production environment but flask_jwt_extended is not available."
        )

    # Removed the PrometheusMetrics conditional wiring here to avoid conflicts.
    # The /metrics endpoint is now handled by the separate route function below.
    if PrometheusMetrics and os.getenv("ENABLE_METRICS", "false").lower() == "true":
        PrometheusMetrics(app, group_by="endpoint", excluded_paths=["/health"])
    elif PrometheusMetrics:
        logger.info("/metrics endpoint is disabled; set ENABLE_METRICS=true to enable.")

    @app.after_request
    def add_security_headers(response):
        if os.getenv("ENV", "dev") == "prod":
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self' data:;"
            )
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return response

    @app.before_request
    def before_request():
        g.request_start_time = time.time()
        g.request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        client_ip = request.remote_addr
        logger.info(
            f"rid={g.request_id} {request.method} {request.path} from {client_ip}"
        )

    @app.after_request
    def after_request_log(response):
        elapsed_time = time.time() - g.request_start_time
        response.headers["X-Request-ID"] = g.get("request_id", "-")
        logger.info(
            f"rid={g.request_id} {request.path} {response.status_code} in {elapsed_time:.4f}s"
        )
        return response

    @app.errorhandler(429)
    def handle_ratelimit(e: TooManyRequests):
        resp = jsonify({"error": "Too many requests"})
        retry_after = getattr(e, "retry_after", None)
        if retry_after:
            resp.headers["Retry-After"] = str(retry_after)
        return resp, 429

    @app.errorhandler(BadRequest)
    def handle_bad_request(e: BadRequest):
        if "Failed to decode JSON object" in str(e):
            return (
                jsonify({"error": "Bad Request", "message": "Invalid JSON body"}),
                400,
            )
        return jsonify({"error": "Bad Request", "message": str(e)}), 400

    @app.errorhandler(404)
    def handle_not_found(e):
        return jsonify({"error": "Resource not found"}), 404

    @app.errorhandler(ValidationError)
    def handle_pydantic_validation(e):
        logger.error(f"Pydantic validation error: {e.errors()}")
        return jsonify({"error": "Invalid input", "details": e.errors()}), 400

    @app.errorhandler(ValueError)
    def handle_value_error(e):
        return jsonify({"error": "Invalid input", "message": str(e)}), 400

    @app.errorhandler(Exception)
    def handle_generic_exception(e):
        logger.error(f"An unexpected API error occurred: {e}", exc_info=True)
        return jsonify({"error": "An internal server error occurred"}), 500

    def _health_check() -> Dict[str, Any]:
        ok_llm = bool(current_app.config.get("_LLM"))
        if os.getenv("DEEP_HEALTHCHECK", "false").lower() == "true":
            try:
                llm = current_app.config["_LLM"]
                res = getattr(llm, "invoke", None)
                if callable(res):
                    out = res("ping")
                    if hasattr(out, "__await__"):
                        _run_async(out, timeout=2.0)
                else:
                    ok_llm = False
            except Exception:
                ok_llm = False
        return {
            "status": "ok" if ok_llm else "degraded",
            "llm_available": ok_llm,
            "audit_log_available": bool(AUDIT_LOGGER_AVAILABLE),
        }

    @app.route("/health", methods=["GET"])
    def health_check_endpoint():
        status = _health_check()
        return jsonify(status), 200 if status["status"] == "ok" else 503

    @app.route("/metrics", methods=["GET"])
    def metrics_endpoint():
        from test_generation.orchestrator.metrics import METRICS_AVAILABLE

        if METRICS_AVAILABLE:
            from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

            return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}
        return jsonify({"error": "Metrics not enabled"}), 404

    @app.route("/status", methods=["GET"])
    def status_endpoint():
        status = _health_check()
        status["version"] = os.getenv("APP_VERSION", "0.0.0")
        app_start_time = app.config.get("APP_START_TIME", time.time())
        status["uptime"] = time.time() - app_start_time
        return jsonify(status), 200

    @app.teardown_appcontext
    def cleanup_appcontext(exception=None):
        logger.info("Cleaning up app context")
        if AUDIT_LOGGER_AVAILABLE:
            try:
                asyncio.run(
                    audit_logger.log_event(
                        event_type="app_shutdown",
                        details={"reason": str(exception) if exception else "normal"},
                        critical=bool(exception),
                    )
                )
            except Exception as e:
                logger.warning("Failed to log shutdown event: %s", e)

    @app.route("/generate-tests", methods=["POST"])
    @with_jwt_required
    @limiter.limit("100 per minute") if LIMITER_AVAILABLE and limiter else (lambda f: f)
    def generate_tests_endpoint():
        try:
            json_data = request.get_json(force=True)
            if json_data is None:
                return (
                    jsonify({"error": "Bad Request", "message": "Invalid JSON body"}),
                    400,
                )

            if current_app.config["_GRAPH"] is None:
                return (
                    jsonify(
                        {
                            "error": "Service unavailable",
                            "message": "LLM agent failed to initialize at startup.",
                        }
                    ),
                    503,
                )

            data = GenerateTestsRequest(**json_data)
            session_name = (
                data.session or f"api-session-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            )
            validate_session_inputs(session_name, data.language, data.framework)
        except BadRequest:
            return (
                jsonify({"error": "Bad Request", "message": "Invalid JSON body"}),
                400,
            )
        except (ValidationError, ValueError) as e:
            if isinstance(e, ValidationError):
                return (
                    jsonify(
                        {
                            "error": "Bad Request",
                            "message": "Validation failed",
                            "details": e.errors(),
                        }
                    ),
                    400,
                )
            else:
                return jsonify({"error": "Bad Request", "message": str(e)}), 400
        except Exception as e:
            logger.error(f"Request validation failed: {e}", exc_info=True)
            return jsonify({"error": "Bad Request", "message": str(e)}), 400

        try:
            final_state = asyncio.run(_generate_tests_logic(data))
        except (AsyncTimeoutError, TimeoutError):
            return jsonify({"error": "Request timed out"}), 504
        except Exception as e:
            logger.error(f"Async execution failed: {e}", exc_info=True)
            return jsonify({"error": "An internal server error occurred"}), 500

        return jsonify(final_state)

    @app.route("/api/docs", methods=["GET"])
    def docs():
        html = """<!doctype html>
<html>
  <head>
    <meta charset="utf-8"/>
    <title>API Docs</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <script src="https://cdn.jsdelivr.net/npm/redoc@next/bundles/redoc.standalone.js"></script>
    <style>html,body,#redoc{height:100%;margin:0;padding:0}</style>
  </head>
  <body>
    <redoc spec-url="/swagger.json"></redoc>
  </body>
</html>"""
        return html, 200, {"Content-Type": "text/html; charset=utf-8"}

    # --- Optional: auto-register lightweight mock endpoints from swagger.json ---
    def _register_swagger_mocks():
        spec = _load_swagger()
        for path, methods in (spec.get("paths") or {}).items():
            for m, op in (methods or {}).items():
                if not isinstance(op, dict) or not op.get("x-mock"):
                    continue
                http_method = m.upper()
                endpoint_name = f"{http_method}_{path}".replace("/", "_")
                if endpoint_name in app.view_functions:
                    continue

                def _mk_handler(p=path, method=http_method, opdef=op):
                    def _handler():
                        return jsonify(
                            {
                                "ok": True,
                                "path": p,
                                "method": method,
                                "operationId": opdef.get("operationId"),
                                "params": request.args,
                                "body": request.get_json(silent=True),
                            }
                        )

                    return _handler

                try:
                    app.add_url_rule(
                        path,
                        endpoint=endpoint_name,
                        view_func=_mk_handler(),
                        methods=[http_method],
                    )
                    logger.info(
                        "Registered mock endpoint from swagger: %s %s",
                        http_method,
                        path,
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to register mock endpoint %s %s: %s",
                        http_method,
                        path,
                        e,
                    )

    _register_swagger_mocks()

    app.config["APP_START_TIME"] = time.time()
    return app


def serve_api(host: str, port: int) -> int:
    if not Flask or not FLASK_AVAILABLE:
        logging.critical(
            "CRITICAL: Cannot serve API: 'flask' package is not installed. Aborting."
        )
        if AUDIT_LOGGER_AVAILABLE:
            asyncio.run(
                audit_logger.log_event(
                    event_type="test_agent_action",
                    details={
                        "action": "serve_api",
                        "result": "failure",
                        "reason": "flask not installed",
                    },
                    critical=True,
                )
            )
        return 1

    config = {
        "SECRET_KEY": os.getenv(
            "FLASK_SECRET_KEY", "a-strong-default-secret-key-CHANGE-ME"
        ),
        "JWT_SECRET_KEY": os.getenv(
            "JWT_SECRET_KEY", "another-strong-secret-CHANGE-ME"
        ),
    }

    if os.getenv("ENV", "dev") == "prod":
        if config["SECRET_KEY"].endswith("CHANGE-ME"):
            raise RuntimeError("SECRET_KEY must be set in production")
        if config["JWT_SECRET_KEY"].endswith("CHANGE-ME"):
            raise RuntimeError("JWT_SECRET_KEY must be set in production")
        # FIX: Raise an error if JWT is required in production but not available.
        if not JWT_AVAILABLE:
            raise RuntimeError(
                "JWT authentication is required in production environment but flask_jwt_extended is not available."
            )

    app = create_app(config)
    globals()["app"] = app

    if os.getenv("ENV", "dev") == "prod" and _GUNICORN_OK:
        logging.info("Running in production mode with Gunicorn.")
        from gevent import monkey as _monkey

        _monkey.patch_all()
        sys.argv = [
            "gunicorn",
            "-w",
            "4",
            "-k",
            "gevent",
            f"--bind={host}:{port}",
            f"{__name__}:app",
        ]
        gunicorn.app.wsgiapp.run()
    else:
        logging.info("Running in development mode with Flask's built-in server.")
        debug = os.getenv("DEBUG", "false").lower() == "true"
        app.run(host=host, port=port, debug=debug, use_reloader=False)

    return 0
