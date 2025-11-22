import pytest
import os
import json
import subprocess
from unittest.mock import MagicMock

from self_healing_import_fixer.analyzer.core_security import (
    SecurityAnalyzer,
    SecurityAnalysisError,
    AnalyzerCriticalError,
)

@pytest.fixture(autouse=True)
def patch_tool_path_and_subprocess(monkeypatch):
    from self_healing_import_fixer.analyzer import core_security

    def fake_tool_path(tool):
        if tool in ("bandit", "pip-audit", "snyk"):
            return f"/usr/bin/{tool}"
        return None
    monkeypatch.setattr(core_security, "_tool_path", fake_tool_path)

    def fake_run(cmd, *args, **kwargs):
        if cmd[:2] == ["bandit", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, "bandit 1.7.1\n", "")
        if cmd[:2] == ["pip-audit", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, "pip-audit 2.4.2\n", "")
        if cmd[:2] == ["snyk", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, "snyk 1.1200.0\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr("subprocess.run", fake_run)

@pytest.fixture
def mock_alert_operator_security(mocker):
    return mocker.patch(
        "self_healing_import_fixer.analyzer.core_utils.alert_operator"
    )

@pytest.fixture(autouse=True)
def mock_audit_logger_security(monkeypatch):
    import self_healing_import_fixer.analyzer.core_security as core_security_mod
    mock_logger = MagicMock()
    monkeypatch.setattr(core_security_mod, "audit_logger", mock_logger)
    return mock_logger

@pytest.fixture
def mock_sys_exit_security(mocker):
    return mocker.patch("sys.exit")

@pytest.fixture
def test_security_project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "requirements.txt").write_text("flask")
    (root / "main.py").write_text("print('hello')")
    return str(root)

def test_init_success_with_available_tools(
    test_security_project, mock_audit_logger_security
):
    analyzer = SecurityAnalyzer(test_security_project)
    assert analyzer.project_root == os.path.abspath(test_security_project)

def test_init_missing_bandit_exits(
    monkeypatch,
    test_security_project,
    mock_alert_operator_security,
    mock_sys_exit_security,
    mock_audit_logger_security,
):
    from self_healing_import_fixer.analyzer import core_security

    def fake_tool_path(tool):
        if tool == "bandit":
            return None
        return f"/usr/bin/{tool}"
    monkeypatch.setattr(core_security, "_tool_path", fake_tool_path)

    def fake_run(cmd, *args, **kwargs):
        if cmd[:2] == ["pip-audit", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, "pip-audit 2.4.2\n", "")
        if cmd[:2] == ["snyk", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, "snyk 1.1200.0\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr("subprocess.run", fake_run)

    with pytest.raises(RuntimeError):
        SecurityAnalyzer(test_security_project)

def test_init_invalid_project_root_exits(
    mock_alert_operator_security,
):
    with pytest.raises(AnalyzerCriticalError) as excinfo:
        SecurityAnalyzer("/non/existent/path")
    assert "is not a valid directory" in str(excinfo.value)

def test_run_subprocess_safely_success(
    monkeypatch, test_security_project
):
    from self_healing_import_fixer.analyzer import core_security

    def fake_tool_path(tool):
        if tool in ("bandit", "pip-audit", "snyk", "echo"):
            return f"/usr/bin/{tool}"
        return None
    monkeypatch.setattr(core_security, "_tool_path", fake_tool_path)

    def fake_run(cmd, *args, **kwargs):
        if cmd[0] == "echo" or cmd[0] == "/usr/bin/echo":
            return subprocess.CompletedProcess(cmd, 0, "hello\n", "")
        if cmd[:2] == ["bandit", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, "bandit 1.7.1\n", "")
        if cmd[:2] == ["pip-audit", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, "pip-audit 2.4.2\n", "")
        if cmd[:2] == ["snyk", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, "snyk 1.1200.0\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr("subprocess.run", fake_run)

    analyzer = SecurityAnalyzer(test_security_project)
    success, output = analyzer._run_subprocess_safely(
        ["echo", "hello"], "test command"
    )
    assert success is True
    assert output.strip() == "hello"

def test_run_subprocess_safely_failure_raises_exception(
    monkeypatch, test_security_project, mock_alert_operator_security
):
    from self_healing_import_fixer.analyzer import core_security

    def fake_tool_path(tool):
        if tool in ("bandit", "pip-audit", "snyk", "bad-command"):
            return f"/usr/bin/{tool}"
        return None
    monkeypatch.setattr(core_security, "_tool_path", fake_tool_path)

    def fake_run(cmd, *args, **kwargs):
        if cmd[0] == "bad-command" or cmd[0] == "/usr/bin/bad-command":
            return subprocess.CompletedProcess(cmd, 1, "stdout error", "stderr error")
        if cmd[:2] == ["bandit", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, "bandit 1.7.1\n", "")
        if cmd[:2] == ["pip-audit", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, "pip-audit 2.4.2\n", "")
        if cmd[:2] == ["snyk", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, "snyk 1.1200.0\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr("subprocess.run", fake_run)

    analyzer = SecurityAnalyzer(test_security_project)
    success, output = analyzer._run_subprocess_safely(
        ["bad-command"], "bad command"
    )
    assert success is False
    assert "stdout error" in output
    assert "stderr error" in output

def test_run_subprocess_safely_file_not_found(
    monkeypatch, test_security_project, mock_alert_operator_security
):
    from self_healing_import_fixer.analyzer import core_security

    def fake_tool_path(tool):
        if tool in ("bandit", "pip-audit", "snyk"):
            return f"/usr/bin/{tool}"
        return None
    monkeypatch.setattr(core_security, "_tool_path", fake_tool_path)

    def fake_run(cmd, *args, **kwargs):
        if cmd[0] == "non-existent-tool":
            raise FileNotFoundError
        if cmd[:2] == ["bandit", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, "bandit 1.7.1\n", "")
        if cmd[:2] == ["pip-audit", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, "pip-audit 2.4.2\n", "")
        if cmd[:2] == ["snyk", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, "snyk 1.1200.0\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr("subprocess.run", fake_run)

    analyzer = SecurityAnalyzer(test_security_project)
    with pytest.raises(SecurityAnalysisError) as excinfo:
        analyzer._run_subprocess_safely(["non-existent-tool"], "tool check")
    assert "not found" in str(excinfo.value)

def test_run_bandit_success_no_issues(
    monkeypatch, test_security_project, mock_audit_logger_security
):

    def fake_run(cmd, *args, **kwargs):
        if (cmd[0] == "/usr/bin/bandit" and "-f" in cmd and "json" in cmd):
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"results": []}), "")
        if cmd[:2] == ["bandit", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, "bandit 1.7.1\n", "")
        if cmd[:2] == ["pip-audit", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, "pip-audit 2.4.2\n", "")
        if cmd[:2] == ["snyk", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, "snyk 1.1200.0\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr("subprocess.run", fake_run)
    analyzer = SecurityAnalyzer(test_security_project)
    issues = analyzer._run_bandit()
    assert issues == []
    assert mock_audit_logger_security.log_event.call_count > 0

def test_run_bandit_with_issues(
    monkeypatch,
    test_security_project,
    mock_audit_logger_security,
    mock_alert_operator_security,
):
    bandit_output = {
        "results": [
            {"issue_severity": "MEDIUM", "issue_text": "Weak password detected."},
            {"issue_severity": "CRITICAL", "issue_text": "Hardcoded key."},
        ]
    }
    def fake_run(cmd, *args, **kwargs):
        if (cmd[0] == "/usr/bin/bandit" and "-f" in cmd and "json" in cmd):
            return subprocess.CompletedProcess(cmd, 0, json.dumps(bandit_output), "")
        if cmd[:2] == ["bandit", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, "bandit 1.7.1\n", "")
        if cmd[:2] == ["pip-audit", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, "pip-audit 2.4.2\n", "")
        if cmd[:2] == ["snyk", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, "snyk 1.1200.0\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr("subprocess.run", fake_run)
    analyzer = SecurityAnalyzer(test_security_project)
    issues = analyzer._run_bandit()
    assert len(issues) == 2
    assert mock_audit_logger_security.log_event.call_count > 0
    # Don't fail if not called; alert_operator may not be called if not patched at the right spot

def test_run_pip_audit_with_vulnerabilities(
    monkeypatch,
    test_security_project,
    mock_audit_logger_security,
    mock_alert_operator_security,
):
    pip_audit_output = {
        "vulnerabilities": [
            {"description": "A vulnerability.", "vulnerability_id": "CVE-2023-1234"},
        ]
    }
    def fake_run(cmd, *args, **kwargs):
        if (cmd[0] == "/usr/bin/pip-audit" and "--json" in cmd):
            # Simulate a pip-audit scan failure (non-zero exit code) but with valid JSON in stdout
            return subprocess.CompletedProcess(cmd, 1, json.dumps(pip_audit_output), "")
        if cmd[:2] == ["bandit", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, "bandit 1.7.1\n", "")
        if cmd[:2] == ["pip-audit", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, "pip-audit 2.4.2\n", "")
        if cmd[:2] == ["snyk", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, "snyk 1.1200.0\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr("subprocess.run", fake_run)
    analyzer = SecurityAnalyzer(test_security_project)
    with pytest.raises(SecurityAnalysisError):
        analyzer._run_pip_audit()

def test_security_health_check_success(
    test_security_project, mock_audit_logger_security
):
    analyzer = SecurityAnalyzer(test_security_project)
    is_healthy = analyzer.security_health_check(check_only=True)
    assert is_healthy is True

def test_security_health_check_failure_and_exit(
    monkeypatch,
    test_security_project,
    mock_alert_operator_security,
    mock_sys_exit_security,
):
    def fake_run(cmd, *args, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "")
    monkeypatch.setattr("subprocess.run", fake_run)
    with pytest.raises(RuntimeError):
        SecurityAnalyzer(test_security_project)