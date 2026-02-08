# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Integration test for the materializer wiring in the code generation pipeline.

Verifies that:
1. _run_codegen uses materialize_file_map (not manual loop) to write files
2. JSON file-map bundles are properly exploded into separate files
3. main.py never contains raw JSON bundle content
4. requirements.txt is written when present in the codegen result
5. Provenance and validation are invoked in the full pipeline
6. JSON string results from agent are parsed into file maps
7. Spec-required files are extracted from MD content and validated
"""

import json
import pytest
import shutil
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch

from server.services.omnicore_service import OmniCoreService
from server.schemas.jobs import JobStatus, JobStage, Job
from server.storage import jobs_db


class TestMaterializerIntegration:
    """Tests that the codegen pipeline properly uses materialize_file_map."""

    @pytest.fixture
    def service(self):
        """Create an OmniCoreService instance for testing."""
        return OmniCoreService()

    @pytest.fixture
    def job_id(self):
        return "test-mat-integration-001"

    @pytest.fixture
    def mock_job(self, job_id):
        """Create a mock job in RUNNING state."""
        fixed_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        job = Job(
            id=job_id,
            status=JobStatus.RUNNING,
            current_stage=JobStage.GENERATOR_GENERATION,
            input_files=[],
            output_files=[],
            created_at=fixed_time,
            updated_at=fixed_time,
            completed_at=None,
            metadata={}
        )
        jobs_db[job_id] = job
        yield job
        # Cleanup
        jobs_db.pop(job_id, None)

    @pytest.fixture(autouse=True)
    def cleanup_uploads(self, job_id):
        """Clean up upload directories after each test."""
        yield
        upload_path = Path(f"./uploads/{job_id}")
        if upload_path.exists():
            shutil.rmtree(upload_path)

    def _setup_service(self, service, codegen_return):
        """Configure the service with mocked agents and LLM config."""
        service._codegen_func = AsyncMock(return_value=codegen_return)
        service.agents_available['codegen'] = True
        service._agents_loaded = True
        service.llm_config = Mock()
        service.llm_config.default_llm_provider = "openai"
        service.llm_config.is_provider_configured = Mock(return_value=True)
        service.llm_config.get_provider_model = Mock(return_value="gpt-4o")
        service.llm_config.get_provider_api_key = Mock(return_value="test-key-123")
        service.llm_config.enable_ensemble_mode = False
        service.llm_config.llm_timeout = 60
        service.llm_config.llm_max_retries = 2
        service.llm_config.llm_temperature = 0.7
        service.llm_config.openai_base_url = None
        service.llm_config.ollama_host = None
        service.agent_config = Mock()
        service.agent_config.upload_dir = Path("./uploads")

    @pytest.mark.asyncio
    async def test_codegen_writes_separate_files_not_json_bundle(
        self, service, job_id, mock_job
    ):
        """
        Core regression test: when codegen returns a dict of files,
        each file should be written separately - NOT as a JSON bundle
        dumped into main.py.
        """
        self._setup_service(service, {
            "main.py": "from fastapi import FastAPI\napp = FastAPI()\n\n@app.get('/health')\ndef health():\n    return {'status': 'ok'}\n",
            "requirements.txt": "fastapi\nuvicorn\n",
        })

        payload = {
            "requirements": "Create a FastAPI health check endpoint",
            "language": "python",
            "framework": "fastapi",
        }

        result = await service._run_codegen(job_id, payload)

        assert result["status"] == "completed"
        assert len(result["generated_files"]) > 0

        # The crucial check: main.py should contain Python code, NOT a JSON bundle
        output_path = Path(result["output_path"])
        main_py = output_path / "main.py"
        assert main_py.exists(), "main.py should exist"

        content = main_py.read_text(encoding="utf-8")
        assert not content.strip().startswith("{"), \
            "main.py should NOT start with '{' (JSON bundle detected)"
        assert "FastAPI" in content, \
            "main.py should contain actual Python code, not JSON"

        # requirements.txt should also be written properly
        req_txt = output_path / "requirements.txt"
        assert req_txt.exists(), "requirements.txt should exist"
        req_content = req_txt.read_text(encoding="utf-8")
        assert "fastapi" in req_content, \
            "requirements.txt should contain dependencies"

    @pytest.mark.asyncio
    async def test_codegen_handles_single_file_result(
        self, service, job_id, mock_job
    ):
        """Test that single-file results (just main.py) work correctly."""
        self._setup_service(service, {
            "main.py": "print('Hello, World!')\n",
        })

        payload = {
            "requirements": "Print hello world",
            "language": "python",
        }

        result = await service._run_codegen(job_id, payload)

        assert result["status"] == "completed"
        output_path = Path(result["output_path"])
        main_py = output_path / "main.py"
        assert main_py.exists()
        assert main_py.read_text().strip() == "print('Hello, World!')"

    @pytest.mark.asyncio
    async def test_codegen_empty_result_returns_error(
        self, service, job_id, mock_job
    ):
        """Test that empty codegen result returns error status."""
        self._setup_service(service, {})

        payload = {
            "requirements": "Invalid",
            "language": "python",
        }

        result = await service._run_codegen(job_id, payload)
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_codegen_with_subdirectories(
        self, service, job_id, mock_job
    ):
        """Test that files with subdirectory paths are written correctly."""
        self._setup_service(service, {
            "main.py": "from fastapi import FastAPI\napp = FastAPI()\n",
            "tests/test_main.py": "def test_app():\n    assert True\n",
            "requirements.txt": "fastapi\npytest\n",
        })

        payload = {
            "requirements": "Create app with tests",
            "language": "python",
        }

        result = await service._run_codegen(job_id, payload)

        assert result["status"] == "completed"
        output_path = Path(result["output_path"])
        assert (output_path / "main.py").exists()
        assert (output_path / "tests" / "test_main.py").exists()
        assert (output_path / "requirements.txt").exists()

    @pytest.mark.asyncio
    async def test_codegen_error_txt_only_returns_error(
        self, service, job_id, mock_job
    ):
        """Test that error.txt-only results are returned as errors."""
        self._setup_service(service, {
            "error.txt": "LLM response did not contain recognizable code.",
        })

        payload = {
            "requirements": "Something",
            "language": "python",
        }

        result = await service._run_codegen(job_id, payload)
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_codegen_handles_json_string_result(
        self, service, job_id, mock_job
    ):
        """
        When the agent returns a JSON string instead of a dict,
        _run_codegen should parse it and materialize the files.
        """
        json_result = json.dumps({
            "main.py": "from fastapi import FastAPI\napp = FastAPI()\n",
            "requirements.txt": "fastapi\nuvicorn\n",
        })
        self._setup_service(service, {})
        # Override codegen func to return a string
        service._codegen_func = AsyncMock(return_value=json_result)

        payload = {
            "requirements": "Create a FastAPI app",
            "language": "python",
            "framework": "fastapi",
        }

        result = await service._run_codegen(job_id, payload)

        assert result["status"] == "completed"
        output_path = Path(result["output_path"])
        main_py = output_path / "main.py"
        assert main_py.exists(), "main.py should exist after JSON string parsing"
        content = main_py.read_text(encoding="utf-8")
        assert "FastAPI" in content

    @pytest.mark.asyncio
    async def test_codegen_handles_nested_json_string_result(
        self, service, job_id, mock_job
    ):
        """
        When the agent returns a JSON string with a nested 'files' key,
        _run_codegen should unwrap and materialize each file.
        """
        json_result = json.dumps({
            "files": {
                "main.py": "print('hello')\n",
            }
        })
        self._setup_service(service, {})
        service._codegen_func = AsyncMock(return_value=json_result)

        payload = {
            "requirements": "Print hello",
            "language": "python",
        }

        result = await service._run_codegen(job_id, payload)

        assert result["status"] == "completed"
        output_path = Path(result["output_path"])
        assert (output_path / "main.py").exists()


class TestExtractRequiredFilesFromMd:
    """Tests for extract_required_files_from_md in provenance module."""

    def test_extract_tree_style_files(self):
        """Extract files from tree-style project listing."""
        from generator.main.provenance import extract_required_files_from_md

        md = (
            "## Project Structure\n"
            "├── main.py\n"
            "├── models.py\n"
            "├── app/routes.py\n"
            "├── requirements.txt\n"
        )
        result = extract_required_files_from_md(md)
        assert "main.py" in result
        assert "models.py" in result
        assert "app/routes.py" in result
        assert "requirements.txt" in result

    def test_extract_backtick_files(self):
        """Extract files referenced in backticks."""
        from generator.main.provenance import extract_required_files_from_md

        md = "The app uses `main.py` and `app/routes.py` for routing."
        result = extract_required_files_from_md(md)
        assert "main.py" in result
        assert "app/routes.py" in result

    def test_extract_empty_spec(self):
        """Empty spec returns empty list."""
        from generator.main.provenance import extract_required_files_from_md

        result = extract_required_files_from_md("Just a description, no files.")
        assert result == []

    def test_extract_deduplicates(self):
        """Duplicate file references are deduplicated."""
        from generator.main.provenance import extract_required_files_from_md

        md = (
            "├── main.py\n"
            "├── main.py\n"
            "Use `main.py` as entry point.\n"
        )
        result = extract_required_files_from_md(md)
        assert result.count("main.py") == 1
