# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# self_healing_import_fixer/analyzer/core_report.py

import datetime
import io
import json
import logging
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import boto3
from botocore.exceptions import ClientError

# --- Global Production Mode Flag (from analyzer.py) ---
PRODUCTION_MODE = os.getenv("PRODUCTION_MODE", "false").lower() == "true"

logger = logging.getLogger(__name__)


# --- Custom Exception for critical errors (from analyzer.py) ---
class AnalyzerCriticalError(RuntimeError):
    """
    Custom exception for critical errors that should halt execution and alert ops.
    """

    def __init__(self, message: str, alert_level: str = "CRITICAL"):
        super().__init__(f"[CRITICAL][REPORT] {message}")
        try:
            from .core_utils import alert_operator

            alert_operator(message, alert_level)
        except Exception:
            pass


# --- Centralized Utilities (replacing placeholders) ---
try:
    from .core_secrets import SECRETS_MANAGER
    from .core_utils import alert_operator
except ImportError as e:
    logger.critical(
        f"CRITICAL: Missing core dependency for core_report: {e}. Aborting startup."
    )
    # Cannot use alert_operator here since it failed to import!
    raise RuntimeError("[CRITICAL][REPORT] Missing core dependency") from e


def scrub_secrets(data: Any) -> Any:
    if isinstance(data, str):
        return data.replace("secret_token_abc", "[SCRUBBED]")
    elif isinstance(data, list):
        return [scrub_secrets(item) for item in data]
    elif isinstance(data, dict):
        return {key: scrub_secrets(value) for key, value in data.items()}
    else:
        return data


def _atomic_write_bytes(path: str, data: bytes) -> None:
    """Writes data to a file atomically, ensuring data integrity."""
    d = os.path.dirname(path) or "."
    # Use a temporary file in the same directory to prevent cross-device move issues
    with tempfile.NamedTemporaryFile(dir=d, delete=False, mode="wb") as tf:
        tf.write(data)
        tf.flush()
        os.fsync(tf.fileno())
        tmp = tf.name
    os.replace(tmp, path)


def _atomic_write_text(path: str, data: str) -> None:
    """Writes text data to a file atomically."""
    _atomic_write_bytes(path, data.encode("utf-8"))


# --- Flask Imports and App Initialization (at module scope) ---
try:
    from flask import (
        Flask,
        current_app,
        jsonify,
        render_template_string,
        request,
        send_file,
    )
    from flask_jwt_extended import (
        JWTManager,
        create_access_token,
        get_jwt,
        jwt_required,
    )

    FLASK_AVAILABLE = True
    app = Flask(__name__)
except ImportError:
    FLASK_AVAILABLE = False
    app = None
    JWTManager = None

    def jwt_required(f):
        return f

    def get_jwt():
        return {}

    def create_access_token(*args, **kwargs):
        return "dummy_token"


if FLASK_AVAILABLE:
    jwt_secret = SECRETS_MANAGER.get_secret("JWT_SECRET_KEY")
    if PRODUCTION_MODE and not jwt_secret:
        raise RuntimeError("[CRITICAL][REPORT] Missing JWT_SECRET_KEY in production")
    app.config["JWT_SECRET_KEY"] = jwt_secret
    app.config.setdefault("REPORTS_DIR", os.path.abspath("reports"))
    jwt = JWTManager(app)
    app.config.update(
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )


def get_reports_dir():
    if FLASK_AVAILABLE:
        try:
            from flask import current_app

            return current_app.config["REPORTS_DIR"]
        except Exception:
            pass
    return os.path.abspath("reports")


# --- KMS Utilities (at module scope) ---
_kms_client = None


def get_kms_client():
    global _kms_client
    if _kms_client is None:
        _kms_client = boto3.client(
            "kms", region_name=os.getenv("AWS_REGION", "us-east-1")
        )
    return _kms_client


REPORT_KMS_KEY_ALIAS = os.getenv("REPORT_KMS_KEY_ALIAS", "alias/analyzer-report-key")


def encrypt_report_content(content: bytes) -> bytes:
    if not PRODUCTION_MODE:
        return content
    try:
        kms = get_kms_client()
        try:
            kms.describe_key(KeyId=REPORT_KMS_KEY_ALIAS)
        except ClientError as e:
            raise AnalyzerCriticalError(
                f"KMS key {REPORT_KMS_KEY_ALIAS} not found: {e}"
            )

        response = kms.encrypt(KeyId=REPORT_KMS_KEY_ALIAS, Plaintext=content)
        return response["CiphertextBlob"]
    except ClientError as e:
        logger.error(f"KMS encryption failed: {e}", exc_info=True)
        alert_operator(
            f"CRITICAL: Report encryption failed: {e}. Report not encrypted.",
            level="CRITICAL",
        )
        raise RuntimeError(f"KMS encryption failed: {e}")
    except AnalyzerCriticalError as e:
        logger.error(str(e))
        alert_operator(str(e), level="CRITICAL")
        raise RuntimeError(f"Report encryption failed due to KMS key issue: {e}")


def decrypt_report_content(encrypted_content: bytes) -> bytes:
    if not PRODUCTION_MODE:
        return encrypted_content
    try:
        kms = get_kms_client()
        response = kms.decrypt(CiphertextBlob=encrypted_content)
        return response["Plaintext"]
    except ClientError as e:
        logger.error(
            f"KMS decryption failed: {e}. Cannot display report.", exc_info=True
        )
        alert_operator(
            f"CRITICAL: Report decryption failed: {e}. Report inaccessible.",
            level="CRITICAL",
        )
        return b"Error: Could not decrypt report content."


class ReportGenerator:
    """
    Generates various types of reports (text, HTML, Markdown, PDF, JSON) from analysis results.
    """

    def __init__(
        self,
        output_dir: str = "reports",
        approved_report_dirs: Optional[List[str]] = None,
    ):
        self.output_dir = os.path.abspath(output_dir)
        self.approved_report_dirs = [
            os.path.abspath(d) for d in (approved_report_dirs or [])
        ]
        self.production_mode = os.getenv("PRODUCTION_MODE", "false").lower() == "true"

        # Check for directory approval before creating/checking writability
        if self.production_mode and not self._is_approved_dir(self.output_dir):
            raise AnalyzerCriticalError(
                "[CRITICAL][REPORT] Output directory is not within approved paths."
            )

        if not os.path.exists(self.output_dir):
            try:
                os.makedirs(self.output_dir, exist_ok=True)
            except Exception:
                raise AnalyzerCriticalError(
                    "[CRITICAL][REPORT] Output directory is not writable or accessible."
                )

        if not os.access(self.output_dir, os.W_OK):
            raise AnalyzerCriticalError(
                "[CRITICAL][REPORT] Output directory is not writable or accessible."
            )

        try:
            test_file = os.path.join(self.output_dir, f".test_write_{uuid.uuid4().hex}")
            _atomic_write_text(test_file, "test")
            os.remove(test_file)
            logger.info(
                f"ReportGenerator initialized. Reports will be saved to: {self.output_dir}"
            )
        except Exception as e:
            raise AnalyzerCriticalError(
                f"Report output directory '{self.output_dir}' is not writable or accessible: {e}."
            )

    def _is_approved_dir(self, directory: str) -> bool:
        """Checks if a directory is within the list of approved paths."""
        abs_dir = os.path.abspath(directory)
        return any(
            abs_dir.startswith(approved) for approved in self.approved_report_dirs
        )

    def _format_text_report(self, results):
        lines = []
        for section, data in results.items():
            lines.append(f"=== {section} ===")
            if isinstance(data, (dict, list)):
                lines.append(
                    json.dumps(scrub_secrets(data), indent=2, ensure_ascii=False)
                )
            else:
                lines.append(str(scrub_secrets(data)))
            lines.append("")
        return "\n".join(lines)

    def _format_markdown_report(self, results):
        lines = []
        for section, data in results.items():
            lines.append(f"## {section}")
            if isinstance(data, (dict, list)):
                lines.append("```json")
                lines.append(
                    json.dumps(scrub_secrets(data), indent=2, ensure_ascii=False)
                )
                lines.append("```")
            else:
                lines.append(str(scrub_secrets(data)))
            lines.append("")
        return "\n".join(lines)

    def _format_html_report(self, results):
        html = ["<html><body>"]
        for section, data in results.items():
            html.append(f"<h2>{section}</h2>")
            if isinstance(data, (dict, list)):
                html.append(
                    f"<pre>{json.dumps(scrub_secrets(data), indent=2, ensure_ascii=False)}</pre>"
                )
            else:
                html.append(f"<p>{str(scrub_secrets(data))}</p>")
        html.append("</body></html>")
        return "\n".join(html)

    def _format_json_report(self, results: Dict[str, Any]) -> str:
        """Formats analysis results into a JSON report."""
        return json.dumps(scrub_secrets(results), indent=2, ensure_ascii=False)

    def _format_pdf_report(self, results: Dict[str, Any]) -> bytes:
        """Formats analysis results into a PDF report."""
        try:
            from weasyprint import HTML
        except ImportError:
            raise ImportError(
                "weasyprint library not found. Please install it to generate PDF reports."
            )

        html_content = self._format_html_report(results)
        return HTML(string=html_content).write_pdf()

    def generate_report(
        self,
        results: Dict[str, Any],
        report_name: str = "analysis_report",
        report_format: Literal["text", "markdown", "html", "pdf", "json"] = "html",
        user_id: str = "system",
    ) -> str:
        from .core_audit import audit_logger

        logger.info(f"Generating {report_format} report: {report_name}...")

        formatted_content: Any  # Can be str or bytes for PDF
        try:
            if report_format == "text":
                formatted_content = self._format_text_report(results).encode("utf-8")
            elif report_format == "markdown":
                formatted_content = self._format_markdown_report(results).encode(
                    "utf-8"
                )
            elif report_format == "html":
                formatted_content = self._format_html_report(results).encode("utf-8")
            elif report_format == "json":
                formatted_content = self._format_json_report(results).encode("utf-8")
            elif report_format == "pdf":
                formatted_content = self._format_pdf_report(results)
            else:
                raise ValueError(f"Unsupported report format: {report_format}")
        except Exception as e:
            logger.error(f"Failed to format {report_format} report: {e}", exc_info=True)
            alert_operator(
                f"ERROR: Failed to format {report_format} report: {e}", level="ERROR"
            )
            formatted_content = f"Error generating report content: {e}".encode("utf-8")

        if self.production_mode:
            try:
                formatted_content = encrypt_report_content(formatted_content)
                logger.info("Report content encrypted successfully.")
            except Exception as e:
                logger.critical(
                    f"CRITICAL: Report encryption failed: {e}. Report will NOT be saved.",
                    exc_info=True,
                )
                alert_operator(
                    f"CRITICAL: Report encryption failed: {e}. Aborting report save.",
                    level="CRITICAL",
                )
                raise AnalyzerCriticalError("Report encryption failed.")

        timestamp = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        unique_report_name = f"{report_name}_{timestamp}"

        # B. Output File Extension Mapping
        format_ext_map = {
            "text": ".txt",
            "markdown": ".md",
            "html": ".html",
            "json": ".json",
            "pdf": ".pdf",
        }
        file_ext = format_ext_map.get(report_format, f".{report_format}")
        file_path = os.path.join(self.output_dir, f"{unique_report_name}{file_ext}")

        try:
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            _atomic_write_bytes(file_path, formatted_content)
            os.chmod(file_path, 0o600)
            logger.info(f"Report saved to: {file_path}")

            audit_logger.log_event(
                "report_generated",
                report_path=file_path,
                report_name=report_name,
                report_format=report_format,
                generated_by=user_id,
                timestamp=datetime.datetime.utcnow().isoformat() + "Z",
                summary={
                    "overall_status": results.get("overall_status", "N/A"),
                    "violations_count": len(results.get("policy_violations", [])),
                },
            )
            return file_path
        except IOError as e:
            logger.error(f"Failed to save report to {file_path}: {e}", exc_info=True)
            alert_operator(
                f"CRITICAL: Failed to save report to {file_path}: {e}", level="CRITICAL"
            )
            raise RuntimeError(f"Failed to save report: {e}")
        except Exception as e:
            logger.critical(
                f"CRITICAL: An unexpected error occurred during report generation: {e}",
                exc_info=True,
            )
            alert_operator(
                f"CRITICAL: Unexpected error during report generation: {e}",
                level="CRITICAL",
            )
            raise RuntimeError(f"Unexpected error during report generation: {e}")


def get_reports_dir():
    if FLASK_AVAILABLE:
        try:
            from flask import current_app

            return current_app.config["REPORTS_DIR"]
        except Exception:
            pass
    return os.path.abspath("reports")


# --- Flask Endpoints (at module scope) ---
if FLASK_AVAILABLE:

    @app.route("/login", methods=["POST"])
    def login():
        from .core_audit import audit_logger

        username = request.json.get("username", None)
        password = request.json.get("password", None)
        admin_password = SECRETS_MANAGER.get_secret("DASHBOARD_ADMIN_PASSWORD")
        if PRODUCTION_MODE and not admin_password:
            raise RuntimeError(
                "[CRITICAL][REPORT] Missing DASHBOARD_ADMIN_PASSWORD in production"
            )

        if username == "admin" and password == admin_password:
            access_token = create_access_token(
                identity=username, additional_claims={"roles": ["admin"]}
            )
            audit_logger.log_event("user_login_success", username=username)
            return jsonify(access_token=access_token)
        audit_logger.log_event(
            "user_login_failure", username=username, reason="invalid_credentials"
        )
        return jsonify({"msg": "Bad username or password"}), 401

    @app.route("/health")
    @jwt_required()
    def health_check_endpoint():
        from .core_audit import audit_logger

        status = {"status": "healthy", "components": {"mock_db": "ok"}}
        audit_logger.log_event(
            "dashboard_access", endpoint="/health", user=get_jwt()["sub"]
        )
        return jsonify(status)

    @app.route("/report/<report_name_with_ext>")
    @jwt_required()
    def get_report_endpoint(report_name_with_ext: str):
        from .core_audit import audit_logger

        claims = get_jwt()
        if "admin" not in claims.get("roles", []):
            audit_logger.log_event(
                "report_access_denied",
                report_name=report_name_with_ext,
                user=claims["sub"],
                reason="admin_access_required",
            )
            return jsonify({"message": "Admin access required"}), 403

        sanitized_report_name = Path(report_name_with_ext).name
        report_path = os.path.join(get_reports_dir(), sanitized_report_name)

        audit_logger.log_event(
            "report_access_attempt",
            report_name=sanitized_report_name,
            user=claims["sub"],
        )

        if os.path.exists(report_path):
            try:
                with open(report_path, "rb") as f:
                    encrypted_content = f.read()

                decrypted_content = decrypt_report_content(encrypted_content)

                _, ext = os.path.splitext(sanitized_report_name)
                content_type = {
                    ".html": "text/html",
                    ".pdf": "application/pdf",
                    ".json": "application/json",
                    ".txt": "text/plain",
                    ".md": "text/markdown",
                }.get(ext.lower(), "application/octet-stream")

                audit_logger.log_event(
                    "report_viewed",
                    report_name=sanitized_report_name,
                    user=claims["sub"],
                )
                return send_file(
                    io.BytesIO(decrypted_content),
                    mimetype=content_type,
                    as_attachment=False,
                    download_name=sanitized_report_name,
                )
            except Exception as e:
                logger.error(
                    f"Error serving report {sanitized_report_name}: {e}", exc_info=True
                )
                audit_logger.log_event(
                    "report_access_failure",
                    report_name=sanitized_report_name,
                    user=claims["sub"],
                    error=str(e),
                )
                alert_operator(
                    f"ERROR: Failed to serve report {sanitized_report_name}: {e}",
                    level="ERROR",
                )
                return jsonify({"message": "Error serving report"}), 500
        audit_logger.log_event(
            "report_access_failure",
            report_name=sanitized_report_name,
            user=claims["sub"],
            reason="not_found",
        )
        return jsonify({"message": "Report not found"}), 404

else:

    def login():
        raise ImportError("Flask is not available, 'login' endpoint is not usable.")

    def health_check_endpoint():
        raise ImportError(
            "Flask is not available, 'health_check_endpoint' is not usable."
        )

    def get_report_endpoint(report_name_with_ext: str):
        raise ImportError(
            "Flask is not available, 'get_report_endpoint' is not usable."
        )


def start_dashboard(host: str = "127.0.0.1", port: int = 5000):
    if FLASK_AVAILABLE:
        logger.info(f"Starting dashboard on http://{host}:{port}")
        app.run(host=host, port=port, debug=False)
    else:
        logger.warning("Cannot start dashboard: Flask not available.")


# Public facing functions (used by analyzer.py)
def generate_report(
    results: Dict[str, Any],
    report_name: str = "analysis_report",
    report_format: Literal["text", "markdown", "html", "pdf", "json"] = "html",
    user_id: str = "system",
) -> str:
    approved_dirs = [get_reports_dir(), os.path.abspath("/var/log/analyzer_reports")]
    generator = ReportGenerator(
        output_dir=get_reports_dir(), approved_report_dirs=approved_dirs
    )

    original_generate_report = generator.generate_report

    def _generate_report_with_encryption(*args, **kwargs):
        file_path = original_generate_report(*args, **kwargs)
        if generator.production_mode:
            with open(file_path, "rb") as f:
                content_bytes = f.read()
            encrypted_content = encrypt_report_content(content_bytes)
            with open(file_path, "wb") as f:
                f.write(encrypted_content)
            logger.info(f"Report {file_path} encrypted at rest.")
        return file_path

    generator.generate_report = _generate_report_with_encryption

    try:
        return generator.generate_report(results, report_name, report_format, user_id)
    finally:
        generator.generate_report = original_generate_report


def start_dashboard_server(host: str = "127.0.0.1", port: int = 5000):
    if FLASK_AVAILABLE:
        start_dashboard(host, port)
    else:
        logger.error(
            "Dashboard cannot be started: Flask or Flask-JWT-Extended is not installed."
        )


# Optional: Add to __all__ to explicitly define public API
__all__ = [
    "AnalyzerCriticalError",
    "ReportGenerator",
    "generate_report",
    "start_dashboard_server",
    "encrypt_report_content",
    "decrypt_report_content",
    "get_reports_dir",
]

if FLASK_AVAILABLE:
    __all__.extend(["login", "health_check_endpoint", "get_report_endpoint"])

# Example usage (for testing this module independently)
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )
    logger.setLevel(logging.DEBUG)

    class DummySecretsManager:
        def get_secret(
            self, key: str, default: Optional[str] = None, required: bool = True
        ) -> Optional[str]:
            if key == "JWT_SECRET_KEY":
                return "super-secret-jwt-key"
            if key == "DASHBOARD_ADMIN_PASSWORD":
                return "admin_secure_password"
            if key == "REPORT_KMS_KEY_ALIAS":
                return "alias/analyzer-report-key"
            if required:
                raise ValueError(f"Missing required secret for test: {key}")
            return default

    def alert_operator(message: str, level: str = "CRITICAL"):
        logger.critical(f"[OPS ALERT - {level}] {message}")

    class DummyAuditLogger:
        def log_event(self, event_type: str, **kwargs: Any):
            logger.info(f"[AUDIT_LOG] {event_type}: {kwargs}")

    SECRETS_MANAGER = DummySecretsManager()
    audit_logger = DummyAuditLogger()

    class MockKMSClient:
        def __init__(self):
            self.key_exists = True

        def describe_key(self, KeyId):
            if not self.key_exists:
                raise ClientError(
                    {"Error": {"Code": "NotFoundException"}}, "DescribeKey"
                )

        def encrypt(self, KeyId, Plaintext):
            return {"CiphertextBlob": b"encrypted_" + Plaintext}

        def decrypt(self, CiphertextBlob):
            return {"Plaintext": CiphertextBlob.replace(b"encrypted_", b"", 1)}

    boto3.client = lambda service_name, region_name: MockKMSClient()

    sys.modules["core_secrets"] = sys.modules["__main__"]
    sys.modules["core_utils"] = sys.modules["__main__"]
    sys.modules["core_audit"] = sys.modules["__main__"]

    sample_results = {
        "graph_analysis": {
            "nodes": 10,
            "edges": 25,
            "cycles_detected": 2,
            "dead_nodes_found": 1,
            "some_secrets": "secret_token_abc",
        },
        "policy_violations": [
            {
                "rule_id": "PR001",
                "severity": "high",
                "message": "Denied import detected.",
                "sensitive_field": "secret_token_abc",
            },
            {
                "rule_id": "DL001",
                "severity": "medium",
                "message": "Too many dependencies.",
            },
            "A list of strings with secret_token_abc",
        ],
        "security_scan_results": {
            "bandit_issues_count": 5,
            "pip_audit_vulnerabilities_count": 3,
            "critical_vulnerabilities": [
                "CVE-2023-1234",
                {"id": "CVE-2023-4567", "details": "This has a secret_token_abc."},
            ],
        },
        "ai_suggestions": [
            "Refactor data access layer for better abstraction.",
            "Implement stricter input validation in API endpoints.",
        ],
    }

    test_approved_dirs = [
        os.path.abspath("test_reports_approved"),
        os.path.abspath("/var/log/analyzer_reports"),
    ]
    os.makedirs(test_approved_dirs[0], exist_ok=True)

    report_generator = ReportGenerator(
        output_dir=test_approved_dirs[0], approved_report_dirs=test_approved_dirs
    )

    print("\n--- Generating Text Report ---")
    text_report_path = report_generator.generate_report(
        sample_results, "my_text_report", "text", user_id="test_user_1"
    )
    print(f"Text report saved to: {text_report_path}")
    with open(text_report_path, "r") as f:
        content = f.read()
        print(f"Content snippet:\n{content[:500]}...\n")
        assert "secret_token_abc" not in content
        assert "[SCRUBBED]" in content

    print("\n--- Generating Markdown Report ---")
    markdown_report_path = report_generator.generate_report(
        sample_results, "my_markdown_report", "markdown", user_id="test_user_2"
    )
    print(f"Markdown report saved to: {markdown_report_path}")
    with open(markdown_report_path, "r") as f:
        content = f.read()
        assert "secret_token_abc" not in content
        assert "[SCRUBBED]" in content

    print("\n--- Generating HTML Report ---")
    html_report_path = report_generator.generate_report(
        sample_results, "my_html_report", "html", user_id="test_user_3"
    )
    print(f"HTML report saved to: {html_report_path}")
    with open(html_report_path, "r") as f:
        content = f.read()
        assert "secret_token_abc" not in content
        assert "[SCRUBBED]" in content

    print("\n--- Generating JSON Report ---")
    json_report_path = report_generator.generate_report(
        sample_results, "my_json_report", "json", user_id="test_user_4"
    )
    print(f"JSON report saved to: {json_report_path}")
    with open(json_report_path, "r") as f:
        content = f.read()
        assert "secret_token_abc" not in content
        assert "[SCRUBBED]" in content

    print("\n--- Generating PDF Report (requires weasyprint) ---")
    try:
        pdf_report_path = report_generator.generate_report(
            sample_results, "my_pdf_report", "pdf", user_id="test_user_5"
        )
        print(f"PDF report saved to: {pdf_report_path}")
    except ImportError as e:
        print(f"Skipping PDF report generation: {e}")
    except Exception as e:
        print(f"Error generating PDF report: {e}")

    print("\n--- Testing Write to Unapproved Directory (expecting abort in prod) ---")
    unapproved_dir = os.path.abspath("unapproved_reports")
    try:
        original_production_mode = PRODUCTION_MODE
        os.environ["PRODUCTION_MODE"] = "true"

        ReportGenerator(
            output_dir=unapproved_dir, approved_report_dirs=test_approved_dirs
        )
    except AnalyzerCriticalError as e:
        print(
            f"Caught expected AnalyzerCriticalError for writing to unapproved directory: {e}"
        )
    except Exception as e:
        print(f"Caught unexpected exception: {e}")
    finally:
        os.environ["PRODUCTION_MODE"] = str(original_production_mode).lower()
        if os.path.exists(unapproved_dir):
            shutil.rmtree(unapproved_dir)

    print("\n--- Cleaning up test reports ---")
    if os.path.exists(test_approved_dirs[0]):
        shutil.rmtree(test_approved_dirs[0])
    if os.path.exists("reports"):
        shutil.rmtree("reports")
