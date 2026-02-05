"""
Tests for the provenance tracking module.
"""

import json
import tempfile
from pathlib import Path

import pytest

from generator.main.provenance import (
    PipelineStage,
    ProvenanceTracker,
    extract_endpoints_from_code,
    run_fail_fast_validation,
    validate_deployment_artifacts,
    validate_docker_compose,
    validate_dockerfile,
    validate_has_content,
    validate_syntax,
)


class TestProvenanceTracker:
    """Test ProvenanceTracker class."""

    def test_init_with_job_id(self):
        tracker = ProvenanceTracker(job_id="test-123")
        assert tracker.job_id == "test-123"
        assert tracker.stages == []
        assert tracker.artifacts == {}

    def test_init_generates_job_id(self):
        tracker = ProvenanceTracker()
        assert tracker.job_id.startswith("job-")

    def test_compute_sha256(self):
        content = "Hello, World!"
        sha256 = ProvenanceTracker.compute_sha256(content)
        # Verify it's a valid SHA256 hex string (64 chars)
        assert len(sha256) == 64
        assert all(c in '0123456789abcdef' for c in sha256)
        # Verify consistency
        assert sha256 == ProvenanceTracker.compute_sha256(content)

    def test_record_stage(self):
        tracker = ProvenanceTracker(job_id="test")
        tracker.record_stage(
            PipelineStage.READ_MD,
            artifacts={"input.md": "# Test"},
            metadata={"source": "test"}
        )
        
        assert len(tracker.stages) == 1
        assert tracker.stages[0]["stage"] == "READ_MD"
        assert "input.md" in tracker.stages[0]["artifacts"]

    def test_record_error(self):
        tracker = ProvenanceTracker(job_id="test")
        tracker.record_error("STAGE1", "TestError", "Test message")
        
        assert len(tracker.errors) == 1
        assert tracker.errors[0]["error_type"] == "TestError"

    def test_artifact_change_detection(self):
        tracker = ProvenanceTracker(job_id="test")
        
        tracker.record_stage("S1", artifacts={"file.py": "v1"})
        tracker.record_stage("S2", artifacts={"file.py": "v1"})
        assert not tracker.check_artifact_changed("file.py")
        
        tracker.record_stage("S3", artifacts={"file.py": "v2"})
        assert tracker.check_artifact_changed("file.py")

    def test_get_overwrites(self):
        tracker = ProvenanceTracker(job_id="test")
        tracker.record_stage("S1", artifacts={"a.py": "v1", "b.py": "stable"})
        tracker.record_stage("S2", artifacts={"a.py": "v2", "b.py": "stable"})
        
        overwrites = tracker.get_artifact_overwrites()
        assert "a.py" in overwrites
        assert "b.py" not in overwrites

    def test_to_dict(self):
        tracker = ProvenanceTracker(job_id="test")
        tracker.record_stage("S1", artifacts={"test.py": "content"})
        
        data = tracker.to_dict()
        assert data["job_id"] == "test"
        assert "stages" in data
        assert "summary" in data

    def test_save_to_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = ProvenanceTracker(job_id="test")
            tracker.record_stage("S1", artifacts={"test.py": "content"})
            
            path = tracker.save_to_file(tmpdir)
            assert Path(path).exists()
            
            with open(path) as f:
                data = json.load(f)
            assert data["job_id"] == "test"


class TestValidateSyntax:
    """Test syntax validation."""

    def test_valid_syntax(self):
        code = "def hello(): return 'world'"
        result = validate_syntax(code, "test.py")
        assert result["valid"] is True

    def test_invalid_syntax(self):
        code = "def hello(:"
        result = validate_syntax(code, "test.py")
        assert result["valid"] is False
        assert result["error"] is not None


class TestValidateHasContent:
    """Test content validation."""

    def test_has_content(self):
        result = validate_has_content("def main(): pass", "main.py")
        assert result["valid"] is True

    def test_empty_content(self):
        result = validate_has_content("", "main.py")
        assert result["valid"] is False


class TestExtractEndpoints:
    """Test endpoint extraction."""

    def test_extract_fastapi_endpoints(self):
        code = '''
@app.get("/users")
def get_users(): pass

@app.post("/users")
def create_user(): pass
'''
        endpoints = extract_endpoints_from_code(code)
        assert len(endpoints) == 2
        assert any(e["path"] == "/users" and e["method"] == "GET" for e in endpoints)
        assert any(e["path"] == "/users" and e["method"] == "POST" for e in endpoints)


class TestRunFailFastValidation:
    """Test fail-fast validation."""

    def test_valid_files(self):
        files = {
            "main.py": "def main(): pass",
            "requirements.txt": "fastapi\nuvicorn"
        }
        result = run_fail_fast_validation(files)
        assert result["valid"] is True

    def test_missing_main(self):
        files = {"requirements.txt": "fastapi"}
        result = run_fail_fast_validation(files)
        assert result["valid"] is False
        assert any("main.py" in e for e in result["errors"])

    def test_syntax_error(self):
        files = {
            "main.py": "def broken(:",
            "requirements.txt": "fastapi"
        }
        result = run_fail_fast_validation(files)
        assert result["valid"] is False


class TestValidateDockerfile:
    """Test Dockerfile validation."""

    def test_valid_dockerfile(self):
        content = "FROM python:3.11\nCMD python main.py"
        result = validate_dockerfile(content)
        assert result["valid"] is True

    def test_missing_from(self):
        content = "CMD python main.py"
        result = validate_dockerfile(content)
        assert result["valid"] is False

    def test_empty(self):
        result = validate_dockerfile("")
        assert result["valid"] is False


class TestValidateDockerCompose:
    """Test docker-compose validation."""

    def test_valid_compose(self):
        content = "services:\n  app:\n    build: ."
        result = validate_docker_compose(content)
        assert result["valid"] is True

    def test_missing_services(self):
        content = "version: '3'"
        result = validate_docker_compose(content)
        assert result["valid"] is False


class TestValidateDeploymentArtifacts:
    """Test deployment validation."""

    def test_valid_deployment(self):
        files = {
            "Dockerfile": "FROM python:3.11\nCMD python main.py",
            "docker-compose.yml": "services:\n  app:\n    build: ."
        }
        result = validate_deployment_artifacts(files)
        assert result["valid"] is True

    def test_invalid_dockerfile(self):
        files = {"Dockerfile": "# no FROM"}
        result = validate_deployment_artifacts(files)
        assert result["valid"] is False
