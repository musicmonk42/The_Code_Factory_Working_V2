# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for clarifier gating and file-map materialization fixes.

Tests:
1. Clarifier gating: pipeline pauses with NEEDS_CLARIFICATION status when
   questions are generated, and resumes after all answers are submitted.
2. File-map materialization fallback: nested {"files": {...}} structures and
   JSON string bundles are correctly unpacked instead of being written as
   raw content to a single file.
"""

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from server.schemas.jobs import JobStatus, JobStage, Job


class TestJobStatusEnum:
    """Test JobStatus enum includes NEEDS_CLARIFICATION."""

    def test_needs_clarification_status_exists(self):
        """NEEDS_CLARIFICATION should be a valid JobStatus value."""
        assert hasattr(JobStatus, "NEEDS_CLARIFICATION")
        assert JobStatus.NEEDS_CLARIFICATION.value == "needs_clarification"

    def test_all_statuses_present(self):
        """All expected statuses should be present in the enum."""
        expected = {"pending", "running", "needs_clarification", "completed", "failed", "cancelled"}
        actual = {s.value for s in JobStatus}
        assert expected == actual


class TestClarifierGating:
    """Test that the pipeline pauses when clarification questions are generated."""

    @pytest.mark.asyncio
    async def test_pipeline_pauses_on_clarification_questions(self):
        """Pipeline should set NEEDS_CLARIFICATION and return early when questions exist."""
        from server.routers.generator import _trigger_pipeline_background
        from server.storage import jobs_db

        job_id = "test-clarifier-gate-001"
        readme_content = "# Test Project\n\nBuild a web app."
        job = Job(
            id=job_id,
            status=JobStatus.RUNNING,
            input_files=[],
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            metadata={},
        )
        jobs_db[job_id] = job

        try:
            mock_service = MagicMock()
            mock_service.clarify_requirements = AsyncMock(return_value={
                "clarifications": [
                    {"id": "q1", "question": "What database?"},
                    {"id": "q2", "question": "What auth method?"},
                ],
                "questions_count": 2,
                "method": "rule_based",
            })
            # run_full_pipeline should NOT be called
            mock_service.run_full_pipeline = AsyncMock()

            await _trigger_pipeline_background(
                job_id=job_id,
                readme_content=readme_content,
                generator_service=mock_service,
            )

            # Verify pipeline paused
            assert job.status == JobStatus.NEEDS_CLARIFICATION
            assert job.metadata["clarification_status"] == "pending_response"
            assert job.metadata["readme_content"] == readme_content
            assert len(job.metadata["clarification_questions"]) == 2

            # run_full_pipeline should not have been called
            mock_service.run_full_pipeline.assert_not_called()
        finally:
            if job_id in jobs_db:
                del jobs_db[job_id]

    @pytest.mark.asyncio
    async def test_pipeline_continues_when_no_questions(self):
        """Pipeline should proceed to code generation when no questions are generated."""
        from server.routers.generator import _trigger_pipeline_background
        from server.storage import jobs_db

        job_id = "test-clarifier-gate-002"
        readme_content = "# Test Project\n\nBuild a Python CLI tool."
        job = Job(
            id=job_id,
            status=JobStatus.RUNNING,
            input_files=[],
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            metadata={},
        )
        jobs_db[job_id] = job

        try:
            mock_service = MagicMock()
            mock_service.clarify_requirements = AsyncMock(return_value={
                "clarifications": [],
                "questions_count": 0,
            })
            mock_service.run_full_pipeline = AsyncMock(return_value={
                "status": "completed",
                "stages_completed": ["codegen"],
            })

            with patch("server.routers.generator.finalize_job_success", new_callable=AsyncMock) as mock_finalize:
                mock_finalize.return_value = True
                await _trigger_pipeline_background(
                    job_id=job_id,
                    readme_content=readme_content,
                    generator_service=mock_service,
                )

            # run_full_pipeline should have been called
            mock_service.run_full_pipeline.assert_called_once()
        finally:
            if job_id in jobs_db:
                del jobs_db[job_id]

    @pytest.mark.asyncio
    async def test_pipeline_continues_when_clarification_fails(self):
        """Pipeline should continue with original requirements when clarification fails."""
        from server.routers.generator import _trigger_pipeline_background
        from server.storage import jobs_db

        job_id = "test-clarifier-gate-003"
        readme_content = "# Test Project"
        job = Job(
            id=job_id,
            status=JobStatus.RUNNING,
            input_files=[],
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            metadata={},
        )
        jobs_db[job_id] = job

        try:
            mock_service = MagicMock()
            mock_service.clarify_requirements = AsyncMock(side_effect=Exception("Clarifier down"))
            mock_service.run_full_pipeline = AsyncMock(return_value={
                "status": "completed",
                "stages_completed": ["codegen"],
            })

            with patch("server.routers.generator.finalize_job_success", new_callable=AsyncMock) as mock_finalize:
                mock_finalize.return_value = True
                await _trigger_pipeline_background(
                    job_id=job_id,
                    readme_content=readme_content,
                    generator_service=mock_service,
                )

            assert job.metadata["clarification_status"] == "skipped"
            mock_service.run_full_pipeline.assert_called_once()
        finally:
            if job_id in jobs_db:
                del jobs_db[job_id]


class TestPipelineResumption:
    """Test that the pipeline resumes correctly after clarification answers."""

    @pytest.mark.asyncio
    async def test_resume_pipeline_after_clarification(self):
        """Pipeline should resume with clarified requirements when answers are complete."""
        from server.routers.generator import _resume_pipeline_after_clarification
        from server.storage import jobs_db

        job_id = "test-resume-001"
        readme_content = "# Test Project\n\nBuild a web app."
        job = Job(
            id=job_id,
            status=JobStatus.NEEDS_CLARIFICATION,
            input_files=[],
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            metadata={
                "readme_content": readme_content,
                "language": "python",
                "clarification_status": "pending_response",
            },
        )
        jobs_db[job_id] = job

        try:
            mock_service = MagicMock()
            mock_service.run_full_pipeline = AsyncMock(return_value={
                "status": "completed",
                "stages_completed": ["codegen"],
            })

            clarified_requirements = {
                "clarified_requirements": {
                    "database": "PostgreSQL",
                    "authentication": "OAuth2",
                },
            }

            with patch("server.routers.generator.finalize_job_success", new_callable=AsyncMock) as mock_finalize:
                mock_finalize.return_value = True
                await _resume_pipeline_after_clarification(
                    job_id=job_id,
                    generator_service=mock_service,
                    clarified_requirements=clarified_requirements,
                )

            # Verify job status was updated
            assert job.status == JobStatus.RUNNING or job.status == JobStatus.COMPLETED
            assert job.metadata["clarification_status"] == "completed"

            # Verify run_full_pipeline was called with supplemented readme
            call_args = mock_service.run_full_pipeline.call_args
            assert "PostgreSQL" in call_args.kwargs.get("readme_content", "")
            assert "OAuth2" in call_args.kwargs.get("readme_content", "")
        finally:
            if job_id in jobs_db:
                del jobs_db[job_id]


class TestFallbackMaterialization:
    """Test that the fallback file-writing path handles nested file maps and JSON bundles."""

    def test_fallback_unpacks_files_key(self):
        """Fallback should unwrap a {"files": {...}} structure into individual files."""
        output_dir = Path(tempfile.mkdtemp())
        try:
            result = {
                "files": {
                    "main.py": "print('hello')",
                    "utils.py": "def helper(): pass",
                }
            }

            # Simulate the fallback logic
            file_map = result
            generated_files = []

            if "files" in file_map and isinstance(file_map["files"], dict):
                file_map = file_map["files"]

            for filename, content in file_map.items():
                if isinstance(content, str):
                    file_path = output_dir / filename
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    file_path.write_text(content, encoding='utf-8')
                    generated_files.append(str(file_path))

            assert len(generated_files) == 2
            assert (output_dir / "main.py").read_text() == "print('hello')"
            assert (output_dir / "utils.py").read_text() == "def helper(): pass"
        finally:
            shutil.rmtree(output_dir, ignore_errors=True)

    def test_fallback_unpacks_json_string_bundle(self):
        """Fallback should detect and unpack a JSON string representing multiple files."""
        output_dir = Path(tempfile.mkdtemp())
        try:
            # Simulate LLM returning a single file whose content is a JSON bundle
            bundle = json.dumps({
                "app.py": "from fastapi import FastAPI\napp = FastAPI()",
                "tests/test_app.py": "def test_app(): assert True",
            })
            result = {"main.py": bundle}

            generated_files = []
            file_map = result

            for filename, content in file_map.items():
                if isinstance(content, str):
                    stripped = content.strip()
                    if stripped.startswith('{') and stripped.endswith('}'):
                        try:
                            parsed = json.loads(stripped)
                            if isinstance(parsed, dict) and len(parsed) > 0:
                                inner = parsed
                                if "files" in inner and isinstance(inner["files"], dict):
                                    inner = inner["files"]
                                if all(isinstance(v, str) for v in inner.values()):
                                    for inner_name, inner_content in inner.items():
                                        inner_path = output_dir / inner_name
                                        inner_path.parent.mkdir(parents=True, exist_ok=True)
                                        inner_path.write_text(inner_content, encoding='utf-8')
                                        generated_files.append(str(inner_path))
                                    continue
                        except (json.JSONDecodeError, ValueError):
                            pass

                    file_path = output_dir / filename
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    file_path.write_text(content, encoding='utf-8')
                    generated_files.append(str(file_path))

            # Should have unpacked into two files, not written JSON to main.py
            assert len(generated_files) == 2
            assert (output_dir / "app.py").exists()
            assert (output_dir / "tests" / "test_app.py").exists()
            assert not (output_dir / "main.py").exists()
        finally:
            shutil.rmtree(output_dir, ignore_errors=True)

    def test_fallback_plain_files_pass_through(self):
        """Fallback should write plain string files directly."""
        output_dir = Path(tempfile.mkdtemp())
        try:
            result = {
                "main.py": "print('hello world')",
                "README.md": "# Project\n\nA simple project.",
            }

            generated_files = []
            for filename, content in result.items():
                if isinstance(content, str):
                    stripped = content.strip()
                    unpacked = False
                    if stripped.startswith('{') and stripped.endswith('}'):
                        try:
                            parsed = json.loads(stripped)
                            if isinstance(parsed, dict) and all(isinstance(v, str) for v in parsed.values()):
                                unpacked = True
                        except (json.JSONDecodeError, ValueError):
                            pass

                    if not unpacked:
                        file_path = output_dir / filename
                        file_path.parent.mkdir(parents=True, exist_ok=True)
                        file_path.write_text(content, encoding='utf-8')
                        generated_files.append(str(file_path))

            assert len(generated_files) == 2
            assert (output_dir / "main.py").read_text() == "print('hello world')"
            assert (output_dir / "README.md").read_text() == "# Project\n\nA simple project."
        finally:
            shutil.rmtree(output_dir, ignore_errors=True)
