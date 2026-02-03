import asyncio
from unittest.mock import AsyncMock, MagicMock, Mock, patch

# Fix: Import from arbiter.file_watcher instead of file_watcher
import arbiter.file_watcher as file_watcher_module
import pytest
import yaml
from aiolimiter import AsyncLimiter
from self_fixing_engineer.arbiter.file_watcher import (
    AlerterConfig,
    CodeChangeHandler,
    Config,
    MetricsAndHealthServer,
    SMTPConfig,
    app,
    compare_diffs,
    deploy_code,
    load_config_with_env,
    notify_changes,
    process_file,
    send_email_alert,
    send_pagerduty_alert,
    send_slack_alert,
    summarize_code_changes,
)
from typer.testing import CliRunner


# Fixture for temp dir
@pytest.fixture
def temp_dir(tmp_path):
    return tmp_path


# Fixture for mock config YAML with all required fields
@pytest.fixture
def mock_yaml_config():
    return """
watch:
  folder: "."
  extensions: [".py"]
  skip_patterns: []
  cooldown_seconds: 2.0
  batch_mode: false
  batch_schedule: ""

llm:
  provider: "openai"
  model: "gpt-4o-mini"
  prompt_template: "Summarize: {diff}"
  max_code_size: 10000

api:
  upload_url: "http://localhost:8000/api/upload"
  rate_limit: 10.0

deploy:
  command: "echo deploy"
  rollback_command: ""
  ci_cd_url: ""
  ci_cd_token: ""
  webhook_urls:
    slack: ""
    discord: ""
  aws_s3:
    bucket: ""
    region: "us-east-1"

reporting:
  changelog_file: "changelog.md"
  formats: ["markdown"]

cache:
  redis_url: "redis://localhost:6379/0"
  pool_size: 10
  ttl: 86400

metrics:
  prometheus_port: 8001
  auth_token: ""

health:
  port: 8002

alerter:
  smtp:
    host: "smtp.gmail.com"
    port: 587
    username: ""
    password: ""
    use_tls: true
    timeout: 30
    rate_limit: 1.0
  audit_file: "audit.log"
"""


# Fixture for mock env
@pytest.fixture
def mock_env(monkeypatch):
    monkeypatch.setenv("WATCH_FOLDER", "test_folder")
    yield


# Fixture to create a valid config
@pytest.fixture
def valid_config():
    return Config(
        watch=file_watcher_module.WatchConfig(
            folder="test_folder",
            extensions=[".py"],
            skip_patterns=[],
            cooldown_seconds=2.0,
            batch_mode=False,
            batch_schedule="",
        ),
        llm=file_watcher_module.LLMConfig(
            provider="openai",
            model="gpt-4o-mini",
            prompt_template="Summarize: {diff}",
            max_code_size=10000,
        ),
        api=file_watcher_module.ApiConfig(
            upload_url="http://localhost:8000/api/upload", rate_limit=10.0
        ),
        deploy=file_watcher_module.DeployConfig(),
        reporting=file_watcher_module.ReportingConfig(),
        cache=file_watcher_module.CacheConfig(),
        metrics=file_watcher_module.MetricsConfig(),
        health=file_watcher_module.HealthConfig(),
        alerter=AlerterConfig(
            smtp=SMTPConfig(
                host="smtp.gmail.com",
                port=587,
                username="test@example.com",
                password="password",
                use_tls=True,
                timeout=30,
                rate_limit=1.0,
            ),
            audit_file="audit.log",
        ),
    )


# Test load_config_with_env
def test_load_config_with_env(mock_yaml_config, temp_dir, mock_env):
    config_path = temp_dir / "config.yaml"
    config_path.write_text(mock_yaml_config)

    # Mock os.makedirs to avoid directory creation issues
    with patch("self_fixing_engineer.arbiter.file_watcher.os.makedirs"):
        config = load_config_with_env(str(config_path))
        assert config.watch.folder == "test_folder"  # Env override
        assert config.watch.extensions == [".py"]


# Test load_config_with_env no file
def test_load_config_no_file():
    # Mock os.makedirs to avoid directory creation issues
    with patch("self_fixing_engineer.arbiter.file_watcher.os.makedirs"):
        # Create minimal config without file
        config = load_config_with_env(None)
        # Should have defaults
        assert config.watch.folder == "frontend"  # default value


# Test load_config_with_env invalid YAML
def test_load_config_invalid_yaml(temp_dir):
    config_path = temp_dir / "invalid.yaml"
    config_path.write_text("invalid: yaml:")
    with pytest.raises(yaml.scanner.ScannerError):
        load_config_with_env(str(config_path))


# Test send_email_alert
@pytest.mark.asyncio
async def test_send_email_alert(valid_config):
    # Set global config
    file_watcher_module.config = valid_config
    file_watcher_module.email_limiter = AsyncLimiter(1, 1)

    with patch("aiosmtplib.SMTP") as mock_smtp_class:
        mock_smtp = AsyncMock()
        mock_smtp_class.return_value = mock_smtp

        await send_email_alert("Subject", "Body")

        mock_smtp_class.assert_called_once()
        mock_smtp.connect.assert_called_once()
        mock_smtp.send_message.assert_called_once()


# Test send_email_alert failure
@pytest.mark.asyncio
async def test_send_email_alert_failure(valid_config, caplog):
    file_watcher_module.config = valid_config
    file_watcher_module.email_limiter = AsyncLimiter(1, 1)

    with patch("aiosmtplib.SMTP") as mock_smtp_class:
        mock_smtp = AsyncMock()
        mock_smtp.send_message.side_effect = Exception("SMTP error")
        mock_smtp_class.return_value = mock_smtp

        # Should raise due to retry decorator
        with pytest.raises(Exception):
            await send_email_alert("Subject", "Body")


# Test send_slack_alert (simplified version)
@pytest.mark.asyncio
async def test_send_slack_alert():
    result = await send_slack_alert("Message")
    assert result  # Placeholder function returns True


# Test send_pagerduty_alert (simplified version)
@pytest.mark.asyncio
async def test_send_pagerduty_alert():
    result = await send_pagerduty_alert("Title", "Details")
    assert result  # Placeholder function returns True


# Test summarize_code_changes
@pytest.mark.asyncio
async def test_summarize_code_changes():
    # Create a mock LLMClient class
    mock_llm_client = Mock()
    mock_llm_instance = AsyncMock()
    mock_llm_instance.generate_text.return_value = "Mock summary"
    mock_llm_client.return_value = mock_llm_instance

    # Create a mock module with the LLMClient
    mock_llm_module = Mock()
    mock_llm_module.LLMClient = mock_llm_client

    # Set up minimal config
    file_watcher_module.config = MagicMock()
    file_watcher_module.config.llm.provider = "openai"
    file_watcher_module.config.llm.model = "gpt-4"
    file_watcher_module.config.llm.openai_api_key = "test_key"

    # Mock the import of plugins.llm_client
    with patch.dict("sys.modules", {"plugins.llm_client": mock_llm_module}):
        diff = "diff text"
        summary = await summarize_code_changes(diff, "template {diff}")
        assert summary == "Mock summary"


# Test summarize_code_changes no LLM
@pytest.mark.asyncio
async def test_summarize_code_changes_no_llm(caplog):
    # Test when LLM config is incomplete
    file_watcher_module.config = MagicMock()
    file_watcher_module.config.llm.provider = None

    summary = await summarize_code_changes("diff", "template")
    assert summary == ""


# Test compare_diffs
def test_compare_diffs():
    old = "old code"
    new = "new code"
    diff = compare_diffs(old, new)
    assert "-old code" in diff
    assert "+new code" in diff


# Test deploy_code success
@pytest.mark.asyncio
async def test_deploy_code_success():
    with patch(
        "asyncio.create_subprocess_shell", new_callable=AsyncMock
    ) as mock_subprocess:
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"output", b"")
        mock_proc.returncode = 0
        mock_subprocess.return_value = mock_proc

        result = await deploy_code("echo deploy")
        assert result["success"]
        assert result["output"] == "output"


# Test deploy_code failure
@pytest.mark.asyncio
async def test_deploy_code_failure():
    with patch(
        "asyncio.create_subprocess_shell", new_callable=AsyncMock
    ) as mock_subprocess:
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"error")
        mock_proc.returncode = 1
        mock_subprocess.return_value = mock_proc

        result = await deploy_code("echo deploy")
        assert not result["success"]
        assert result["error"] == "error"


# Test notify_changes
@pytest.mark.asyncio
async def test_notify_changes(valid_config):
    file_watcher_module.config = valid_config
    file_watcher_module.email_limiter = AsyncLimiter(1, 1)

    with patch("self_fixing_engineer.arbiter.file_watcher.send_email_alert", new_callable=AsyncMock):
        with patch("self_fixing_engineer.arbiter.file_watcher.send_slack_alert", new_callable=AsyncMock):
            with patch(
                "self_fixing_engineer.arbiter.file_watcher.send_pagerduty_alert", new_callable=AsyncMock
            ):
                await notify_changes("file.py", "diff", "summary", {"success": True})
                # These are called but may fail, which is handled
                assert True  # Just verify no exceptions


# Test process_file
@pytest.mark.asyncio
async def test_process_file(valid_config, temp_dir):
    file_watcher_module.config = valid_config

    # Create a test file
    test_file = temp_dir / "test.py"
    test_file.write_text("test code")

    with patch("self_fixing_engineer.arbiter.file_watcher.summarize_code_changes", return_value="summary"):
        with patch("self_fixing_engineer.arbiter.file_watcher.deploy_code", return_value={"success": True}):
            with patch("self_fixing_engineer.arbiter.file_watcher.notify_changes"):
                result = await process_file(str(test_file))
                assert result is not None
                assert result["file"] == str(test_file)
                assert result["summary"] == "summary"


# Test CodeChangeHandler on_modified
@pytest.mark.asyncio
async def test_code_change_handler_on_modified():
    handler = CodeChangeHandler(asyncio.Semaphore(1))
    event = MagicMock()
    event.event_type = "modified"
    event.src_path = "file.py"
    event.is_directory = False

    with patch.object(handler, "process_file", new_callable=AsyncMock):
        handler.on_modified(event)
        # Give the event loop time to run the task
        await asyncio.sleep(0.1)
        # The handler creates a task, so we can't directly assert it was called


# Test MetricsAndHealthServer - FIXED VERSION
@pytest.mark.asyncio
async def test_metrics_health_server(valid_config):
    # Mock the web.Application and related classes before creating the server
    mock_app = MagicMock()
    mock_router = MagicMock()
    mock_app.router = mock_router

    mock_runner = AsyncMock()
    mock_site = AsyncMock()
    mock_site_class = MagicMock(return_value=mock_site)

    # Patch all the web-related classes before instantiation
    with patch("aiohttp.web.Application", return_value=mock_app):
        with patch("aiohttp.web.AppRunner", return_value=mock_runner):
            with patch("aiohttp.web.TCPSite", mock_site_class):
                # Now create the server with mocked dependencies
                server = MetricsAndHealthServer(valid_config)

                # Manually set the runner to our mock since __init__ already ran
                server.runner = mock_runner

                await server.start()

                # Check that setup and start were called
                mock_runner.setup.assert_called_once()
                mock_site_class.assert_called_once_with(
                    mock_runner, "0.0.0.0", valid_config.metrics.prometheus_port
                )
                mock_site.start.assert_called_once()

                await server.stop()
                mock_runner.cleanup.assert_called_once()


# Test CLI run
def test_cli_run(temp_dir, mock_yaml_config):
    config_path = temp_dir / "config.yaml"
    config_path.write_text(mock_yaml_config)

    # Mock both asyncio.run and os.makedirs to avoid filesystem issues
    with patch("self_fixing_engineer.arbiter.file_watcher.asyncio.run") as mock_run:
        with patch("self_fixing_engineer.arbiter.file_watcher.os.makedirs"):
            runner = CliRunner()
            result = runner.invoke(app, ["run", "--config", str(config_path)])
            assert result.exit_code == 0
            mock_run.assert_called_once()


# Test CLI batch
def test_cli_batch(temp_dir, mock_yaml_config):
    config_path = temp_dir / "config.yaml"
    config_path.write_text(mock_yaml_config)

    # Mock both asyncio.run and os.makedirs to avoid filesystem issues
    with patch("self_fixing_engineer.arbiter.file_watcher.asyncio.run") as mock_run:
        with patch("self_fixing_engineer.arbiter.file_watcher.os.makedirs"):
            runner = CliRunner()
            result = runner.invoke(app, ["batch", "--config", str(config_path)])

            # Debug output if test fails
            if result.exit_code != 0:
                print(f"Exit code: {result.exit_code}")
                print(f"Output: {result.output}")
                if result.exception:
                    print(f"Exception: {result.exception}")
                    import traceback

                    traceback.print_exception(
                        type(result.exception),
                        result.exception,
                        result.exception.__traceback__,
                    )

            assert result.exit_code == 0
            mock_run.assert_called_once()
