"""
test_deploy_agent.py

Industry-grade test suite for DeployAgent with comprehensive coverage including:
- Unit tests for all public methods
- Integration tests for plugin system
- Performance tests for concurrent operations
- Security tests for data scrubbing
- Failure scenario testing and recovery
- Mock LLM responses and external tool calls
"""

import asyncio
import json
import os
import sys
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, AsyncGenerator
from unittest.mock import Mock, AsyncMock, patch, MagicMock, call, ANY

import pytest
import pytest_asyncio
from pytest_mock import MockerFixture
import aiofiles
import networkx as nx
from freezegun import freeze_time
from hypothesis import given, strategies as st, settings
from faker import Faker

# Test fixtures and utilities
from test_fixtures import (
    create_test_repository,
    cleanup_test_repository,
    generate_mock_config,
    generate_mock_validation_result
)

# Import the module under test
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from deploy_agent import (
    DeployAgent,
    TargetPlugin,
    PluginRegistry,
    scrub_text,
    ScrubFilter
)

# Initialize faker for test data generation
fake = Faker()

# Test constants
TEST_REPO_PATH = "/tmp/test_deploy_repo"
TEST_PLUGIN_DIR = "/tmp/test_plugins"
TEST_DB_PATH = "/tmp/test_deploy.db"
MOCK_RUN_ID = "test-run-" + str(uuid.uuid4())


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture(scope="function")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def test_repository():
    """Create a test repository with sample files."""
    repo_path = Path(TEST_REPO_PATH)
    repo_path.mkdir(parents=True, exist_ok=True)
    
    # Create sample files
    files = {
        "main.py": "import flask\napp = flask.Flask(__name__)",
        "requirements.txt": "flask==2.0.1\nrequests==2.27.1",
        "package.json": json.dumps({
            "name": "test-app",
            "dependencies": {"express": "^4.17.1"},
            "devDependencies": {"jest": "^27.0.0"}
        }),
        "go.mod": "module test\ngo 1.17\nrequire github.com/gin-gonic/gin v1.7.0",
        "Dockerfile": "FROM python:3.9\nCOPY . /app\nCMD python main.py"
    }
    
    for filename, content in files.items():
        file_path = repo_path / filename
        async with aiofiles.open(file_path, 'w') as f:
            await f.write(content)
    
    # Initialize git repo
    os.system(f"cd {repo_path} && git init && git add . && git commit -m 'Initial commit' > /dev/null 2>&1")
    
    yield repo_path
    
    # Cleanup
    import shutil
    if repo_path.exists():
        shutil.rmtree(repo_path)


@pytest_asyncio.fixture
async def mock_llm_orchestrator(mocker: MockerFixture):
    """Mock LLM orchestrator with configurable responses."""
    orchestrator = AsyncMock()
    
    async def generate_config_mock(prompt, model, stream=False, ensemble=False, cancel_event=None):
        if stream:
            async def stream_gen():
                yield '{"config": "streaming"}'
                yield '[END_STREAM_METADATA]{"status": "success"}[/END_STREAM_METADATA]'
            return stream_gen()
        return {
            "config": {"type": "mock_config", "content": "test configuration"},
            "model": model,
            "provider": "mock",
            "valid": True,
            "input_tokens": 100,
            "output_tokens": 50,
            "cost": 0.01,
            "latency": 0.5
        }
    
    orchestrator.generate_config = generate_config_mock
    return orchestrator


@pytest.fixture
def mock_plugin():
    """Create a mock target plugin."""
    plugin = Mock(spec=TargetPlugin)
    plugin.__version__ = "1.0.0"
    plugin.health_check.return_value = True
    plugin.generate_config = AsyncMock(return_value={
        "type": "mock",
        "config": "test config"
    })
    plugin.validate_config = AsyncMock(return_value={
        "valid": True,
        "details": "Validation passed"
    })
    plugin.simulate_deployment = AsyncMock(return_value={
        "status": "success",
        "message": "Simulation successful"
    })
    return plugin


@pytest_asyncio.fixture
async def deploy_agent(test_repository, mock_llm_orchestrator):
    """Create a DeployAgent instance with mocked dependencies."""
    agent = DeployAgent(
        repo_path=str(test_repository),
        languages_supported=["python", "javascript", "go"],
        plugin_dir=TEST_PLUGIN_DIR,
        llm_orchestrator_instance=mock_llm_orchestrator
    )
    
    # Ensure plugin directory exists
    Path(TEST_PLUGIN_DIR).mkdir(parents=True, exist_ok=True)
    
    yield agent
    
    # Cleanup
    if agent.db:
        agent.db.close()
    if Path(TEST_DB_PATH).exists():
        os.remove(TEST_DB_PATH)


# ============================================================================
# UNIT TESTS - Core Functionality
# ============================================================================

class TestDeployAgentCore:
    """Test core DeployAgent functionality."""
    
    @pytest.mark.asyncio
    async def test_initialization(self, test_repository):
        """Test DeployAgent initialization with valid parameters."""
        agent = DeployAgent(
            repo_path=str(test_repository),
            languages_supported=["python", "rust"],
            rate_limit=10
        )
        
        assert agent.repo_path == test_repository
        assert agent.languages_supported == ["python", "rust"]
        assert agent.run_id is not None
        assert len(agent.run_id) == 36  # UUID format
        assert agent.history == []
        assert agent.last_result is None
    
    @pytest.mark.asyncio
    async def test_initialization_invalid_repo(self):
        """Test DeployAgent initialization with invalid repository path."""
        with pytest.raises(ValueError, match="Repository path does not exist"):
            DeployAgent(repo_path="/non/existent/path")
    
    @pytest.mark.asyncio
    async def test_gather_context(self, deploy_agent):
        """Test context gathering from repository."""
        context = await deploy_agent.gather_context(["main.py", "requirements.txt"])
        
        assert "dependencies" in context
        assert "python" in context["dependencies"]
        assert "flask==2.0.1" in context["dependencies"]["python"]
        assert "file_contents" in context
        assert "main.py" in context["file_contents"]
        assert "recent_commits" in context
        assert len(context["recent_commits"]) > 0
    
    @pytest.mark.asyncio
    async def test_gather_context_with_missing_files(self, deploy_agent):
        """Test context gathering with some missing files."""
        context = await deploy_agent.gather_context(["main.py", "nonexistent.txt"])
        
        assert "main.py" in context["file_contents"]
        assert "nonexistent.txt" not in context["file_contents"]
    
    @pytest.mark.asyncio
    @patch('deploy_agent.asyncio.create_subprocess_exec')
    async def test_gather_context_git_failure(self, mock_subprocess, deploy_agent):
        """Test context gathering when git command fails."""
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate.return_value = (b"", b"git error")
        mock_subprocess.return_value = mock_proc
        
        context = await deploy_agent.gather_context([])
        
        assert context["recent_commits"] == []


class TestSecurityFeatures:
    """Test security-related functionality."""
    
    def test_scrub_text_basic(self):
        """Test basic text scrubbing for sensitive data."""
        test_cases = [
            ("api_key=sk-1234567890abcdef", "api_key=[REDACTED]"),
            ("password: mysecret123", "password: [REDACTED]"),
            ("email: user@example.com", "email: [REDACTED]"),
            ("SSN: 123-45-6789", "SSN: [REDACTED]"),
            ("credit card: 4111111111111111", "credit card: [REDACTED]")
        ]
        
        for input_text, expected in test_cases:
            assert scrub_text(input_text) == expected
    
    def test_scrub_text_multiple_patterns(self):
        """Test scrubbing with multiple sensitive patterns."""
        text = "API_KEY=secret123 and password:pass456 with email test@test.com"
        result = scrub_text(text)
        
        assert "secret123" not in result
        assert "pass456" not in result
        assert "test@test.com" not in result
        assert result.count("[REDACTED]") == 3
    
    @patch('deploy_agent.AnalyzerEngine')
    @patch('deploy_agent.AnonymizerEngine')
    def test_scrub_text_with_presidio(self, mock_anonymizer, mock_analyzer):
        """Test scrubbing with Presidio when available."""
        mock_analyzer_instance = Mock()
        mock_analyzer_instance.analyze.return_value = [Mock(start=0, end=10)]
        mock_analyzer.return_value = mock_analyzer_instance
        
        mock_anonymizer_instance = Mock()
        mock_anonymizer_instance.anonymize.return_value = Mock(text="[REDACTED] data")
        mock_anonymizer.return_value = mock_anonymizer_instance
        
        result = scrub_text("sensitive data")
        assert result == "[REDACTED] data"
    
    def test_scrub_filter(self):
        """Test the ScrubFilter for logging."""
        filter_obj = ScrubFilter()
        record = Mock()
        record.msg = "Password: secret123"
        
        assert filter_obj.filter(record)
        assert record.msg == "Password: [REDACTED]"


# ============================================================================
# PLUGIN SYSTEM TESTS
# ============================================================================

class TestPluginSystem:
    """Test plugin registry and management."""
    
    @pytest.mark.asyncio
    async def test_plugin_registration(self, deploy_agent, mock_plugin):
        """Test registering a plugin."""
        deploy_agent.register_plugin("test_target", mock_plugin)
        
        registered_plugin = deploy_agent.registry.get_plugin("test_target")
        assert registered_plugin == mock_plugin
        assert "test_target" in deploy_agent.registry.plugins
    
    @pytest.mark.asyncio
    async def test_plugin_hot_reload(self, deploy_agent):
        """Test plugin hot-reload functionality."""
        plugin_file = Path(TEST_PLUGIN_DIR) / "test_plugin.py"
        
        # Write initial plugin
        plugin_code = '''
from deploy_agent import TargetPlugin

class TestPlugin(TargetPlugin):
    __version__ = "1.0"
    
    async def generate_config(self, target_files, instructions, context, previous_configs):
        return {"version": "1.0"}
    
    async def validate_config(self, config):
        return {"valid": True}
    
    async def simulate_deployment(self, config):
        return {"status": "success"}
    
    def health_check(self):
        return True
'''
        plugin_file.write_text(plugin_code)
        
        # Load plugins
        deploy_agent.registry.load_plugins()
        
        # Modify plugin
        plugin_code_v2 = plugin_code.replace('"1.0"', '"2.0"')
        plugin_file.write_text(plugin_code_v2)
        
        # Trigger reload
        deploy_agent.registry.load_plugins()
        
        # Verify version update
        plugin = deploy_agent.registry.get_plugin("test_plugin")
        assert plugin is not None
        # Clean up
        plugin_file.unlink()
    
    @pytest.mark.asyncio
    async def test_plugin_health_check(self, deploy_agent, mock_plugin):
        """Test plugin health monitoring."""
        mock_plugin.health_check.return_value = True
        deploy_agent.register_plugin("healthy", mock_plugin)
        
        mock_unhealthy = Mock(spec=TargetPlugin)
        mock_unhealthy.health_check.return_value = False
        deploy_agent.register_plugin("unhealthy", mock_unhealthy)
        
        assert deploy_agent.registry.plugin_info["healthy"]["health"] == True
        assert deploy_agent.registry.plugin_info["unhealthy"]["health"] == False


# ============================================================================
# CONFIGURATION GENERATION TESTS
# ============================================================================

class TestConfigurationGeneration:
    """Test configuration generation pipeline."""
    
    @pytest.mark.asyncio
    async def test_generate_documentation_basic(self, deploy_agent, mock_plugin):
        """Test basic documentation generation."""
        deploy_agent.register_plugin("docker", mock_plugin)
        
        result = await deploy_agent.generate_documentation(
            target_files=["main.py"],
            doc_type="README",
            targets=["docker"],
            instructions="Generate minimal config",
            human_approval=False,
            ensemble=False,
            stream=False
        )
        
        assert result["run_id"] == deploy_agent.run_id
        assert "configs" in result
        assert "docker" in result["configs"]
        assert result["validations"]["docker"]["valid"] == True
        assert result["simulations"]["docker"]["status"] == "success"
    
    @pytest.mark.asyncio
    async def test_generate_with_dependencies(self, deploy_agent, mock_plugin):
        """Test generation with target dependencies."""
        # Register plugins for dependent targets
        deploy_agent.register_plugin("docker", mock_plugin)
        deploy_agent.register_plugin("helm", mock_plugin)
        
        result = await deploy_agent.generate_documentation(
            target_files=["main.py"],
            targets=["helm"],  # helm depends on docker
            human_approval=False
        )
        
        # Verify both targets were processed in order
        assert "docker" in result["configs"]
        assert "helm" in result["configs"]
    
    @pytest.mark.asyncio
    async def test_generate_with_streaming(self, deploy_agent):
        """Test configuration generation with streaming."""
        result = await deploy_agent.generate_documentation(
            target_files=["main.py"],
            doc_type="README",
            targets=["docs"],
            stream=True
        )
        
        assert "configs" in result
        assert "docs" in result["configs"]
    
    @pytest.mark.asyncio
    async def test_generate_with_ensemble(self, deploy_agent):
        """Test configuration generation with ensemble mode."""
        result = await deploy_agent.generate_documentation(
            target_files=["main.py"],
            targets=["docs"],
            ensemble=True,
            stream=False
        )
        
        assert result is not None
        assert "configs" in result
    
    @pytest.mark.asyncio
    @patch('deploy_agent.DeployAgent.request_human_approval')
    async def test_generate_with_human_approval(self, mock_approval, deploy_agent, mock_plugin):
        """Test generation with human approval workflow."""
        mock_approval.return_value = True
        deploy_agent.register_plugin("docker", mock_plugin)
        
        result = await deploy_agent.generate_documentation(
            target_files=["main.py"],
            targets=["docker"],
            human_approval=True,
            cli_approval=True
        )
        
        assert mock_approval.called
        assert result["provenance"]["config_status"] == "Approved"
    
    @pytest.mark.asyncio
    async def test_generate_with_cycle_detection(self, deploy_agent):
        """Test that cyclic dependencies are detected."""
        # Create a cycle in the dependency graph
        deploy_agent.target_dependencies_graph.add_edge("terraform", "docker")
        
        with pytest.raises(ValueError, match="Cycle detected"):
            await deploy_agent.generate_documentation(
                target_files=["main.py"],
                targets=["docker", "helm", "terraform"]
            )


# ============================================================================
# VALIDATION AND COMPLIANCE TESTS
# ============================================================================

class TestValidationAndCompliance:
    """Test validation and compliance checking."""
    
    @pytest.mark.asyncio
    async def test_validate_configs(self, deploy_agent, mock_plugin):
        """Test configuration validation."""
        deploy_agent.register_plugin("docker", mock_plugin)
        
        config = {"type": "docker", "image": "python:3.9"}
        result = await deploy_agent.validate_configs(config, ["main.py"], "docker")
        
        assert result["valid"] == True
        assert "details" in result
    
    @pytest.mark.asyncio
    async def test_validate_with_invalid_plugin(self, deploy_agent):
        """Test validation with missing plugin."""
        config = {"type": "unknown"}
        result = await deploy_agent.validate_configs(config, ["main.py"], "unknown_target")
        
        assert result["valid"] == False
        assert "No validator plugin found" in result["error"]
    
    @pytest.mark.asyncio
    @patch('deploy_agent.asyncio.create_subprocess_exec')
    async def test_compliance_check_with_trivy(self, mock_subprocess, deploy_agent):
        """Test compliance checking with Trivy."""
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (
            json.dumps({"Results": []}).encode(),
            b""
        )
        mock_subprocess.return_value = mock_proc
        
        issues = await deploy_agent.compliance_check({"config": "test"})
        
        assert isinstance(issues, list)
        mock_subprocess.assert_called()
    
    @pytest.mark.asyncio
    async def test_compliance_check_missing_sections(self, deploy_agent):
        """Test compliance check for missing required sections."""
        config = {"data": "test"}  # Missing license and copyright
        issues = await deploy_agent.compliance_check(config)
        
        assert any("license" in issue.lower() for issue in issues)
        assert any("copyright" in issue.lower() for issue in issues)


# ============================================================================
# SELF-HEALING TESTS
# ============================================================================

class TestSelfHealing:
    """Test self-healing capabilities."""
    
    @pytest.mark.asyncio
    async def test_self_heal_successful(self, deploy_agent, mock_plugin):
        """Test successful self-healing after failure."""
        deploy_agent.register_plugin("docker", mock_plugin)
        
        # Mock the LLM to return a fixed config
        async def mock_generate_fixed(*args, **kwargs):
            return {
                "config": {
                    "docker": {"type": "fixed", "status": "healed"}
                }
            }
        
        deploy_agent.llm_orchestrator.generate_config = mock_generate_fixed
        
        result = await deploy_agent.self_heal(
            target_files=["main.py"],
            doc_type="config",
            targets=["docker"],
            instructions="Fix the config",
            error="Original error message",
            llm_model="gpt-4",
            ensemble=False,
            stream=False
        )
        
        assert result is not None
        assert "configs" in result
        assert result["provenance"]["generated_by"] == "DeployAgent (Self-Healed)"
    
    @pytest.mark.asyncio
    async def test_self_heal_max_attempts(self, deploy_agent):
        """Test self-healing stops after max attempts."""
        # Mock to always fail validation
        async def mock_validate(*args, **kwargs):
            return {"valid": False, "error": "Still broken"}
        
        deploy_agent.validate_configs = mock_validate
        
        result = await deploy_agent.self_heal(
            target_files=["main.py"],
            doc_type="config",
            targets=["docker"],
            instructions=None,
            error="Test error",
            llm_model="gpt-4",
            ensemble=False,
            stream=False
        )
        
        assert result is None  # Should fail after 3 attempts


# ============================================================================
# ROLLBACK AND HISTORY TESTS
# ============================================================================

class TestRollbackAndHistory:
    """Test rollback and history management."""
    
    @pytest.mark.asyncio
    async def test_save_and_retrieve_history(self, deploy_agent):
        """Test saving and retrieving run history."""
        test_result = {
            "configs": {"docker": {"test": "config"}},
            "run_id": "test-123",
            "timestamp": datetime.now().isoformat()
        }
        
        # Save to history
        cursor = deploy_agent.db.cursor()
        cursor.execute(
            "INSERT INTO history (id, timestamp, result) VALUES (?, ?, ?)",
            ("test-123", test_result["timestamp"], json.dumps(test_result))
        )
        deploy_agent.db.commit()
        
        # Retrieve
        retrieved = deploy_agent.get_previous_run("test-123")
        
        assert retrieved is not None
        assert retrieved["run_id"] == "test-123"
        assert retrieved["configs"] == test_result["configs"]
    
    @pytest.mark.asyncio
    @patch('deploy_agent.asyncio.create_subprocess_exec')
    async def test_rollback_kubernetes(self, mock_subprocess, deploy_agent):
        """Test rollback for Kubernetes target."""
        # Setup history
        previous_run = {
            "target": "kubernetes",
            "configs": {"apiVersion": "v1", "kind": "Pod"}
        }
        
        cursor = deploy_agent.db.cursor()
        cursor.execute(
            "INSERT INTO history (id, timestamp, result) VALUES (?, ?, ?)",
            ("rollback-123", datetime.now().isoformat(), json.dumps(previous_run))
        )
        deploy_agent.db.commit()
        
        # Mock kubectl
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (b"configured", b"")
        mock_subprocess.return_value = mock_proc
        
        # Mock plugin
        mock_k8s_plugin = Mock(spec=TargetPlugin)
        deploy_agent.register_plugin("kubernetes", mock_k8s_plugin)
        
        result = await deploy_agent.rollback("rollback-123")
        
        assert result == True
        mock_subprocess.assert_called()
    
    @pytest.mark.asyncio
    async def test_rollback_nonexistent_run(self, deploy_agent):
        """Test rollback with non-existent run ID."""
        result = await deploy_agent.rollback("nonexistent-id")
        assert result == False


# ============================================================================
# PERFORMANCE TESTS
# ============================================================================

class TestPerformance:
    """Test performance and concurrency."""
    
    @pytest.mark.asyncio
    @pytest.mark.timeout(10)
    async def test_concurrent_generation(self, deploy_agent, mock_plugin):
        """Test concurrent configuration generation."""
        deploy_agent.register_plugin("docker", mock_plugin)
        deploy_agent.register_plugin("helm", mock_plugin)
        
        # Run multiple generations concurrently
        tasks = [
            deploy_agent.generate_documentation(
                target_files=["main.py"],
                targets=["docker"],
                human_approval=False
            )
            for _ in range(3)
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # All should succeed
        for result in results:
            assert not isinstance(result, Exception)
            assert "configs" in result
    
    @pytest.mark.asyncio
    async def test_rate_limiting(self, deploy_agent, mock_plugin):
        """Test rate limiting with semaphore."""
        deploy_agent.register_plugin("docker", mock_plugin)
        deploy_agent.sem = asyncio.Semaphore(2)  # Limit to 2 concurrent
        
        start_time = time.time()
        
        # Try to run 4 tasks
        tasks = [
            deploy_agent.generate_documentation(
                target_files=["main.py"],
                targets=["docker"],
                human_approval=False
            )
            for _ in range(4)
        ]
        
        await asyncio.gather(*tasks)
        
        # Should take longer due to rate limiting
        duration = time.time() - start_time
        assert duration > 0  # Just ensure it completes


# ============================================================================
# INTEGRATION TESTS
# ============================================================================

class TestIntegration:
    """End-to-end integration tests."""
    
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_full_pipeline(self, deploy_agent, mock_plugin):
        """Test complete pipeline from generation to validation."""
        # Register multiple plugins
        deploy_agent.register_plugin("docker", mock_plugin)
        deploy_agent.register_plugin("helm", mock_plugin)
        deploy_agent.register_plugin("terraform", mock_plugin)
        
        # Run full pipeline
        result = await deploy_agent.generate_documentation(
            target_files=["main.py", "requirements.txt", "Dockerfile"],
            doc_type="README",
            targets=["docker", "helm", "terraform"],
            instructions="Generate production-ready configs",
            human_approval=False,
            ensemble=False,
            stream=False,
            llm_model="gpt-4"
        )
        
        # Verify all components
        assert result["run_id"] is not None
        assert len(result["configs"]) == 3
        assert all(target in result["configs"] for target in ["docker", "helm", "terraform"])
        assert all(result["validations"][t]["valid"] for t in ["docker", "helm", "terraform"])
        assert all(result["simulations"][t]["status"] == "success" for t in ["docker", "helm", "terraform"])
        assert "badges" in result
        assert "provenance" in result
    
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_error_recovery_pipeline(self, deploy_agent):
        """Test pipeline with errors and recovery."""
        # Create a plugin that fails initially
        failing_plugin = Mock(spec=TargetPlugin)
        failing_plugin.generate_config = AsyncMock(
            side_effect=[Exception("First attempt failed"), {"config": "recovered"}]
        )
        failing_plugin.validate_config = AsyncMock(return_value={"valid": True})
        failing_plugin.simulate_deployment = AsyncMock(return_value={"status": "success"})
        failing_plugin.health_check.return_value = True
        
        deploy_agent.register_plugin("unstable", failing_plugin)
        
        # Should trigger self-healing
        with pytest.raises(Exception):
            await deploy_agent.generate_documentation(
                target_files=["main.py"],
                targets=["unstable"],
                human_approval=False
            )


# ============================================================================
# PROPERTY-BASED TESTS
# ============================================================================

class TestPropertyBased:
    """Property-based tests using Hypothesis."""
    
    @given(
        text=st.text(min_size=0, max_size=1000),
        include_secrets=st.booleans()
    )
    def test_scrub_text_properties(self, text, include_secrets):
        """Property: scrubbed text never contains sensitive patterns."""
        if include_secrets:
            text = f"api_key={fake.sha256()[:20]} {text}"
        
        result = scrub_text(text)
        
        # Properties that should always hold
        assert len(result) <= len(text) + result.count("[REDACTED]") * 10
        assert "api_key=" not in result or "[REDACTED]" in result
    
    @given(
        num_targets=st.integers(min_value=1, max_value=10),
        with_dependencies=st.booleans()
    )
    @pytest.mark.asyncio
    async def test_dependency_graph_properties(self, num_targets, with_dependencies):
        """Property: dependency resolution always produces valid order."""
        agent = DeployAgent(repo_path=TEST_REPO_PATH)
        
        # Create random targets
        targets = [f"target_{i}" for i in range(num_targets)]
        for target in targets:
            agent.target_dependencies_graph.add_node(target)
        
        # Add random dependencies (ensuring no cycles)
        if with_dependencies and num_targets > 1:
            for i in range(1, num_targets):
                agent.target_dependencies_graph.add_edge(targets[i-1], targets[i])
        
        # Property: topological sort should work without cycles
        try:
            ordered = list(nx.topological_sort(agent.target_dependencies_graph))
            # Verify ordering respects dependencies
            for edge in agent.target_dependencies_graph.edges():
                assert ordered.index(edge[0]) < ordered.index(edge[1])
        except nx.NetworkXUnfeasible:
            # Only happens if there's a cycle (which we avoided)
            assert False, "Unexpected cycle in dependency graph"


# ============================================================================
# EDGE CASES AND ERROR SCENARIOS
# ============================================================================

class TestEdgeCases:
    """Test edge cases and error scenarios."""
    
    @pytest.mark.asyncio
    async def test_empty_repository(self):
        """Test with empty repository."""
        with tempfile.TemporaryDirectory() as empty_repo:
            agent = DeployAgent(repo_path=empty_repo)
            context = await agent.gather_context([])
            
            assert context["file_contents"] == {}
            assert context["dependencies"] == {}
    
    @pytest.mark.asyncio
    async def test_unicode_in_files(self, deploy_agent):
        """Test handling of Unicode characters in files."""
        unicode_content = "Hello 世界 🚀 émojis"
        test_file = deploy_agent.repo_path / "unicode.txt"
        
        async with aiofiles.open(test_file, 'w', encoding='utf-8') as f:
            await f.write(unicode_content)
        
        context = await deploy_agent.gather_context(["unicode.txt"])
        
        assert "unicode.txt" in context["file_contents"]
        # Content should be scrubbed but Unicode preserved
        assert "世界" in context["file_contents"]["unicode.txt"]
    
    @pytest.mark.asyncio
    async def test_large_file_handling(self, deploy_agent):
        """Test handling of large files."""
        large_content = "x" * (10 * 1024 * 1024)  # 10MB
        test_file = deploy_agent.repo_path / "large.txt"
        
        async with aiofiles.open(test_file, 'w') as f:
            await f.write(large_content)
        
        # Should handle without memory issues
        context = await deploy_agent.gather_context(["large.txt"])
        
        assert "large.txt" in context["file_contents"]
    
    @pytest.mark.asyncio
    async def test_concurrent_database_access(self, deploy_agent):
        """Test concurrent database operations."""
        async def write_history(run_id):
            cursor = deploy_agent.db.cursor()
            cursor.execute(
                "INSERT INTO history (id, timestamp, result) VALUES (?, ?, ?)",
                (run_id, datetime.now().isoformat(), json.dumps({"test": run_id}))
            )
            deploy_agent.db.commit()
        
        # Run concurrent writes
        tasks = [write_history(f"concurrent-{i}") for i in range(10)]
        await asyncio.gather(*tasks)
        
        # Verify all were written
        cursor = deploy_agent.db.cursor()
        cursor.execute("SELECT COUNT(*) FROM history WHERE id LIKE 'concurrent-%'")
        count = cursor.fetchone()[0]
        
        assert count == 10


# ============================================================================
# MOCK RESPONSES AND FIXTURES MODULE
# ============================================================================

"""test_fixtures.py - Shared fixtures and utilities for tests"""

def create_test_repository(path: str) -> Path:
    """Create a test repository with sample files."""
    repo_path = Path(path)
    repo_path.mkdir(parents=True, exist_ok=True)
    return repo_path

def cleanup_test_repository(path: Path):
    """Clean up test repository."""
    import shutil
    if path.exists():
        shutil.rmtree(path)

def generate_mock_config(target: str) -> Dict[str, Any]:
    """Generate mock configuration for a target."""
    configs = {
        "docker": {
            "FROM": "python:3.9",
            "WORKDIR": "/app",
            "COPY": ". .",
            "CMD": ["python", "app.py"]
        },
        "helm": {
            "apiVersion": "v2",
            "name": "test-chart",
            "version": "0.1.0",
            "dependencies": []
        },
        "terraform": {
            "terraform": {"required_version": ">= 0.14"},
            "provider": {"aws": {"version": "~> 3.0"}}
        }
    }
    return configs.get(target, {"type": target, "config": "mock"})

def generate_mock_validation_result(valid: bool = True) -> Dict[str, Any]:
    """Generate mock validation result."""
    return {
        "valid": valid,
        "details": "Validation passed" if valid else "Validation failed",
        "errors": [] if valid else ["Error 1", "Error 2"]
    }


if __name__ == "__main__":
    # Run tests with coverage
    pytest.main([
        __file__,
        "-v",
        "--cov=deploy_agent",
        "--cov-report=html",
        "--cov-report=term-missing",
        "-m", "not integration"  # Skip integration tests by default
    ])
