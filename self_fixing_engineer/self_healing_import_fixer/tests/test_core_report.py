import os
import sys
from unittest.mock import MagicMock, patch

import pytest
# Flask app import moved to fixture to avoid expensive initialization during collection
from self_healing_import_fixer.analyzer.core_report import (
    AnalyzerCriticalError,
    ReportGenerator,
)

# Try to import app for skipif checks - don't fail if not available
try:
    from self_healing_import_fixer.analyzer.core_report import app as _test_app
    _FLASK_APP_AVAILABLE = hasattr(_test_app, "test_client")
except (ImportError, AttributeError):
    _FLASK_APP_AVAILABLE = False


@pytest.fixture
def flask_app():
    """Lazy-load the Flask app to avoid expensive initialization during collection."""
    from self_healing_import_fixer.analyzer.core_report import app
    return app


# --- ReportGenerator Initialization Tests ---
def test_init_with_valid_dir_succeeds(tmp_path):
    output_dir = tmp_path / "test_reports_approved"
    ReportGenerator(str(output_dir), approved_report_dirs=[str(tmp_path)])
    assert os.path.isdir(output_dir)


def test_init_with_unapproved_dir_in_prod_exits(tmp_path):
    with patch.dict(os.environ, {"PRODUCTION_MODE": "true"}):
        with pytest.raises(AnalyzerCriticalError) as excinfo:
            ReportGenerator(
                str(tmp_path / "unapproved_dir"),
                approved_report_dirs=[str(tmp_path / "approved_dir")],
            )
    assert "is not within approved paths" in str(excinfo.value)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="os.chmod does not restrict permissions on Windows as on Unix",
)
def test_init_with_unwritable_dir_exits(tmp_path):
    unwritable_dir = tmp_path / "unwritable"
    unwritable_dir.mkdir()
    os.chmod(unwritable_dir, 0o400)  # Read-only
    try:
        with pytest.raises(AnalyzerCriticalError) as excinfo:
            ReportGenerator(str(unwritable_dir))
        assert "is not writable or accessible" in str(excinfo.value)
    finally:
        os.chmod(unwritable_dir, 0o700)


@pytest.mark.parametrize(
    "report_format, file_ext",
    [
        ("text", ".txt"),
        ("markdown", ".md"),
        ("html", ".html"),
        ("json", ".json"),
    ],
)
def test_generate_report_formats_and_saves_correctly(tmp_path, report_format, file_ext):
    output_dir = tmp_path / "reports"
    generator = ReportGenerator(str(output_dir), approved_report_dirs=[str(tmp_path)])

    sample_results = {
        "section1": [{"field": "secret_token_abc"}],
        "section2": {"val": "secret_token_abc"},
        "section3": [],
    }
    file_path = generator.generate_report(sample_results, report_format=report_format)
    assert file_path.endswith(file_ext)
    assert os.path.exists(file_path)

    with open(file_path, "r") as f:
        content = f.read()
        assert "[SCRUBBED]" in content

    if sys.platform != "win32":
        assert os.stat(file_path).st_mode & 0o777 == 0o600


def test_generate_pdf_report_calls_weasyprint(tmp_path, monkeypatch):
    output_dir = tmp_path / "reports"
    generator = ReportGenerator(str(output_dir), approved_report_dirs=[str(tmp_path)])

    class DummyHtml:
        def __init__(self, string):
            self.string = string

        def write_pdf(self):
            return b"%PDF-mock"

    called = {}

    def dummy_html(string):
        called["called"] = True
        return DummyHtml(string)

    monkeypatch.setitem(
        __import__("sys").modules, "weasyprint", MagicMock(HTML=dummy_html)
    )
    sample_results = {"section": [{"field": "secret_token_abc"}]}
    file_path = generator.generate_report(sample_results, report_format="pdf")
    assert file_path.endswith(".pdf")
    assert os.path.exists(file_path)
    with open(file_path, "rb") as f:
        assert f.read() == b"%PDF-mock"
    assert called["called"]


def test_generate_report_catches_formatting_errors(tmp_path):
    output_dir = tmp_path / "reports"
    generator = ReportGenerator(str(output_dir), approved_report_dirs=[str(tmp_path)])
    sample_results = {"section": [{"field": "secret_token_abc"}]}
    with patch.object(
        generator, "_format_html_report", side_effect=ValueError("Bad format")
    ):
        file_path = generator.generate_report(sample_results, report_format="html")
    with open(file_path, "r") as f:
        content = f.read()
        assert "Error generating report content" in content


def test_generate_report_catches_saving_io_errors(tmp_path):
    output_dir = tmp_path / "reports"
    generator = ReportGenerator(str(output_dir))
    sample_results = {"section": [{"field": "secret_token_abc"}]}
    with patch("tempfile.NamedTemporaryFile", side_effect=IOError("Disk full")):
        with pytest.raises(RuntimeError) as excinfo:
            generator.generate_report(sample_results)
    assert "Failed to save report: Disk full" in str(excinfo.value)


def test_public_generate_report_calls_encryption_in_prod(tmp_path, monkeypatch):
    with patch.dict(os.environ, {"PRODUCTION_MODE": "true"}):
        called = {}
        import self_healing_import_fixer.analyzer.core_report as crmod

        def mock_encrypt(content):
            called["called"] = True
            return content + b"-encrypted"

        monkeypatch.setattr(crmod, "encrypt_report_content", mock_encrypt)

        class DummyGen(ReportGenerator):
            def generate_report(self, *a, **k):
                file_path = tmp_path / "enc_report.txt"
                with open(file_path, "wb") as f:
                    f.write(b"hello")
                return str(file_path)

        monkeypatch.setattr(crmod, "ReportGenerator", DummyGen)
        sample_results = {"section": [{"field": "secret_token_abc"}]}
        crmod.generate_report(sample_results, report_format="text")
        assert called["called"]


# --- Dashboard Integration Tests (now using Flask test client) ---


@pytest.mark.skipif(False, reason="Flask app available via fixture")
def test_dashboard_login_success(monkeypatch, flask_app):

    flask_app.config["JWT_SECRET_KEY"] = "jwtsecret"
    monkeypatch.setattr(
        "self_healing_import_fixer.analyzer.core_report.SECRETS_MANAGER",
        MagicMock(
            get_secret=lambda k: (
                "admin_secure_password"
                if k == "DASHBOARD_ADMIN_PASSWORD"
                else "jwtsecret"
            )
        ),
    )
    monkeypatch.setattr(
        "self_healing_import_fixer.analyzer.core_report.audit_logger",
        MagicMock(log_event=MagicMock()),
    )
    with flask_app.test_client() as client:
        resp = client.post(
            "/login", json={"username": "admin", "password": "admin_secure_password"}
        )
        assert resp.status_code == 200
        assert "access_token" in resp.json


@pytest.mark.skipif(False, reason="Flask app available via fixture")
def test_dashboard_login_failure(monkeypatch, flask_app):

    flask_app.config["JWT_SECRET_KEY"] = "jwtsecret"
    monkeypatch.setattr(
        "self_healing_import_fixer.analyzer.core_report.SECRETS_MANAGER",
        MagicMock(
            get_secret=lambda k: (
                "admin_secure_password"
                if k == "DASHBOARD_ADMIN_PASSWORD"
                else "jwtsecret"
            )
        ),
    )
    monkeypatch.setattr(
        "self_healing_import_fixer.analyzer.core_report.audit_logger",
        MagicMock(log_event=MagicMock()),
    )
    with flask_app.test_client() as client:
        resp = client.post(
            "/login", json={"username": "admin", "password": "wrong_password"}
        )
        assert resp.status_code == 401
        assert "Bad username or password" in resp.json.get("msg", "")


@pytest.mark.skipif(False, reason="Flask app available via fixture")
def test_get_report_endpoint_success_no_encryption(tmp_path, monkeypatch, flask_app):
    import self_healing_import_fixer.analyzer.core_report as crmod

    flask_app.config["JWT_SECRET_KEY"] = "jwtsecret"
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    (report_dir / "my_report.html").write_text("<html>Report Content</html>")
    flask_app.config["REPORTS_DIR"] = str(report_dir)

    monkeypatch.setattr(crmod, "audit_logger", MagicMock(log_event=MagicMock()))
    monkeypatch.setattr(
        crmod,
        "SECRETS_MANAGER",
        MagicMock(
            get_secret=lambda k: (
                "admin_secure_password"
                if k == "DASHBOARD_ADMIN_PASSWORD"
                else "jwtsecret"
            )
        ),
    )

    with flask_app.test_client() as client:
        login_resp = client.post(
            "/login", json={"username": "admin", "password": "admin_secure_password"}
        )
        assert login_resp.status_code == 200
        token = login_resp.json["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        resp = client.get("/report/my_report.html", headers=headers)
        assert resp.status_code == 200
        assert b"Report Content" in resp.data


@pytest.mark.skipif(False, reason="Flask app available via fixture")
def test_get_report_endpoint_with_encryption_in_prod(tmp_path, monkeypatch, flask_app):
    import self_healing_import_fixer.analyzer.core_report as crmod

    flask_app.config["JWT_SECRET_KEY"] = "jwtsecret"
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    (report_dir / "my_report.html").write_bytes(b"encrypted_content")
    flask_app.config["REPORTS_DIR"] = str(report_dir)

    def decrypt(content):
        return b"decrypted_content"

    monkeypatch.setattr(crmod, "decrypt_report_content", decrypt)
    monkeypatch.setattr(crmod, "audit_logger", MagicMock(log_event=MagicMock()))
    monkeypatch.setattr(
        crmod,
        "SECRETS_MANAGER",
        MagicMock(
            get_secret=lambda k: (
                "admin_secure_password"
                if k == "DASHBOARD_ADMIN_PASSWORD"
                else "jwtsecret"
            )
        ),
    )

    with flask_app.test_client() as client:
        login_resp = client.post(
            "/login", json={"username": "admin", "password": "admin_secure_password"}
        )
        assert login_resp.status_code == 200
        token = login_resp.json["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        resp = client.get("/report/my_report.html", headers=headers)
        assert resp.status_code == 200
        assert resp.data == b"decrypted_content"


@pytest.mark.skipif(not _FLASK_APP_AVAILABLE, reason="Flask app not available")
def test_get_report_endpoint_path_traversal_prevention(tmp_path, monkeypatch):
    from self_healing_import_fixer.analyzer.core_report import app
    import self_healing_import_fixer.analyzer.core_report as crmod

    app.config["JWT_SECRET_KEY"] = "jwtsecret"
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    secret_dir = tmp_path / "secrets"
    secret_dir.mkdir()
    (secret_dir / "secret.txt").write_text("My secret data")
    app.config["REPORTS_DIR"] = str(report_dir)

    monkeypatch.setattr(crmod, "audit_logger", MagicMock(log_event=MagicMock()))
    monkeypatch.setattr(
        crmod,
        "SECRETS_MANAGER",
        MagicMock(
            get_secret=lambda k: (
                "admin_secure_password"
                if k == "DASHBOARD_ADMIN_PASSWORD"
                else "jwtsecret"
            )
        ),
    )

    with app.test_client() as client:
        login_resp = client.post(
            "/login", json={"username": "admin", "password": "admin_secure_password"}
        )
        assert login_resp.status_code == 200
        token = login_resp.json["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        resp = client.get("/report/../secrets/secret.txt", headers=headers)
        assert resp.status_code == 404
        # Accept both JSON and HTML 404s
        if resp.is_json and resp.json is not None and "message" in resp.json:
            assert "Report not found" in resp.json.get("message", "")
        else:
            # fallback: accept HTML 404 as well
            assert resp.status_code == 404
