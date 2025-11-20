"""
test_deploy_agent.py
Comprehensive tests for deploy_agent module (orchestration layer).
"""

import pytest
import asyncio
import json
import tempfile
import aiosqlite # FIX: Import aiosqlite
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch, MagicMock, call
from datetime import datetime
import uuid # FIX: Import uuid for the concurrent test fix

# Import the module under test
from generator.agents.deploy_agent.deploy_agent import (
    DeployAgent,
    TargetPlugin,
    PluginRegistry,
    scrub_text,
)
from generator.agents.deploy_agent.deploy_validator import ValidatorRegistry


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def temp_repo():
    """Create a temporary repository for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)
        
        # Create repo structure
        (repo_path / ".git").mkdir()
        (repo_path / "src").mkdir()
        (repo_path / "tests").mkdir()
        
        # Create sample files
        (repo_path / "README.md").write_text("# Test Project\n\nA test repository.")
        (repo_path / "src" / "main.py").write_text("""
def main():
    print("Hello, World!")

if __name__ == "__main__":
    main()
""")
        (repo_path / "requirements.txt").write_text("flask==2.0.1\nrequests==2.26.0")
        (repo_path / "Dockerfile").write_text("""
FROM python:3.9
WORKDIR /app
COPY . .
RUN pip install -r requirements.txt
CMD ["python", "src/main.py"]
""")
        
        yield repo_path

@pytest.fixture
async def agent(temp_repo):
    """Async fixture to create and initialize a DeployAgent."""
    with patch.dict('os.environ', {'TESTING': '1'}):
        agent = DeployAgent(str(temp_repo))
        agent.db_path = str(temp_repo / "test_agent.db")
        await agent._init_db()
        yield agent


@pytest.fixture
def mock_llm_dockerfile_response():
    """Mock LLM response for Dockerfile generation."""
    return {
        "content": """FROM python:3.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
USER appuser
EXPOSE 8000
CMD ["python", "src/main.py"]
""",
        "model": "gpt-4",
        "provider": "openai",
        "tokens": 100
    }


@pytest.fixture
def mock_validation_success():
    """Mock successful validation response."""
    return {
        "valid": True,
        "build_status": "success",
        "lint_status": "passed",
        "lint_issues": [],
        "security_findings": [],
        "compliance_score": 1.0,
        "provenance": {
            "timestamp": datetime.now().isoformat(),
            "validator": "DockerValidator"
        }
    }


@pytest.fixture
def mock_validation_with_issues():
    """Mock validation response with issues."""
    return {
        "valid": False,
        "build_status": "failed",
        "lint_status": "failed",
        "lint_issues": [
            "Missing FROM instruction",
            "No USER specified (running as root)"
        ],
        "security_findings": [
            {
                "type": "Security",
                "category": "RootUser",
                "description": "Container runs as root",
                "severity": "High"
            }
        ],
        "compliance_score": 0.3,
        "provenance": {
            "timestamp": datetime.now().isoformat(),
            "validator": "DockerValidator"
        }
    }


# ============================================================================
# TESTS: DeployAgent Initialization
# ============================================================================

class TestDeployAgentInit:
    """Tests for DeployAgent initialization."""
    
    @pytest.mark.asyncio
    async def test_init_with_valid_repo(self, agent, temp_repo):
        """Test initializing agent with valid repo path."""
        assert agent.repo_path == temp_repo
        assert agent.run_id is not None
        assert isinstance(agent.plugin_registry, PluginRegistry)
        assert agent.db_path == str(temp_repo / "test_agent.db")
    
    @pytest.mark.asyncio
    async def test_init_creates_database(self, agent):
        """Test that initialization creates SQLite database."""
        db_path = Path(agent.db_path)
        assert db_path.exists()
        
        async with aiosqlite.connect(db_path) as db:
            async with db.execute("SELECT name FROM sqlite_master WHERE type='table'") as cursor:
                tables = [row[0] for row in await cursor.fetchall()]
                assert "history" in tables
    
    def test_init_with_nonexistent_repo(self):
        """Test initializing with non-existent repository."""
        with pytest.raises(ValueError, match="Repository path does not exist"):
            DeployAgent("/nonexistent/path")
    

# ============================================================================
# TESTS: Plugin System
# ============================================================================

class TestPluginSystem:
    """Tests for plugin registry and management."""
    
    def test_plugin_registry_register(self):
        """Test registering a plugin."""
        with patch.dict('os.environ', {'TESTING': '1'}):
            registry = PluginRegistry(plugin_dir="./test_plugins")
            
            class TestPlugin(TargetPlugin):
                async def generate_config(self, *args, **kwargs): return {"config": "test"}
                async def validate_config(self, *args, **kwargs): return {"status": "valid"}
                async def simulate_deployment(self, *args, **kwargs): return {"result": "success"}
                async def rollback(self, *args, **kwargs): return True
                def health_check(self): return True
            
            plugin = TestPlugin()
            registry.register("test", plugin)
            
            assert registry.get_plugin("test") == plugin
    
    def test_plugin_registry_get_unknown(self):
        """Test getting unknown plugin returns None."""
        with patch.dict('os.environ', {'TESTING': '1'}):
            registry = PluginRegistry(plugin_dir="./test_plugins")
            assert registry.get_plugin("unknown") is None
    
    @pytest.mark.asyncio
    async def test_custom_plugin_registration(self, agent):
        """Test registering and using custom plugin."""
        class CustomPlugin(TargetPlugin):
            async def generate_config(self, *args, **kwargs): return {"config": "custom content"}
            async def validate_config(self, *args, **kwargs): return {"status": "valid"}
            async def simulate_deployment(self, *args, **kwargs): return {"result": "simulated"}
            async def rollback(self, *args, **kwargs): return True
            def health_check(self): return True
        
        custom = CustomPlugin()
        agent.register_plugin("custom", custom)
        
        assert agent.plugin_registry.get_plugin("custom") == custom


# ============================================================================
# TESTS: Configuration Generation
# ============================================================================

class TestConfigurationGeneration:
    """Tests for configuration generation."""
    
    @pytest.mark.asyncio
    @patch('generator.agents.deploy_agent.deploy_agent.call_llm_api')
    @patch('generator.agents.deploy_agent.deploy_agent.handle_deploy_response')
    @patch('generator.agents.deploy_agent.deploy_agent.ValidatorRegistry')
    async def test_generate_documentation_docker(
        self, mock_validator_registry, mock_handler, mock_llm, agent,
        mock_llm_dockerfile_response, mock_validation_success
    ):
        """Test generating Docker configuration."""
        mock_llm.return_value = mock_llm_dockerfile_response
        mock_handler.return_value = {
            "final_config_output": mock_llm_dockerfile_response["content"],
            "structured_data": {"FROM": "python:3.9-slim"},
            "provenance": {
                "run_id": "test-123",
                "timestamp": datetime.now().isoformat()
            }
        }
        
        mock_validator = MagicMock()
        mock_validator.validate = AsyncMock(return_value=mock_validation_success)
        agent.validator_registry.get_validator = Mock(return_value=mock_validator)
        
        agent.validate_configs_final = AsyncMock(return_value=mock_validation_success)
        agent.compliance_check_final = AsyncMock(return_value=[])
        agent.simulate_deployment_final = AsyncMock(return_value={"status": "success"})
        agent.generate_explanation_final = AsyncMock(return_value="Explanation")
        
        # Patch the instance attribute 'prompt_agent' directly on the 'agent' fixture
        agent.prompt_agent = AsyncMock(return_value="Mocked Prompt")
        
        result = await agent.generate_documentation(
            target_files=["src/main.py", "requirements.txt"],
            targets=["docker"],
            doc_type="deployment",
            human_approval=False
        )
        
        assert "configs" in result
        assert "docker" in result["configs"]
        assert "FROM python:3.9-slim" in result["configs"]["docker"]
        assert mock_llm.called
        mock_handler.assert_called_with(
            raw_response=mock_llm_dockerfile_response['content'],
            handler_registry=agent.handler_registry,
            output_format='docker',
            to_format='docker',
            repo_path=str(agent.repo_path),
            run_id=agent.run_id
        )
    
    @pytest.mark.asyncio
    @patch('generator.agents.deploy_agent.deploy_agent.call_llm_api')
    @patch('generator.agents.deploy_agent.deploy_agent.handle_deploy_response')
    async def test_generate_multiple_targets(
        self, mock_handler, mock_llm, agent,
        mock_validation_success
    ):
        """Test generating configurations for multiple targets."""
        mock_llm.return_value = {
            "content": "Generated config",
            "model": "gpt-4",
            "provider": "openai"
        }
        mock_handler.return_value = {
            "final_config_output": "Generated config",
            "structured_data": {},
            "provenance": {"run_id": "test-123"}
        }
        
        mock_validator = MagicMock()
        mock_validator.validate = AsyncMock(return_value=mock_validation_success)
        agent.validator_registry.get_validator = Mock(return_value=mock_validator)
        
        agent.validate_configs_final = AsyncMock(return_value=mock_validation_success)
        agent.compliance_check_final = AsyncMock(return_value=[])
        agent.simulate_deployment_final = AsyncMock(return_value={"status": "success"})
        agent.generate_explanation_final = AsyncMock(return_value="Explanation")
        
        # Patch the instance attribute 'prompt_agent' directly on the 'agent' fixture
        agent.prompt_agent = AsyncMock(return_value="Mocked Prompt")
        
        result = await agent.generate_documentation(
            target_files=["src/main.py"],
            targets=["docker", "helm", "docs"],
            doc_type="deployment",
            human_approval=False
        )
        
        assert len(result["configs"]) == 3
        assert "docker" in result["configs"]
        assert "helm" in result["configs"]
        assert "docs" in result["configs"]
    
    @pytest.mark.asyncio
    @patch('generator.agents.deploy_agent.deploy_agent.call_llm_api')
    async def test_generate_with_llm_failure(self, mock_llm, agent):
        """Test handling LLM failure during generation."""
        mock_llm.side_effect = Exception("LLM API Error")
        
        # Patch the instance attribute 'prompt_agent' directly on the 'agent' fixture
        agent.prompt_agent = AsyncMock(return_value="Mocked Prompt")
        
        with patch.object(agent, 'self_heal', new=AsyncMock(return_value=None)):
            with pytest.raises(Exception, match="LLM call failed for target docker"):
                await agent.generate_documentation(
                    target_files=["src/main.py"],
                    targets=["docker"],
                    doc_type="deployment",
                    human_approval=False
                )


# ============================================================================
# TESTS: Rollback
# ============================================================================

class TestRollback:
    """Tests for rollback functionality."""
    
    @pytest.mark.asyncio
    async def test_rollback_to_previous_run(self, agent):
        """Test rolling back to previous run."""
        first_run = {
            "run_id": "run-001",
            "timestamp": datetime.now().isoformat(),
            # Configs must be JSON-dumped strings for json.loads() in rollback() to work
            "configs": {"docker": json.dumps({"config_key": "FROM python:3.8"})}
        }
        
        second_run = {
            "run_id": "run-002",
            "timestamp": datetime.now().isoformat(),
            "configs": {"docker": json.dumps({"config_key": "FROM python:3.9"})}
        }
        
        agent.history = [first_run, second_run]
        agent.last_result = second_run
        
        async with aiosqlite.connect(agent.db_path) as db:
            await db.execute(
                "INSERT INTO history (id, timestamp, result) VALUES (?, ?, ?)",
                (first_run["run_id"], first_run["timestamp"], json.dumps(first_run))
            )
            await db.commit()
        
        with patch.object(agent.plugin_registry, 'get_plugin') as mock_get_plugin:
            mock_plugin = MagicMock()
            mock_plugin.rollback = AsyncMock(return_value=True)
            mock_get_plugin.return_value = mock_plugin
            
            success = await agent.rollback("run-001")
            
            assert success
            assert mock_plugin.rollback.called
    
    @pytest.mark.asyncio
    async def test_rollback_nonexistent_run(self, agent):
        """Test rolling back to non-existent run."""
        success = await agent.rollback("nonexistent-run")
        assert not success
    
    @pytest.mark.asyncio
    async def test_get_previous_run(self, agent):
        """Test retrieving previous run from history."""
        run = {
            "run_id": "test-run",
            "timestamp": datetime.now().isoformat(),
            "configs": {"docker": "FROM python:3.9"}
        }
        
        async with aiosqlite.connect(agent.db_path) as db:
            await db.execute(
                "INSERT INTO history (id, timestamp, result) VALUES (?, ?, ?)",
                (run["run_id"], run["timestamp"], json.dumps(run))
            )
            await db.commit()
        
        retrieved = await agent.get_previous_run("test-run")
        
        assert retrieved is not None
        assert retrieved["run_id"] == "test-run"


# ============================================================================
# TESTS: Human Approval
# ============================================================================

class TestHumanApproval:
    """Tests for human approval workflow."""
    
    @pytest.mark.asyncio
    @patch('aiohttp.ClientSession')
    async def test_human_approval_webhook_approved(self, mock_session, agent):
        """Test human approval when approved via webhook."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "approved": True,
            "comments": "Looks good!"
        })
        
        mock_session_instance = MagicMock()
        mock_session_instance.post = AsyncMock(return_value=mock_response)
        mock_session.return_value.__aenter__.return_value = mock_session_instance
        
        agent.webhook_url = "http://test.hook"
        
        approved = await agent.request_human_approval({}, {})
        
        assert approved
    
    @pytest.mark.asyncio
    @patch('generator.agents.deploy_agent.deploy_agent.call_llm_api')
    @patch('generator.agents.deploy_agent.deploy_agent.handle_deploy_response')
    async def test_generation_with_approval_required_and_rejected(
        self, mock_handler, mock_llm, agent,
        mock_validation_success
    ):
        """Test generation when human approval is required and rejected."""
        mock_llm.return_value = {"content": "FROM python:3.9", "model": "gpt-4"}
        mock_handler.return_value = {
            "final_config_output": "FROM python:3.9",
            "structured_data": {},
            "provenance": {}
        }
        
        mock_validator = MagicMock()
        mock_validator.validate = AsyncMock(return_value=mock_validation_success)
        agent.validator_registry.get_validator = Mock(return_value=mock_validator)
        
        agent.validate_configs_final = AsyncMock(return_value=mock_validation_success)
        agent.compliance_check_final = AsyncMock(return_value=[])
        agent.simulate_deployment_final = AsyncMock(return_value={"status": "success"})
        agent.generate_explanation_final = AsyncMock(return_value="Explanation")
        
        # Patch the instance attribute 'prompt_agent' directly on the 'agent' fixture
        agent.prompt_agent = AsyncMock(return_value="Mocked Prompt")
        
        with patch.object(agent, 'request_human_approval', new=AsyncMock(return_value=False)):
            with pytest.raises(ValueError, match="Configuration rejected by human reviewer"):
                await agent.generate_documentation(
                    target_files=["src/main.py"],
                    targets=["docker"],
                    doc_type="deployment",
                    human_approval=True
                )


# ============================================================================
# TESTS: Report Generation
# ============================================================================

class TestReportGeneration:
    """Tests for report generation."""
    
    @pytest.mark.asyncio
    async def test_generate_report(self, agent):
        """Test generating deployment report."""
        result = {
            "run_id": "test-123",
            "timestamp": datetime.now().isoformat(),
            "configs": {
                "docker": "FROM python:3.9\nCMD python app.py"
            },
            "validations": {
                "docker": {
                    "build_status": "success",
                    "lint_status": "passed"
                }
            },
            "compliances": {
                "docker": []
            },
            "simulations": {
                "docker": {"status": "success"}
            },
            "explanations": {
                "docker": "This is a Python application container"
            },
            "badges": {},
            "provenance": {
                "timestamp": datetime.now().isoformat()
            }
        }
        
        report = await agent.generate_report(result)
        
        assert "Deployment Configuration Report" in report
        assert "test-123" in report
        assert "docker" in report
        assert "FROM python:3.9" in report
    
    @pytest.mark.asyncio
    async def test_generate_report_empty(self, agent):
        """Test generating report with empty result."""
        result = {
            "run_id": "empty",
            "timestamp": datetime.now().isoformat(),
            "configs": {},
            "provenance": {}
        }
        
        report = await agent.generate_report(result)
        
        assert "Deployment Configuration Report" in report
        assert "empty" in report


# ============================================================================
# TESTS: Security
# ============================================================================

class TestSecurity:
    """Tests for security features."""
    
    @patch('generator.agents.deploy_agent.deploy_agent.redact_secrets', return_value="My API key is: [REDACTED]")
    def test_scrub_text_api_key(self, mock_redact):
        """Test scrubbing API keys from text."""
        text = "My API key is: sk-1234567890abcdef1234567890abcdef"
        scrubbed = scrub_text(text)
        
        assert "sk-1234567890abcdef1234567890abcdef" not in scrubbed
        assert "[REDACTED]" in scrubbed
        mock_redact.assert_called_with(text)

    @patch('generator.agents.deploy_agent.deploy_agent.redact_secrets', return_value="password=[REDACTED]")
    def test_scrub_text_password(self, mock_redact):
        """Test scrubbing passwords from text."""
        text = "password=mysecretpassword123"
        scrubbed = scrub_text(text)
        
        assert "mysecretpassword123" not in scrubbed
        mock_redact.assert_called_with(text)


# ============================================================================
# TESTS: Metrics and Observability
# ============================================================================

class TestMetricsObservability:
    """Tests for metrics and observability."""
    
    @pytest.mark.asyncio
    @patch('generator.agents.deploy_agent.deploy_agent.call_llm_api')
    @patch('generator.agents.deploy_agent.deploy_agent.handle_deploy_response')
    @patch('generator.agents.deploy_agent.deploy_agent.SUCCESSFUL_GENERATIONS')
    async def test_metrics_successful_generation(
        self, mock_metric, mock_handler, mock_llm, 
        agent, mock_validation_success
    ):
        """Test that metrics are recorded on successful generation."""
        mock_llm.return_value = {"content": "FROM python:3.9", "model": "gpt-4"}
        mock_handler.return_value = {
            "final_config_output": "FROM python:3.9",
            "structured_data": {},
            "provenance": {}
        }
        
        mock_validator = MagicMock()
        mock_validator.validate = AsyncMock(return_value=mock_validation_success)
        agent.validator_registry.get_validator = Mock(return_value=mock_validator)
        
        agent.validate_configs_final = AsyncMock(return_value=mock_validation_success)
        agent.compliance_check_final = AsyncMock(return_value=[])
        agent.simulate_deployment_final = AsyncMock(return_value={"status": "success"})
        agent.generate_explanation_final = AsyncMock(return_value="Explanation")
        
        # Patch the instance attribute 'prompt_agent' directly on the 'agent' fixture
        agent.prompt_agent = AsyncMock(return_value="Mocked Prompt")
        
        await agent.generate_documentation(
            target_files=["src/main.py"],
            targets=["docker"],
            doc_type="deployment",
            human_approval=False
        )
        
        mock_metric.labels.assert_called_with(run_type="deployment")


# ============================================================================
# TESTS: Edge Cases
# ============================================================================

class TestEdgeCases:
    """Tests for edge cases and error conditions."""
    
    @pytest.mark.asyncio
    @patch('generator.agents.deploy_agent.deploy_agent.call_llm_api')
    @patch('generator.agents.deploy_agent.deploy_agent.handle_deploy_response')
    async def test_generate_with_no_files(
        self, mock_handler, mock_llm, 
        agent, mock_validation_success
    ):
        """Test generation with no target files."""
        mock_llm.return_value = {"content": "FROM scratch", "model": "gpt-4"}
        mock_handler.return_value = {
            "final_config_output": "FROM scratch",
            "structured_data": {},
            "provenance": {}
        }
        
        mock_validator = MagicMock()
        mock_validator.validate = AsyncMock(return_value=mock_validation_success)
        agent.validator_registry.get_validator = Mock(return_value=mock_validator)
        
        agent.validate_configs_final = AsyncMock(return_value=mock_validation_success)
        agent.compliance_check_final = AsyncMock(return_value=[])
        agent.simulate_deployment_final = AsyncMock(return_value={"status": "success"})
        agent.generate_explanation_final = AsyncMock(return_value="Explanation")
        
        # Patch the instance attribute 'prompt_agent' directly on the 'agent' fixture
        agent.prompt_agent = AsyncMock(return_value="Mocked Prompt")
        
        result = await agent.generate_documentation(
            target_files=[],
            targets=["docker"],
            doc_type="deployment",
            human_approval=False
        )
        
        assert "configs" in result
    
    @pytest.mark.asyncio
    async def test_supported_languages(self, agent):
        """Test getting supported languages."""
        languages = agent.supported_languages()
        
        assert isinstance(languages, list)
        assert "python" in languages
    
    @pytest.mark.asyncio
    @patch('generator.agents.deploy_agent.deploy_agent.call_llm_api')
    @patch('generator.agents.deploy_agent.deploy_agent.handle_deploy_response')
    async def test_concurrent_generations(
        self, mock_handler, mock_llm, 
        agent, mock_validation_success, temp_repo # FIX: Add temp_repo fixture
    ):
        """Test handling concurrent generation requests."""
        
        # FIX: Define a helper function to create a new, fully-mocked agent
        # for each concurrent task. This ensures each run has a unique run_id.
        async def create_and_run_agent():
            with patch.dict('os.environ', {'TESTING': '1'}):
                # Each task gets a new agent, so it gets a new run_id
                new_agent = DeployAgent(str(temp_repo))
                # They can all share the same test database file
                new_agent.db_path = agent.db_path
                # Must await async init
                await new_agent._init_db()

            # Mock all dependencies for the new agent instance
            mock_llm.return_value = {"content": "config", "model": "gpt-4"}
            mock_handler.return_value = {
                "final_config_output": "config",
                "structured_data": {},
                "provenance": {}
            }
            mock_validator = MagicMock()
            mock_validator.validate = AsyncMock(return_value=mock_validation_success)
            new_agent.validator_registry.get_validator = Mock(return_value=mock_validator)
            
            new_agent.validate_configs_final = AsyncMock(return_value=mock_validation_success)
            new_agent.compliance_check_final = AsyncMock(return_value=[])
            new_agent.simulate_deployment_final = AsyncMock(return_value={"status": "success"})
            new_agent.generate_explanation_final = AsyncMock(return_value="Explanation")
            new_agent.prompt_agent = AsyncMock(return_value="Mocked Prompt")

            # Run the generation
            return await new_agent.generate_documentation(
                target_files=["src/main.py"],
                targets=["docker"],
                doc_type="deployment",
                human_approval=False
            )
        
        # Start multiple generations concurrently
        tasks = [ create_and_run_agent() for _ in range(3) ]
        
        results = await asyncio.gather(*tasks)
        
        # Verify all 3 tasks completed
        assert len(results) == 3
        assert all("configs" in r for r in results)

        # Verify 3 unique run IDs were created
        run_ids = [r['run_id'] for r in results]
        assert len(set(run_ids)) == 3
        
        # Verify all 3 unique runs were successfully written to the database
        async with aiosqlite.connect(agent.db_path) as db:
            async with db.execute("SELECT id FROM history") as cursor:
                rows = await cursor.fetchall()
                # Note: This checks count in the DB. If tests run in parallel
                # this might be flaky, but for this file it should be fine.
                # We check that our 3 unique IDs are present.
                db_ids = set(r[0] for r in rows)
                assert set(run_ids).issubset(db_ids)


# ============================================================================
# RUN TESTS
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])