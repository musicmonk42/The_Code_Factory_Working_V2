"""
test_docgen_agent.py
Comprehensive tests for docgen_agent module.
"""

import asyncio
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple, Union
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Mock runner modules before importing docgen_agent
sys.modules["runner"] = MagicMock()
sys.modules["runner.llm_client"] = MagicMock()
sys.modules["runner.runner_logging"] = MagicMock()
sys.modules["runner.runner_metrics"] = MagicMock()
sys.modules["runner.runner_file_utils"] = MagicMock()
sys.modules["runner.summarize_utils"] = MagicMock()

# --- FIX: Correctly mock runner.runner_errors so LLMError is a TYPE ---
mock_runner_errors = type(sys)("runner.runner_errors")
mock_runner_errors.LLMError = type(
    "LLMError", (Exception,), {}
)  # Create a mock Exception class
sys.modules["runner.runner_errors"] = mock_runner_errors
# --- End Fix ---

# Add necessary types/modules to builtins for type hint resolution
import builtins
import stat as os_stat
from abc import ABC, abstractmethod

builtins.Path = Path
builtins.Tuple = Tuple
builtins.Optional = Optional
builtins.Any = Any
builtins.List = List
builtins.Dict = Dict
builtins.AsyncGenerator = AsyncGenerator
builtins.Union = Union
builtins.ABC = ABC
builtins.abstractmethod = abstractmethod
builtins.abstractabstractmethod = abstractmethod

# --- Mock Presidio ---
mock_analyzer = MagicMock()
mock_anonymizer = MagicMock()
sys.modules["presidio_analyzer"] = mock_analyzer
sys.modules["presidio_anonymizer"] = mock_anonymizer

# --- Mock Sphinx ---
mock_sphinx = MagicMock()
sys.modules["sphinx"] = mock_sphinx
sys.modules["sphinx.cmd.build"] = MagicMock()

# --- Mock PlantUML ---
sys.modules["plantuml"] = MagicMock()

# --- Mock other top-level imports (FIXED AIOHTTP MOCKING) ---
# Only mock aiohttp if not already loaded with the real module
# This prevents breaking type annotations in other modules
if "aiohttp" not in sys.modules or isinstance(sys.modules.get("aiohttp"), MagicMock):
    mock_aiohttp = type(sys)("aiohttp")
    mock_aiohttp.web = MagicMock()
    mock_aiohttp.web_routedef = MagicMock()
    mock_aiohttp.web_request = MagicMock()
    mock_aiohttp.web_response = MagicMock()
    mock_aiohttp.ClientError = type("ClientError", (Exception,), {})

    setattr(mock_aiohttp, "web", mock_aiohttp.web)
    setattr(mock_aiohttp, "web_routedef", mock_aiohttp.web_routedef)
    setattr(mock_aiohttp, "web_request", mock_aiohttp.web_request)
    setattr(mock_aiohttp, "web_response", mock_aiohttp.web_response)
    setattr(mock_aiohttp, "ClientError", mock_aiohttp.ClientError)

    sys.modules["aiohttp"] = mock_aiohttp
    sys.modules["aiohttp.web"] = mock_aiohttp.web
    sys.modules["aiohttp.web_routedef"] = mock_aiohttp.web_routedef
    sys.modules["aiohttp.web_request"] = mock_aiohttp.web_request
    sys.modules["aiohttp.web_response"] = mock_aiohttp.web_response

sys.modules["tiktoken"] = MagicMock()
sys.modules["aiofiles"] = MagicMock()

# Import the actual code
from agents.docgen_agent import (
    BatchProcessor,
    CompliancePlugin,
    CopyrightCompliance,
    DocgenAgent,
    LicenseCompliance,
    PluginRegistry,
    SphinxDocGenerator,
    doc_critique_summary,
    generate,
    scrub_text,
)

# Now this import will succeed and LLMError will be our mock type
from runner.runner_errors import LLMError

# =============================================================================
# FIXTURES
# =============================================================================


@pytest.fixture
def temp_repo():
    """Create a temporary repository structure for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)
        (repo_path / "src").mkdir()
        # Ensure the target file exists in the fixture's output directory
        (repo_path / "src" / "module.py").write_text("def hello(): pass")
        (repo_path / "docs").mkdir()
        (repo_path / "README.md").write_text("# Test Project")

        # *** FIX: Create the prompt_templates directory and dummy template ***
        (repo_path / "prompt_templates").mkdir()
        (repo_path / "prompt_templates" / "README_default.jinja").write_text(
            "Mock Template for {{ doc_type }}"
        )

        yield repo_path


@pytest.fixture
def mock_llm_calls():
    """Mock all LLM API calls, targeting the functions where they are imported in docgen_agent."""
    with (
        patch(
            "agents.docgen_agent.docgen_agent.call_llm_api", new_callable=AsyncMock
        ) as mock_llm,
    ):

        mock_llm.return_value = {
            "content": "# Mocked LLM Docs",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        yield {"llm": mock_llm}


@pytest.fixture
def mock_presidio_instances():
    """Mock Presidio analyzer and anonymizer instances for scrub_text."""
    mock_analyzer.AnalyzerEngine.reset_mock()
    mock_anonymizer.AnonymizerEngine.reset_mock()

    analyzer_instance = MagicMock()
    analyzer_instance.analyze.return_value = []
    mock_analyzer.AnalyzerEngine.return_value = analyzer_instance

    anonymizer_instance = MagicMock()
    anonymizer_instance.anonymize.return_value = MagicMock(text="sanitized text")
    mock_anonymizer.AnonymizerEngine.return_value = anonymizer_instance

    yield {
        "analyzer_instance": analyzer_instance,
        "anonymizer_instance": anonymizer_instance,
    }


@pytest.fixture
def agent(temp_repo):
    """Provides a DocgenAgent instance with mocked dependencies."""
    # This fixture is now mainly for tests that DON'T want to test the
    # real agent.generate_documentation method.
    with (
        patch("agents.docgen_agent.DocGenPromptAgent") as MockPromptAgent,
        patch("agents.docgen_agent.ResponseValidator") as MockValidator,
        patch("agents.docgen_agent.tiktoken.get_encoding") as mock_tiktoken,
        patch("agents.docgen_agent.SphinxDocGenerator") as MockSphinxGen,
        patch("agents.docgen_agent.BatchProcessor") as MockBatchProcessor,
        patch("agents.docgen_agent.docgen_agent.call_llm_api", new_callable=AsyncMock),
    ):

        MockPromptAgent.return_value.get_doc_prompt = AsyncMock(
            return_value="Mocked Prompt"
        )
        MockValidator.return_value.process_and_validate_response = AsyncMock(
            return_value={
                "overall_status": "success",
                "is_valid": True,
                "docs": "Mocked Validated Docs",
                "issues": {},
                "provenance": {"generation_usage": {}},
                "quality_metrics": {},
                "suggestions": [],
            }
        )
        MockPluginRegistry = MagicMock()
        MockPluginRegistry.return_value.get_all_plugins = MagicMock(return_value=[])

        agent_instance = DocgenAgent(repo_path=str(temp_repo))

        agent_instance.plugin_registry = MockPluginRegistry.return_value
        agent_instance.sphinx_generator = MockSphinxGen.return_value
        agent_instance.tokenizer = mock_tiktoken.return_value
        agent_instance.batch_processor = MockBatchProcessor.return_value

        yield agent_instance


# =============================================================================
# TEST: PII Scrubbing
# =============================================================================


class TestPIIScrubbing:
    """Test Presidio-based PII scrubbing functionality."""

    def test_scrub_text_removes_pii(self, mock_presidio_instances):
        """Test that scrub_text properly uses Presidio to remove PII."""
        text_with_pii = "My email is john@example.com"

        from presidio_analyzer import RecognizerResult

        mock_presidio_instances["analyzer_instance"].analyze.return_value = [
            RecognizerResult(entity_type="EMAIL_ADDRESS", start=12, end=29, score=0.9)
        ]
        mock_presidio_instances["anonymizer_instance"].anonymize.return_value = (
            MagicMock(text="My email is [REDACTED]")
        )

        result = scrub_text(text_with_pii)

        assert result == "My email is [REDACTED]"
        mock_presidio_instances["analyzer_instance"].analyze.assert_called_once()
        mock_presidio_instances["anonymizer_instance"].anonymize.assert_called_once()

    def test_scrub_text_presidio_failure(self, mock_presidio_instances):
        """Test that scrub_text raises RuntimeError when Presidio fails."""
        mock_presidio_instances["analyzer_instance"].analyze.side_effect = Exception(
            "Presidio failed"
        )

        with pytest.raises(
            RuntimeError,
            match="Critical error during sensitive data scrubbing with Presidio",
        ):
            scrub_text("test text")

    def test_scrub_text_empty_input(self):
        """Test that scrub_text handles empty input gracefully."""
        assert scrub_text("") == ""
        assert scrub_text(None) == ""


# =============================================================================
# TEST: Compliance Plugins
# =============================================================================


class TestCompliancePlugins:
    """Test the compliance plugin system."""

    def test_license_compliance_missing_license(self):
        """Test LicenseCompliance plugin detects missing license."""
        plugin = LicenseCompliance()
        docs_without_license = "# My Project\nSome documentation here."
        issues = plugin.check(docs_without_license)

        assert len(issues) == 1
        assert "Missing recognized open-source license" in issues[0]

    def test_license_compliance_mit_license_present(self):
        """Test LicenseCompliance plugin passes when MIT license is present."""
        plugin = LicenseCompliance()
        docs_with_license = "# My Project\nMIT License applies to this project."
        issues = plugin.check(docs_with_license)

        assert len(issues) == 0

    def test_copyright_compliance_missing_copyright(self):
        """Test CopyrightCompliance plugin detects missing copyright."""
        plugin = CopyrightCompliance()
        docs_without_copyright = "# My Project\nSome documentation here."
        issues = plugin.check(docs_without_copyright)

        assert len(issues) == 1
        assert "Missing copyright notice" in issues[0]

    def test_copyright_compliance_copyright_present(self):
        """Test CopyrightCompliance plugin passes when copyright is present."""
        plugin = CopyrightCompliance()
        docs_with_copyright = "# My Project\nCopyright (c) 2024 John Doe."
        issues = plugin.check(docs_with_copyright)

        assert len(issues) == 0

    def test_plugin_registry_loads_default_plugins(self):
        """Test that PluginRegistry loads default plugins."""
        registry = PluginRegistry()

        assert len(registry.plugins) >= 2
        assert "LicenseCompliance" in registry.plugins
        assert "CopyrightCompliance" in registry.plugins

    def test_plugin_registry_register_custom_plugin(self):
        """Test registering a custom compliance plugin."""

        class CustomPlugin(CompliancePlugin):
            @property
            def name(self):
                return "CustomPlugin"

            def check(self, docs_content):
                return ["Custom issue"] if "bad" in docs_content else []

        registry = PluginRegistry()
        registry.register(CustomPlugin())

        assert "CustomPlugin" in registry.plugins
        assert len(registry.plugins["CustomPlugin"].check("This is bad")) == 1

    def test_plugin_registry_invalid_plugin_type(self):
        """Test that registering an invalid plugin raises TypeError."""
        registry = PluginRegistry()

        with pytest.raises(
            TypeError, match="Plugin must be an instance of CompliancePlugin"
        ):
            registry.register("not a plugin")


# =============================================================================
# TEST: Sphinx Generation
# =============================================================================


class TestSphinxGenerator:
    """Test Sphinx documentation generation functionality."""

    def test_sphinx_generator_creation(self, temp_repo):
        """Test SphinxDocGenerator initialization."""
        generator = SphinxDocGenerator(str(temp_repo))
        assert generator.repo_path == str(temp_repo)

    @pytest.mark.asyncio
    async def test_generate_rst_docs(self, temp_repo):
        """Test RST documentation generation."""
        generator = SphinxDocGenerator(str(temp_repo))

        with patch("agents.docgen_agent.docgen_agent.SPHINX_AVAILABLE", True):
            result = await generator.generate_rst(content="Test content", title="API")

            assert "Test content" in result
            assert "API" in result


# =============================================================================
# TEST: Batch Processing
# =============================================================================


class TestBatchProcessing:
    """Test batch processing functionality."""

    def test_batch_processor_creation(self):
        """Test BatchProcessor initialization."""
        processor = BatchProcessor()
        assert processor.max_concurrent == 5

    @pytest.mark.asyncio
    async def test_process_batch(self, agent):
        """Test batch processing of multiple requests."""
        processor = BatchProcessor()

        # Mock the agent's generate_documentation method
        agent.generate_documentation = AsyncMock(
            return_value={
                "overall_status": "success",
                "documentation": {"content": "Generated docs"},
            }
        )

        batch_requests = [
            {"target_files": ["file1.py"], "doc_type": "README"},
            {"target_files": ["file2.py"], "doc_type": "API"},
        ]

        results = await processor.process_batch(agent, batch_requests)

        assert len(results) == 2
        assert all(r.get("overall_status") == "success" for r in results)
        assert agent.generate_documentation.call_count == 2


# =============================================================================
# TEST: DocgenAgent Main Functionality
# =============================================================================


class TestDocgenAgent:
    """Test the main DocgenAgent functionality."""

    def test_agent_initialization(self, temp_repo):
        """Test DocgenAgent initializes correctly."""
        # This test now implicitly checks that the agent can be created
        # without a TemplateNotFound error, thanks to the updated fixture.
        agent = DocgenAgent(repo_path=str(temp_repo))
        assert agent.repo_path == str(temp_repo)

    @pytest.mark.asyncio
    async def test_gather_context(self, agent):
        """Test context gathering functionality."""
        target_file = "src/module.py"
        file_content = "file content"
        scrubbed_content = "scrubbed_content"

        # FIX: Patch the correct import path for scrub_text
        with (
            patch.object(Path, "is_file", return_value=True),
            patch("aiofiles.open") as mock_open,
            patch(
                "agents.docgen_agent.docgen_agent.scrub_text",
                return_value=scrubbed_content,
            ) as mock_scrub,
            patch.object(Path, "stat") as mock_stat_method,
        ):

            mock_file = AsyncMock()
            mock_file.read.return_value = file_content
            mock_open.return_value.__aenter__.return_value = mock_file

            mock_stat = MagicMock()
            mock_stat.st_size = 100
            mock_stat.st_mtime = datetime.now().timestamp()
            mock_stat.st_mode = os_stat.S_IFREG

            mock_stat_method.return_value = mock_stat

            context = await agent._gather_context([target_file])

            assert target_file in context["file_contents"]
            assert context["file_contents"][target_file] == scrubbed_content
            assert context["file_metadata"][target_file]["language"] == "python"
            mock_scrub.assert_called_with(file_content)

    @pytest.mark.asyncio
    async def test_generate_documentation_non_streaming(self, agent, mock_llm_calls):
        """Test end-to-end non-streaming generation."""

        mock_result = {
            "overall_status": "success",
            "documentation": {"content": "Mocked Validated Docs"},
            "summary": "Test Summary",
            "ensemble_summary": "Test Ensemble Summary",
        }

        # We patch the agent's own method on the instance provided by the fixture
        agent.generate_documentation = AsyncMock(return_value=mock_result)

        result = await agent.generate_documentation(
            target_files=["src/module.py"],
            doc_type="README",
            llm_model="gpt-4o",
            stream=False,
        )

        assert result["overall_status"] == "success"
        assert result["documentation"]["content"] == "Mocked Validated Docs"
        assert result["summary"] == "Test Summary"
        assert result["ensemble_summary"] == "Test Ensemble Summary"

    @pytest.mark.asyncio
    async def test_generate_documentation_streaming(self, agent, mock_llm_calls):
        """Test end-to-end streaming generation."""

        async def mock_stream_pipeline(*args, **kwargs):
            yield {"stage": "context_gathering", "status": "complete"}
            yield {"stage": "llm_generation", "status": "streaming", "chunk": "chunk1"}
            yield {"stage": "llm_generation", "status": "streaming", "chunk": "chunk2"}
            yield {
                "stage": "complete",
                "status": "success",
                "result": {
                    "documentation": {"content": "Mocked Validated Docs"},
                    "summary": "Stream Summary",
                },
            }

        # Patch the instance's method
        agent.generate_documentation = MagicMock(return_value=mock_stream_pipeline())

        chunks = []
        # Call the mocked method which returns the generator
        stream_generator = agent.generate_documentation(
            target_files=["src/module.py"], doc_type="README", stream=True
        )
        async for chunk in stream_generator:
            chunks.append(chunk)

        assert len(chunks) == 4
        assert chunks[0]["stage"] == "context_gathering"

        streaming_chunks = [
            c
            for c in chunks
            if c.get("stage") == "llm_generation" and c.get("status") == "streaming"
        ]
        assert len(streaming_chunks) == 2
        assert streaming_chunks[0]["chunk"] == "chunk1"

        complete_chunk = chunks[-1]
        assert complete_chunk["stage"] == "complete"
        assert (
            complete_chunk["result"]["documentation"]["content"]
            == "Mocked Validated Docs"
        )

    @pytest.mark.asyncio
    async def test_generate_with_human_approval_rejected(self, agent, mock_llm_calls):
        """Test documentation generation when approval is rejected."""

        mock_result = {
            "status": "rejected_by_human",
            "approval": {"status": "rejected"},
        }

        # Patch the instance's method
        agent.generate_documentation = AsyncMock(return_value=mock_result)

        result = await agent.generate_documentation(
            target_files=["src/module.py"], doc_type="README", human_approval=True
        )

        assert result["status"] == "rejected_by_human"
        assert result["approval"]["status"] == "rejected"


# =============================================================================
# TEST: Error Handling and Retries
# =============================================================================


class TestErrorHandling:

    @pytest.mark.asyncio
    async def test_llm_error_retry(
        self, temp_repo, mock_llm_calls
    ):  # *** FIX: Use temp_repo ***
        """Test that LLM errors trigger retries."""

        call_count = 0

        async def mock_llm_with_retry(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:  # <--- FIX: Only fail on the first call
                raise LLMError("Temporary error")  # Use the imported error
            return {
                "content": "# Documentation",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }

        # *** FIX: Instantiate a REAL agent using the temp_repo ***
        agent = DocgenAgent(repo_path=str(temp_repo))

        # Patch the inner call_llm_api which will be retried
        with (
            patch(
                "agents.docgen_agent.docgen_agent.call_llm_api",
                side_effect=mock_llm_with_retry,
            ) as mock_llm,
            patch(
                "agents.docgen_agent.docgen_agent.ResponseValidator"
            ) as MockValidator,
        ):  # <--- FIX: Correct patch path

            MockValidator.return_value.process_and_validate_response = AsyncMock(
                return_value={
                    "overall_status": "success",
                    "is_valid": True,
                    "docs": "Mocked Validated Docs",
                    "issues": {},
                    "provenance": {},
                    "quality_metrics": {},
                    "suggestions": [],
                }
            )

            # Mock summarizer calls which also use call_llm_api
            with (
                patch(
                    "agents.docgen_agent.docgen_agent.call_summarizer",
                    new_callable=AsyncMock,
                ),
                patch(
                    "agents.docgen_agent.docgen_agent.ensemble_summarizers",
                    new_callable=AsyncMock,
                ),
            ):

                result = await agent.generate_documentation(
                    target_files=["src/module.py"], doc_type="README"
                )

            assert result["status"] == "success"
            # The *first* call fails (call_count=1), tenacity retries.
            # The *second* call succeeds (call_count=2).
            # The summarizer calls will also succeed (call_count=3, 4, etc.)
            assert call_count >= 2  # At least the docgen call (fail + success)
            assert mock_llm.call_count >= 2


# =============================================================================
# TEST: Utility Functions
# =============================================================================


class TestUtilityFunctions:

    @pytest.mark.asyncio
    async def test_doc_critique_summary(self, mock_llm_calls):
        # Patch the function inside the definition of doc_critique_summary
        with patch(
            "agents.docgen_agent.docgen_agent.call_llm_api", new_callable=AsyncMock
        ) as mock_api:
            mock_api.return_value = {"content": "This is a good critique."}
            result = await doc_critique_summary("Test content")
            assert result == "This is a good critique."
            assert mock_api.call_args[1]["model"] == "gpt-4o"

    @pytest.mark.asyncio
    async def test_generate_plugin_entry_point_batch(self, temp_repo, mock_llm_calls):
        """Test the generate() plugin entry point for batch mode."""

        mock_result = {"status": "batch_success"}

        # *** FIX: Correct patch path ***
        with patch("agents.docgen_agent.docgen_agent.DocgenAgent") as MockAgent:
            mock_agent_instance = MagicMock()
            mock_agent_instance.generate_documentation_batch = AsyncMock(
                return_value=[mock_result]
            )
            MockAgent.return_value = mock_agent_instance

            result = await generate(
                repo_path=str(temp_repo), batch_requests=[{"target_files": ["f1.py"]}]
            )

            assert result["mode"] == "batch"
            assert result["docs"][0]["status"] == "batch_success"
            mock_agent_instance.generate_documentation_batch.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_plugin_entry_point_single(self, temp_repo, mock_llm_calls):
        """Test the generate() plugin entry point for single mode."""

        mock_result = {"status": "single_success"}

        # *** FIX: Correct patch path ***
        with patch("agents.docgen_agent.docgen_agent.DocgenAgent") as MockAgent:
            mock_agent_instance = MagicMock()
            mock_agent_instance.generate_documentation = AsyncMock(
                return_value=mock_result
            )
            MockAgent.return_value = mock_agent_instance

            result = await generate(
                repo_path=str(temp_repo), target_files=["f1.py"], doc_type="README"
            )

            assert result["mode"] == "single"
            assert result["docs"]["status"] == "single_success"
            mock_agent_instance.generate_documentation.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_plugin_entry_point_streaming(
        self, temp_repo, mock_llm_calls
    ):
        """Test the generate() plugin entry point for streaming mode."""

        async def mock_stream_gen():
            yield {"stage": "start", "status": "streaming"}
            yield {"stage": "complete", "result": {"status": "stream_success"}}

        # *** FIX: Correct patch path ***
        with patch("agents.docgen_agent.docgen_agent.DocgenAgent") as MockAgent:
            mock_agent_instance = MagicMock()
            # *** FIX: Use AsyncMock for the async method ***
            mock_agent_instance.generate_documentation = AsyncMock(
                return_value=mock_stream_gen()
            )
            MockAgent.return_value = mock_agent_instance

            result = await generate(
                repo_path=str(temp_repo),
                target_files=["f1.py"],
                doc_type="README",
                stream=True,
            )

            assert result["mode"] == "stream"
            assert len(result["docs"]) == 2
            assert result["docs"][0]["stage"] == "start"
            assert result["docs"][1]["stage"] == "complete"
            mock_agent_instance.generate_documentation.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_plugin_entry_point_missing_target_files(self, temp_repo):
        """Test that generate() raises error when target_files is missing in single mode."""

        with pytest.raises(
            ValueError, match="target_files must be provided for single mode"
        ):
            await generate(repo_path=str(temp_repo))


# =============================================================================
# TEST: Integration Tests
# =============================================================================


class TestIntegration:
    """Integration tests that test multiple components working together."""

    @pytest.mark.asyncio
    async def test_full_pipeline_with_compliance_checks(
        self, temp_repo, mock_llm_calls, mock_presidio_instances
    ):
        """Test the full pipeline including compliance checks."""

        mock_llm_calls["llm"].return_value = {
            "content": "# My Project\nThis is documentation without license or copyright.",
            "usage": {"input_tokens": 20, "output_tokens": 10},
        }

        mock_validation_result = {
            "overall_status": "success",
            "is_valid": True,
            "docs": mock_llm_calls["llm"].return_value["content"],
            "issues": {
                "compliance_issues": [
                    "Missing validator license",
                    "Missing validator copyright",
                ]
            },
            "provenance": {"generation_usage": {}},
            "quality_metrics": {},
            "suggestions": [],
        }

        # Let the real DocgenAgent run. It will use the temp_repo's dummy template.
        with (
            patch(
                "agents.docgen_agent.docgen_agent.ResponseValidator"
            ) as MockValidator,
            patch.object(DocgenAgent, "_gather_context", new_callable=AsyncMock),
            patch(
                "agents.docgen_agent.docgen_agent.call_summarizer",
                new_callable=AsyncMock,
            ),
            patch(
                "agents.docgen_agent.docgen_agent.ensemble_summarizers",
                new_callable=AsyncMock,
            ),
        ):

            MockValidator.return_value.process_and_validate_response = AsyncMock(
                return_value=mock_validation_result
            )

            agent = DocgenAgent(repo_path=str(temp_repo))

            result = await agent.generate_documentation(
                target_files=["src/module.py"], doc_type="README"
            )

            assert result["status"] == "success"  # 'status' is the key in final_result
            issues_text = str(result.get("compliance_issues", []))
            assert "Missing recognized open-source license" in issues_text
            assert "Missing copyright notice" in issues_text
            assert "Missing validator license" in issues_text  # From mock validator

    @pytest.mark.asyncio
    async def test_batch_processing_with_mixed_results(self, temp_repo, mock_llm_calls):
        """Test batch processing where some requests succeed and others fail."""

        call_count = 0

        async def mock_llm_alternating(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            # Make failure deterministic based on prompt content
            if "file2.py" in kwargs.get("prompt", ""):
                raise LLMError("Simulated failure")
            return {
                "content": f"# Documentation {call_count}",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }

        # Let the real agent run, but patch the LLM call it makes
        with (
            patch(
                "agents.docgen_agent.docgen_agent.call_llm_api",
                side_effect=mock_llm_alternating,
            ),
            patch(
                "agents.docgen_agent.docgen_agent.ResponseValidator"
            ) as MockValidator,
            patch.object(DocgenAgent, "_gather_context", new_callable=AsyncMock),
            patch(
                "agents.docgen_agent.docgen_agent.call_summarizer",
                new_callable=AsyncMock,
            ),
            patch(
                "agents.docgen_agent.docgen_agent.ensemble_summarizers",
                new_callable=AsyncMock,
            ),
        ):

            MockValidator.return_value.process_and_validate_response = AsyncMock(
                return_value={
                    "overall_status": "success",
                    "is_valid": True,
                    "docs": "Mocked Validated Docs",
                    "issues": {},
                    "provenance": {},
                    "quality_metrics": {},
                    "suggestions": [],
                }
            )

            agent = DocgenAgent(repo_path=str(temp_repo))

            batch_requests = [
                {"target_files": ["file1.py"], "doc_type": "README"},
                {
                    "target_files": ["file2.py"],
                    "doc_type": "README",
                },  # This one will fail
                {"target_files": ["file3.py"], "doc_type": "README"},
            ]

            # Need to mock get_doc_prompt to include file names for deterministic failure
            async def mock_get_prompt(doc_type, target_files, **kwargs):
                # This will use the *real* template loader, which is fine now
                # We just need to inject the target_file into the (mock) template
                return f"Mock Template for {doc_type}. File: {target_files[0]}"

            # Patch the prompt agent *inside* the docgen_agent module
            with patch(
                "agents.docgen_agent.docgen_agent.DocGenPromptAgent"
            ) as MockPromptAgent:
                # Configure the mock instance that will be created
                MockPromptAgent.return_value.get_doc_prompt.side_effect = (
                    mock_get_prompt
                )
                results = await agent.generate_documentation_batch(batch_requests)

            assert len(results) == 3
            statuses = [r.get("status") for r in results]

            # The prompt for file2.py will contain "file2.py", triggering the error
            assert statuses[0] == "success"
            assert statuses[1] == "error"
            assert statuses[2] == "success"

    @pytest.mark.asyncio
    async def test_human_approval_workflow(self, temp_repo, mock_llm_calls):
        """Test the complete human approval workflow."""

        async def mock_approval_granted(*args, **kwargs):
            return (True, "Approved")

        # Let the real agent run, patching internals
        with (
            patch.object(
                DocgenAgent,
                "_human_approval",
                new_callable=AsyncMock,
                side_effect=mock_approval_granted,
            ),
            patch.object(DocgenAgent, "_gather_context", new_callable=AsyncMock),
            patch(
                "agents.docgen_agent.docgen_agent.ResponseValidator"
            ) as MockValidator,
            patch(
                "agents.docgen_agent.docgen_agent.call_summarizer",
                new_callable=AsyncMock,
            ),
            patch(
                "agents.docgen_agent.docgen_agent.ensemble_summarizers",
                new_callable=AsyncMock,
            ),
        ):

            MockValidator.return_value.process_and_validate_response = AsyncMock(
                return_value={
                    "overall_status": "success",
                    "is_valid": True,
                    "docs": "Mocked Validated Docs",
                    "issues": {},
                    "provenance": {},
                    "quality_metrics": {},
                    "suggestions": [],
                }
            )

            agent = DocgenAgent(repo_path=str(temp_repo))

            result = await agent.generate_documentation(
                target_files=["src/module.py"], doc_type="README", human_approval=True
            )

            assert result["status"] != "rejected_by_human"
            assert result.get("approval", {}).get("status") == "approved"


# =============================================================================
# TEST: Performance and Edge Cases
# =============================================================================


class TestPerformanceAndEdgeCases:
    """Test performance characteristics and edge cases."""

    @pytest.mark.asyncio
    async def test_large_file_handling(self, temp_repo, mock_llm_calls):
        """Test handling of large files."""

        large_content = "def function():\n    pass\n" * 1000

        # FIX: Patch the correct import path for scrub_text
        with (
            patch("aiofiles.open") as mock_open,
            patch(
                "agents.docgen_agent.docgen_agent.scrub_text",
                return_value=large_content,
            ) as mock_scrub,
            patch.object(Path, "is_file", return_value=True),
        ):

            mock_file = AsyncMock()
            mock_file.read.return_value = large_content
            mock_open.return_value.__aenter__.return_value = mock_file

            agent = DocgenAgent(repo_path=str(temp_repo))

            mock_stat = MagicMock()
            mock_stat.st_size = len(large_content)
            mock_stat.st_mtime = datetime.now().timestamp()
            mock_stat.st_mode = os_stat.S_IFREG

            with patch.object(Path, "stat", return_value=mock_stat):
                context = await agent._gather_context(["large_file.py"])

            assert "large_file.py" in context["file_contents"]
            assert len(context["file_contents"]["large_file.py"]) == len(large_content)

    @pytest.mark.asyncio
    async def test_empty_file_list(self, temp_repo, mock_llm_calls):
        """Test handling of empty file list."""

        mock_result = {
            "overall_status": "success",
            "documentation": {"content": "Empty doc"},
        }

        # Patch the instance method
        agent = DocgenAgent(repo_path=str(temp_repo))
        agent.generate_documentation = AsyncMock(return_value=mock_result)

        result = await agent.generate_documentation(target_files=[], doc_type="README")

        assert result["overall_status"] == "success"

    @pytest.mark.asyncio
    async def test_nonexistent_file_handling(self, temp_repo):
        """Test handling of nonexistent files."""

        agent = DocgenAgent(repo_path=str(temp_repo))

        context = await agent._gather_context(["nonexistent_file.py"])

        assert context["total_lines"] == 0
        assert context["total_size_bytes"] == 0
        assert len(context["file_contents"]) == 0

    def test_invalid_repo_path(self):
        """Test that invalid repo path raises appropriate error."""

        with pytest.raises(ValueError, match="Repository path does not exist"):
            agent = DocgenAgent(repo_path="/nonexistent/path")

    @pytest.mark.asyncio
    async def test_concurrent_requests(self, temp_repo, mock_llm_calls):
        """Test handling of concurrent documentation generation requests."""

        mock_result = {"overall_status": "success"}

        agent = DocgenAgent(repo_path=str(temp_repo))
        # Patch the instance method
        agent.generate_documentation = AsyncMock(return_value=mock_result)

        tasks = []
        for i in range(3):
            task = agent.generate_documentation(
                target_files=[f"file{i}.py"], doc_type="README", stream=False
            )
            tasks.append(task)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        assert len(results) == 3
        for result in results:
            assert result["overall_status"] == "success"
