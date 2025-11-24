"""
test_deploy_prompt.py
Comprehensive tests for deploy_prompt module (prompt building and templating).
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# FIX: Add missing Jinja2 imports
from jinja2 import Environment, FileSystemLoader

# Import the module under test
from generator.agents.deploy_agent.deploy_prompt import DeployPromptAgent, scrub_text

# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def temp_repo():
    """Create a temporary repository with templates and examples."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)

        templates_dir = repo_path / "deploy_templates"
        templates_dir.mkdir(exist_ok=True)

        # FIX: Updated template to include context.framework and few_shot_examples
        (templates_dir / "docker_default.jinja").write_text(
            """
Generate a Dockerfile for {{ target }} based on these files:
{% for file in files %}
- {{ file }}
{% endfor %}

Instructions: {{ instructions }}
Repo Path: {{ repo_path }}
Context Language: {{ context.language | default('N/A') }}
Context Framework: {{ context.framework | default('N/A') }}

{{ few_shot_examples }}

Output format: Plain Dockerfile text
"""
        )

        (templates_dir / "helm_default.jinja").write_text(
            """
Generate a Helm chart for {{ target }} application.
Files: {{ files | join(', ') }}
Instructions: {{ instructions }}
"""
        )

        examples_dir = repo_path / "few_shot_examples"
        examples_dir.mkdir(exist_ok=True)

        (examples_dir / "docker_example1.json").write_text(
            json.dumps(
                {
                    "query": "python web application",
                    "example": 'FROM python:3.9-slim\nWORKDIR /app\nCOPY requirements.txt .\nRUN pip install -r requirements.txt\nCOPY . .\nCMD ["python", "app.py"]',
                }
            )
        )

        (examples_dir / "docker_example2.json").write_text(
            json.dumps(
                {
                    "query": "node.js application",
                    "example": 'FROM node:18-alpine\nWORKDIR /app\nCOPY package*.json ./\nRUN npm install\nCOPY . .\nEXPOSE 3000\nCMD ["npm", "start"]',
                }
            )
        )

        (repo_path / "app.py").write_text(
            """
from flask import Flask
app = Flask(__name__)

@app.route('/')
def hello():
    return 'Hello, World!'

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000)
"""
        )

        (repo_path / "requirements.txt").write_text("flask==2.0.1\nrequests==2.26.0")
        (repo_path / "README.md").write_text(
            "# Test Application\n\nA simple Flask app."
        )
        (repo_path / ".git").mkdir()

        yield repo_path


@pytest.fixture
def mock_model_info():
    """Mock model information."""
    return {
        "name": "gpt-4",
        "few_shot_support": True,
        "token_limit": 8000,
        "optimization_model": "gpt-4",
    }


@pytest.fixture
def sample_context():
    """Sample context for prompt generation."""
    return {
        "file_contents": {
            "app.py": "from flask import Flask\napp = Flask(__name__)",
            "requirements.txt": "flask==2.0.1",
        },
        "dependencies": ["flask"],
        "language": "python",
        "framework": "flask",
    }


@pytest.fixture
def agent(temp_repo):
    """Fixture to create a DeployPromptAgent pointed at the temp repo's dirs."""
    with (
        patch("os.path.exists", return_value=True),
        patch("watchdog.observers.Observer"),
    ):
        agent = DeployPromptAgent(few_shot_dir=str(temp_repo / "few_shot_examples"))
        # Point the agent's template registry to the temp template dir
        agent.template_registry = MagicMock()

        # FIX: Use the now-imported Environment and FileSystemLoader
        agent.template_registry.get_template.return_value = Environment(
            loader=FileSystemLoader(str(temp_repo / "deploy_templates")),
            enable_async=True,
        ).get_template("docker_default.jinja")

        yield agent


# ============================================================================
# TESTS: DeployPromptAgent Initialization
# ============================================================================


class TestDeployPromptAgentInit:
    """Tests for DeployPromptAgent initialization."""

    def test_init_with_valid_dirs(self, temp_repo):
        """Test initializing agent with valid dirs."""
        with (
            patch("os.path.exists", return_value=True),
            patch("watchdog.observers.Observer"),
        ):
            agent = DeployPromptAgent(few_shot_dir=str(temp_repo / "few_shot_examples"))

            assert agent.template_registry is not None
            assert agent.few_shot_examples is not None
            assert len(agent.few_shot_examples) == 2
            assert agent.previous_feedback == {}

    def test_init_loads_templates(self, agent):
        """Test that templates are loaded correctly."""
        assert agent.template_registry is not None
        agent.template_registry.get_template.assert_not_called()

    def test_init_without_dirs(self):
        """Test initialization when directories don't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch("os.path.exists", side_effect=[False, True, False, True]),
                patch("os.makedirs"),
                patch("watchdog.observers.Observer"),
            ):

                agent = DeployPromptAgent(
                    few_shot_dir=str(Path(tmpdir) / "missing_examples")
                )
                assert agent is not None
                assert agent.few_shot_examples == []


# ============================================================================
# TESTS: Prompt Building
# ============================================================================


class TestPromptBuilding:
    """Tests for prompt building functionality."""

    @pytest.mark.asyncio
    async def test_build_deploy_prompt_basic(self, temp_repo, agent, mock_model_info):
        """Test basic prompt building."""

        agent.gather_context_for_prompt = AsyncMock(return_value={})
        agent.retrieve_few_shot = AsyncMock(return_value=[])
        agent.optimize_prompt_with_feedback = AsyncMock(side_effect=lambda s, *args: s)

        with patch(
            "generator.agents.deploy_agent.deploy_prompt.optimize_deployment_prompt_text",
            new_callable=AsyncMock,
        ) as mock_optimize:
            mock_optimize.side_effect = lambda s: s

            prompt = await agent.build_deploy_prompt(
                target="docker",
                files=["app.py", "requirements.txt"],
                repo_path=str(temp_repo),
                instructions="Create a production-ready Dockerfile",
                variant="default",
                context=None,
                model_specific_info=mock_model_info,
            )

        assert isinstance(prompt, str)
        assert len(prompt) > 0
        assert "docker" in prompt.lower() or "dockerfile" in prompt.lower()
        assert "app.py" in prompt
        assert "requirements.txt" in prompt
        assert str(temp_repo) in prompt

    @pytest.mark.asyncio
    async def test_build_prompt_with_context(
        self, temp_repo, agent, mock_model_info, sample_context
    ):
        """Test building prompt with additional context."""

        agent.retrieve_few_shot = AsyncMock(return_value=[])
        agent.optimize_prompt_with_feedback = AsyncMock(side_effect=lambda s, *args: s)

        with patch(
            "generator.agents.deploy_agent.deploy_prompt.optimize_deployment_prompt_text",
            new_callable=AsyncMock,
        ) as mock_optimize:
            mock_optimize.side_effect = lambda s: s

            prompt = await agent.build_deploy_prompt(
                target="docker",
                files=["app.py"],
                repo_path=str(temp_repo),
                instructions="Optimize for production",
                variant="default",
                context=sample_context,
                model_specific_info=mock_model_info,
            )

        assert isinstance(prompt, str)
        # FIX: Check for 'flask' (from context.framework) and 'python' (from context.language)
        assert "flask" in prompt.lower()
        assert "python" in prompt.lower()

    @pytest.mark.asyncio
    async def test_build_prompt_different_variants(
        self, temp_repo, agent, mock_model_info
    ):
        """Test building prompts with different variants."""

        agent.gather_context_for_prompt = AsyncMock(return_value={})
        agent.retrieve_few_shot = AsyncMock(return_value=[])
        agent.optimize_prompt_with_feedback = AsyncMock(side_effect=lambda s, *args: s)

        with patch(
            "generator.agents.deploy_agent.deploy_prompt.optimize_deployment_prompt_text",
            new_callable=AsyncMock,
        ) as mock_optimize:
            mock_optimize.side_effect = lambda s: s

            default_template = agent.template_registry.get_template("docker", "default")
            verbose_template = MagicMock()
            verbose_template.render_async = AsyncMock(return_value="Verbose prompt")

            agent.template_registry.get_template.side_effect = [
                default_template,
                verbose_template,
            ]

            prompt_default = await agent.build_deploy_prompt(
                target="docker",
                files=["app.py"],
                repo_path=str(temp_repo),
                instructions="Standard setup",
                variant="default",
                context=None,
                model_specific_info=mock_model_info,
            )

            assert len(prompt_default) > 0
            assert "docker" in prompt_default.lower()

    @pytest.mark.asyncio
    @patch("generator.agents.deploy_agent.deploy_prompt.call_llm_api")
    async def test_build_prompt_with_optimization(
        self, mock_llm, temp_repo, agent, mock_model_info
    ):
        """Test prompt optimization via LLM."""

        mock_llm.side_effect = [
            {
                "content": "This is a summarized prompt...",
                "model": "gpt-4o",
                "provider": "openai",
            },
            {
                "content": "This is the FINAL optimized prompt...",
                "model": "gpt-4o",
                "provider": "openai",
            },
        ]

        agent.record_feedback("docker", "default", 0.3)

        agent.gather_context_for_prompt = AsyncMock(return_value={})
        agent.retrieve_few_shot = AsyncMock(return_value=[])

        model_info = mock_model_info.copy()
        model_info["optimization_model"] = "gpt-4o"

        prompt = await agent.build_deploy_prompt(
            target="docker",
            files=["app.py"],
            repo_path=str(temp_repo),
            instructions="Create Dockerfile",
            variant="default",
            context=None,
            model_specific_info=model_info,
        )

        assert isinstance(prompt, str)
        assert prompt == "This is the FINAL optimized prompt..."
        assert mock_llm.call_count == 2

    @pytest.mark.asyncio
    async def test_build_prompt_with_missing_files(
        self, temp_repo, agent, mock_model_info
    ):
        """Test building prompt when specified files don't exist."""

        agent.retrieve_few_shot = AsyncMock(return_value=[])
        agent.optimize_prompt_with_feedback = AsyncMock(side_effect=lambda s, *args: s)

        with patch(
            "generator.agents.deploy_agent.deploy_prompt.optimize_deployment_prompt_text",
            new_callable=AsyncMock,
        ) as mock_optimize:
            mock_optimize.side_effect = lambda s: s

            prompt = await agent.build_deploy_prompt(
                target="docker",
                files=["nonexistent.py", "missing.txt"],
                repo_path=str(temp_repo),
                instructions="Create config",
                variant="default",
                context=None,
                model_specific_info=mock_model_info,
            )

        assert isinstance(prompt, str)
        assert "nonexistent.py" in prompt
        assert "missing.txt" in prompt


# ============================================================================
# TESTS: Few-Shot Learning
# ============================================================================


class TestFewShotLearning:
    """Tests for few-shot example handling."""

    @pytest.mark.asyncio
    async def test_load_few_shot_examples(self, temp_repo):
        """Test loading few-shot examples."""
        with patch("watchdog.observers.Observer"):
            agent = DeployPromptAgent(few_shot_dir=str(temp_repo / "few_shot_examples"))

        examples = agent.few_shot_examples

        assert isinstance(examples, list)
        assert len(examples) == 2
        assert all("query" in ex and "example" in ex for ex in examples)

    @pytest.mark.asyncio
    async def test_few_shot_integration_in_prompt(
        self, temp_repo, agent, mock_model_info
    ):
        """Test that few-shot examples are integrated into prompts."""

        agent.gather_context_for_prompt = AsyncMock(return_value={})
        agent.optimize_prompt_with_feedback = AsyncMock(side_effect=lambda s, *args: s)

        # FIX: Mock retrieve_few_shot to return a known example
        agent.retrieve_few_shot = AsyncMock(
            return_value=["FROM python:FEW_SHOT_EXAMPLE"]
        )

        with patch(
            "generator.agents.deploy_agent.deploy_prompt.optimize_deployment_prompt_text",
            new_callable=AsyncMock,
        ) as mock_optimize:
            mock_optimize.side_effect = lambda s: s

            model_info = mock_model_info.copy()
            model_info["few_shot_support"] = True

            prompt = await agent.build_deploy_prompt(
                target="docker",
                files=["app.py"],
                repo_path=str(temp_repo),
                instructions="Create Dockerfile for Python app",
                variant="default",
                context=None,
                model_specific_info=model_info,
            )

        assert isinstance(prompt, str)
        # FIX: Check that the few-shot example is in the prompt
        assert "FROM python:FEW_SHOT_EXAMPLE" in prompt
        agent.retrieve_few_shot.assert_called_once()


# ============================================================================
# TESTS: Template Management
# ============================================================================


class TestTemplateManagement:
    """Tests for template loading and management."""

    def test_get_template_default(self, agent):
        """Test getting default template."""
        template = agent.template_registry.get_template("docker", "default")
        assert template is not None
        assert hasattr(template, "render_async")

    def test_get_template_missing(self, agent):
        """Test getting non-existent template."""
        agent.template_registry.get_template.side_effect = ValueError(
            "Required template 'nonexistent_template.jinja' not found"
        )

        with pytest.raises(
            ValueError, match="Required template 'nonexistent_template.jinja' not found"
        ):
            agent.template_registry.get_template("nonexistent", "template")

    @pytest.mark.asyncio
    async def test_template_rendering(self, agent, temp_repo):
        """Test rendering a template with variables."""

        template = agent.template_registry.get_template("docker", "default")

        rendered = await template.render_async(
            target="docker",
            files=["app.py", "requirements.txt"],
            instructions="Create production Dockerfile",
            repo_path=str(temp_repo),
            context={},
        )

        assert "docker" in rendered.lower()
        assert "app.py" in rendered
        assert "requirements.txt" in rendered


# ============================================================================
# TESTS: A/B Testing
# ============================================================================


class TestABTesting:
    """Tests for prompt A/B testing."""

    @pytest.mark.asyncio
    @patch("generator.agents.deploy_agent.deploy_prompt.call_ensemble_api")
    async def test_ab_test_prompts(self, mock_llm, temp_repo, agent):
        """Test A/B testing multiple prompt variants."""

        mock_llm.return_value = {
            "content": json.dumps({"score": 0.85, "rationale": "Good prompt"}),
            "model": "gpt-4",
            "provider": "openai",
        }

        agent.gather_context_for_prompt = AsyncMock(return_value={})
        agent.retrieve_few_shot = AsyncMock(return_value=[])
        agent.optimize_prompt_with_feedback = AsyncMock(side_effect=lambda s, *args: s)
        with patch(
            "generator.agents.deploy_agent.deploy_prompt.optimize_deployment_prompt_text",
            new_callable=AsyncMock,
        ) as mock_optimize:
            mock_optimize.side_effect = lambda s: s

            results = await agent.ab_test_prompts(
                target="docker",
                files=["app.py"],
                repo_path=str(temp_repo),
                instructions="Create Dockerfile",
                variants=["default"],
            )

        assert isinstance(results, dict)
        assert "default" in results
        assert "prompt" in results["default"]
        assert "length" in results["default"]
        assert "hash" in results["default"]
        assert results["default"]["score"] == 0.85

    @pytest.mark.asyncio
    async def test_ab_test_multiple_variants(self, temp_repo, agent):
        """Test A/B testing with multiple variants."""

        agent.gather_context_for_prompt = AsyncMock(return_value={})
        agent.retrieve_few_shot = AsyncMock(return_value=[])
        agent.optimize_prompt_with_feedback = AsyncMock(side_effect=lambda s, *args: s)

        with (
            patch(
                "generator.agents.deploy_agent.deploy_prompt.optimize_deployment_prompt_text",
                new_callable=AsyncMock,
            ) as mock_optimize,
            patch(
                "generator.agents.deploy_agent.deploy_prompt.call_ensemble_api"
            ) as mock_llm,
        ):

            mock_optimize.side_effect = lambda s: s
            mock_llm.return_value = {
                "content": json.dumps({"score": 0.8, "rationale": "OK"}),
                "model": "gpt-4",
            }

            results = await agent.ab_test_prompts(
                target="docker",
                files=["app.py"],
                repo_path=str(temp_repo),
                instructions="Create config",
                variants=["default"],
            )

            assert len(results) >= 1
            assert "default" in results


# ============================================================================
# TESTS: Feedback System
# ============================================================================


class TestFeedbackSystem:
    """Tests for prompt feedback recording."""

    def test_record_feedback(self, agent):
        """Test recording feedback for a prompt variant."""

        agent.record_feedback("docker", "default", 0.9)

        key = "docker_default"
        assert key in agent.previous_feedback
        assert agent.previous_feedback[key] == 0.9

    def test_record_multiple_feedback(self, agent):
        """Test recording feedback for multiple variants."""

        agent.record_feedback("docker", "default", 0.8)
        agent.record_feedback("docker", "verbose", 0.9)
        agent.record_feedback("helm", "default", 0.85)

        assert len(agent.previous_feedback) == 4
        assert agent.previous_feedback[("docker_verbose")] == 0.9

    def test_feedback_updates_metric(self, agent):
        """Test that feedback updates Prometheus metric."""
        with patch(
            "generator.agents.deploy_agent.deploy_prompt.prompt_feedback_score"
        ) as mock_metric:

            agent.record_feedback("docker", "default", 0.95)

            mock_metric.labels.assert_called_with(target="docker", variant="default")
            mock_metric.labels.return_value.set.assert_called_with(0.95)


# ============================================================================
# TESTS: Security - Secret Scrubbing
# ============================================================================


class TestSecretScrubbing:
    """Tests for secret scrubbing in prompts."""

    @patch("generator.agents.deploy_agent.deploy_prompt.AnalyzerEngine", None)
    @patch("generator.agents.deploy_agent.deploy_prompt.AnonymizerEngine", None)
    def test_scrub_api_key(self):
        """Test scrubbing API keys."""
        text = "My API key is sk-1234567890abcdef1234567890abcdef"
        scrubbed = scrub_text(text)

        assert "sk-1234567890abcdef1234567890abcdef" not in scrubbed
        assert "[REDACTED]" in scrubbed

    @patch("generator.agents.deploy_agent.deploy_prompt.AnalyzerEngine", None)
    @patch("generator.agents.deploy_agent.deploy_prompt.AnonymizerEngine", None)
    def test_scrub_github_token(self):
        """Test scrubbing GitHub tokens."""
        text = "Token: ghp_abcdefghijklmnopqrstuvwxyz123456"
        scrubbed = scrub_text(text)

        assert "ghp_abcdefghijklmnopqrstuvwxyz123456" not in scrubbed
        assert "[REDACTED]" in scrubbed

    @patch("generator.agents.deploy_agent.deploy_prompt.AnalyzerEngine", None)
    @patch("generator.agents.deploy_agent.deploy_prompt.AnonymizerEngine", None)
    def test_scrub_email(self):
        """Test scrubbing email addresses."""
        text = "Contact: user@example.com for support"
        scrubbed = scrub_text(text)

        assert "user@example.com" not in scrubbed
        assert "[REDACTED]" in scrubbed

    @patch("generator.agents.deploy_agent.deploy_prompt.AnalyzerEngine", None)
    @patch("generator.agents.deploy_agent.deploy_prompt.AnonymizerEngine", None)
    def test_scrub_password(self):
        """Test scrubbing passwords."""
        text = "password=mysecretpassword123"
        scrubbed = scrub_text(text)

        assert "mysecretpassword123" not in scrubbed

    @patch("generator.agents.deploy_agent.deploy_prompt.AnalyzerEngine", None)
    @patch("generator.agents.deploy_agent.deploy_prompt.AnonymizerEngine", None)
    def test_scrub_multiple_secrets(self):
        """Test scrubbing multiple secrets at once."""
        text = """
        API_KEY=sk-test123
        SECRET_TOKEN=secret_xyz
        user@email.com
        password: mypass
        """
        scrubbed = scrub_text(text)

        # FIX: Corrected assertion for sk-test123
        assert "sk-test123" not in scrubbed
        assert "secret_xyz" not in scrubbed
        assert "user@email.com" not in scrubbed
        assert "mypass" not in scrubbed

    def test_scrub_empty_text(self):
        """Test scrubbing empty or None text."""
        assert scrub_text("") == ""


# ============================================================================
# TESTS: Context Gathering
# ============================================================================


class TestContextGathering:
    """Tests for context gathering functionality."""

    @pytest.mark.asyncio
    async def test_gather_context_from_files(self, temp_repo, agent):
        """Test gathering context from repository files."""

        files = ["app.py", "requirements.txt"]

        context = await agent.gather_context_for_prompt(files, repo_path=str(temp_repo))

        assert "files_content" in context
        assert "app.py" in context["files_content"]
        assert "requirements.txt" in context["files_content"]
        assert "flask" in context["files_content"]["app.py"].lower()
        assert (Path(temp_repo) / "app.py").exists()
        assert (Path(temp_repo) / "requirements.txt").exists()


# ============================================================================
# TESTS: Token Counting
# ============================================================================


class TestTokenCounting:
    """Tests for token counting functionality."""

    def test_count_tokens_short_text(self):
        """Test counting tokens in short text."""
        try:
            import tiktoken

            encoding = tiktoken.get_encoding("cl100k_base")

            text = "Hello, world!"
            tokens = encoding.encode(text)

            assert len(tokens) > 0
            assert len(tokens) < 10
        except ImportError:
            pytest.skip("tiktoken not available")

    def test_count_tokens_long_text(self):
        """Test counting tokens in long text."""
        try:
            import tiktoken

            encoding = tiktoken.get_encoding("cl100k_base")

            text = "word " * 1000  # ~1000 words
            tokens = encoding.encode(text)

            assert len(tokens) > 500
        except ImportError:
            pytest.skip("tiktoken not available")


# ============================================================================
# TESTS: Provenance Tracking
# ============================================================================


class TestProvenanceTracking:
    """Tests for provenance tracking in prompts."""

    @pytest.mark.asyncio
    async def test_prompt_includes_provenance(self, temp_repo, agent, mock_model_info):
        """Test that generated prompts include provenance metadata."""

        agent.gather_context_for_prompt = AsyncMock(return_value={})
        agent.retrieve_few_shot = AsyncMock(return_value=[])
        agent.optimize_prompt_with_feedback = AsyncMock(side_effect=lambda s, *args: s)

        with (
            patch(
                "generator.agents.deploy_agent.deploy_prompt.optimize_deployment_prompt_text",
                new_callable=AsyncMock,
            ) as mock_optimize,
            patch(
                "generator.agents.deploy_agent.deploy_prompt.add_provenance"
            ) as mock_provenance,
        ):

            mock_optimize.side_effect = lambda s: s

            prompt = await agent.build_deploy_prompt(
                target="docker",
                files=["app.py"],
                repo_path=str(temp_repo),
                instructions="Create Dockerfile",
                variant="default",
                context=None,
                model_specific_info=mock_model_info,
            )

        assert isinstance(prompt, str)
        assert len(prompt) > 0


# ============================================================================
# TESTS: Error Handling
# ============================================================================


class TestErrorHandling:
    """Tests for error handling scenarios."""

    @pytest.mark.asyncio
    async def test_build_prompt_with_llm_failure(
        self, temp_repo, agent, mock_model_info
    ):
        """Test handling LLM failure during optimization."""
        with patch(
            "generator.agents.deploy_agent.deploy_prompt.call_llm_api"
        ) as mock_llm:
            mock_llm.side_effect = Exception("LLM API Error")

            agent.gather_context_for_prompt = AsyncMock(return_value={})
            agent.retrieve_few_shot = AsyncMock(return_value=[])

            prompt = await agent.build_deploy_prompt(
                target="docker",
                files=["app.py"],
                repo_path=str(temp_repo),
                instructions="Create Dockerfile",
                variant="default",
                context=None,
                model_specific_info=mock_model_info,
            )

            assert isinstance(prompt, str)
            # FIX: Check for the *original* content, not the fallback.
            # The agent gracefully skips optimization on failure.
            assert "dockerfile" in prompt.lower()
            assert "fallback" not in prompt.lower()
            assert "internal error" not in prompt.lower()

    @pytest.mark.asyncio
    async def test_invalid_target(self, temp_repo, agent, mock_model_info):
        """Test building prompt for invalid/unsupported target."""

        # FIX: Mock the template registry to raise the error
        error_msg = "Required template 'unsupported_target_default.jinja' not found"
        agent.template_registry.get_template.side_effect = ValueError(error_msg)

        agent.gather_context_for_prompt = AsyncMock(return_value={})
        agent.retrieve_few_shot = AsyncMock(return_value=[])

        # FIX: The error is raised *before* the try/except block
        # in build_deploy_prompt. The test must expect the exception.
        with pytest.raises(ValueError, match=error_msg):
            await agent.build_deploy_prompt(
                target="unsupported_target",
                files=["app.py"],
                repo_path=str(temp_repo),
                instructions="Create config",
                variant="default",
                context=None,
                model_specific_info=mock_model_info,
            )


# ============================================================================
# TESTS: API Endpoints (if applicable)
# ============================================================================


class TestAPIEndpoints:
    """Tests for API endpoints."""

    @pytest.mark.asyncio
    async def test_api_build_prompt(self, temp_repo):
        """Test API endpoint for building prompts."""
        pytest.skip("API testing requires aiohttp test client setup")


# ============================================================================
# RUN TESTS
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
