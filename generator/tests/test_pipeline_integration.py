# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
test_pipeline_integration.py
Code Factory Pipeline Integration Tests

Enterprise-grade integration tests for the Code Factory pipeline validation system.
Tests spec fidelity, provenance tracking, and fail-fast gate behaviors.

Industry Standards Compliance:
    - SOC 2 Type II: Comprehensive test coverage for audit
    - ISO 27001 A.14.2.8: System security testing
    - NIST SP 800-53 SA-11: Developer security testing

Author: Code Factory Team
"""

import json
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import pytest

from generator.main.provenance import (
    PipelineStage,
    ProvenanceTracker,
    extract_endpoints_from_code,
    extract_endpoints_from_md,
    run_fail_fast_validation,
    validate_deployment_artifacts,
    validate_spec_fidelity,
)


# =============================================================================
# TEST DATA FACTORIES
# =============================================================================


@dataclass
class ApiSpecFixture:
    """Test fixture for API specification data."""
    
    markdown_content: str
    expected_route_count: int
    route_definitions: List[Dict[str, str]]


@dataclass  
class GeneratedCodeFixture:
    """Test fixture for generated code artifacts."""
    
    file_contents: Dict[str, str]
    implemented_routes: List[Dict[str, str]]


def create_inventory_api_spec() -> ApiSpecFixture:
    """Factory for inventory management API test spec."""
    spec_md = """
# Inventory Management Service

## Route Definitions

| HTTP Verb | Endpoint Path | Operation |
|-----------|---------------|-----------|
| GET | /api/inventory | List all items |
| POST | /api/inventory | Add new item |
| GET | /api/inventory/{item_id} | Get item details |
| PUT | /api/inventory/{item_id} | Update item |
| DELETE | /api/inventory/{item_id} | Remove item |

## Technical Stack
- Framework: FastAPI 0.100+
- Database: PostgreSQL via SQLAlchemy
"""
    return ApiSpecFixture(
        markdown_content=spec_md,
        expected_route_count=5,
        route_definitions=[
            {"method": "GET", "path": "/api/inventory"},
            {"method": "POST", "path": "/api/inventory"},
            {"method": "GET", "path": "/api/inventory/{item_id}"},
            {"method": "PUT", "path": "/api/inventory/{item_id}"},
            {"method": "DELETE", "path": "/api/inventory/{item_id}"},
        ]
    )


def create_inventory_implementation() -> GeneratedCodeFixture:
    """Factory for complete inventory API implementation."""
    main_module = '''
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field
from typing import List, Optional
from uuid import UUID, uuid4

service = FastAPI(
    title="Inventory Management Service",
    version="1.0.0"
)

class InventoryItem(BaseModel):
    item_id: Optional[UUID] = Field(default_factory=uuid4)
    name: str
    quantity: int = 0
    unit_price: float = 0.0

storage: Dict[UUID, InventoryItem] = {}

@service.get("/api/inventory", response_model=List[InventoryItem])
async def list_inventory():
    return list(storage.values())

@service.post("/api/inventory", response_model=InventoryItem, status_code=status.HTTP_201_CREATED)
async def add_inventory_item(item: InventoryItem):
    storage[item.item_id] = item
    return item

@service.get("/api/inventory/{item_id}", response_model=InventoryItem)
async def get_inventory_item(item_id: UUID):
    if item_id not in storage:
        raise HTTPException(status_code=404, detail="Item not found")
    return storage[item_id]

@service.put("/api/inventory/{item_id}", response_model=InventoryItem)
async def update_inventory_item(item_id: UUID, item: InventoryItem):
    if item_id not in storage:
        raise HTTPException(status_code=404, detail="Item not found")
    storage[item_id] = item
    return item

@service.delete("/api/inventory/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_inventory_item(item_id: UUID):
    if item_id not in storage:
        raise HTTPException(status_code=404, detail="Item not found")
    del storage[item_id]

@service.get("/health")
async def health_probe():
    return {"status": "operational"}
'''
    return GeneratedCodeFixture(
        file_contents={
            "main.py": main_module,
            "requirements.txt": "fastapi>=0.100.0\nuvicorn[standard]\npydantic>=2.0"
        },
        implemented_routes=[
            {"method": "GET", "path": "/api/inventory"},
            {"method": "POST", "path": "/api/inventory"},
            {"method": "GET", "path": "/api/inventory/{item_id}"},
            {"method": "PUT", "path": "/api/inventory/{item_id}"},
            {"method": "DELETE", "path": "/api/inventory/{item_id}"},
        ]
    )


def create_partial_implementation() -> GeneratedCodeFixture:
    """Factory for incomplete API implementation (missing routes)."""
    partial_module = '''
from fastapi import FastAPI
service = FastAPI()

@service.get("/api/inventory")
async def list_inventory():
    return []
'''
    return GeneratedCodeFixture(
        file_contents={
            "main.py": partial_module,
            "requirements.txt": "fastapi"
        },
        implemented_routes=[
            {"method": "GET", "path": "/api/inventory"},
        ]
    )


# =============================================================================
# PYTEST FIXTURES
# =============================================================================


@pytest.fixture
def inventory_spec() -> ApiSpecFixture:
    """Provide inventory API specification fixture."""
    return create_inventory_api_spec()


@pytest.fixture
def complete_implementation() -> GeneratedCodeFixture:
    """Provide complete API implementation fixture."""
    return create_inventory_implementation()


@pytest.fixture
def partial_impl() -> GeneratedCodeFixture:
    """Provide partial implementation fixture."""
    return create_partial_implementation()


@pytest.fixture
def temp_output_dir():
    """Provide temporary directory for test outputs."""
    with tempfile.TemporaryDirectory(prefix="cf_test_") as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def unique_job_id() -> str:
    """Generate unique job identifier for each test."""
    return f"test-job-{uuid.uuid4().hex[:12]}"


# =============================================================================
# SPEC FIDELITY VALIDATION TESTS
# =============================================================================


class TestSpecFidelityValidation:
    """Tests for MD spec to generated code fidelity checking."""

    def test_complete_implementation_passes_validation(
        self, 
        inventory_spec: ApiSpecFixture,
        complete_implementation: GeneratedCodeFixture,
        temp_output_dir: Path
    ):
        """Verify that complete implementation passes spec fidelity check."""
        validation_outcome = validate_spec_fidelity(
            md_content=inventory_spec.markdown_content,
            generated_files=complete_implementation.file_contents,
            output_dir=str(temp_output_dir)
        )
        
        assert validation_outcome["valid"] is True
        assert len(validation_outcome["missing_endpoints"]) == 0
        assert len(validation_outcome["required_endpoints"]) == inventory_spec.expected_route_count

    def test_partial_implementation_fails_validation(
        self,
        inventory_spec: ApiSpecFixture,
        partial_impl: GeneratedCodeFixture,
        temp_output_dir: Path
    ):
        """Verify that incomplete implementation fails spec fidelity check."""
        validation_outcome = validate_spec_fidelity(
            md_content=inventory_spec.markdown_content,
            generated_files=partial_impl.file_contents,
            output_dir=str(temp_output_dir)
        )
        
        assert validation_outcome["valid"] is False
        missing_count = len(validation_outcome["missing_endpoints"])
        expected_missing = inventory_spec.expected_route_count - len(partial_impl.implemented_routes)
        assert missing_count == expected_missing
        
        # Verify structured error file was created
        error_file = temp_output_dir / "error.txt"
        assert error_file.exists()
        error_text = error_file.read_text()
        assert "SPEC FIDELITY VALIDATION FAILED" in error_text
        assert "Missing" in error_text

    def test_empty_spec_passes_validation(self, temp_output_dir: Path):
        """Verify that spec without endpoints passes (nothing to validate)."""
        empty_spec_md = """
# Simple Library
No API endpoints defined here.
Just a utility module.
"""
        code_files = {
            "utils.py": "def helper(): pass",
            "requirements.txt": "numpy"
        }
        
        result = validate_spec_fidelity(
            md_content=empty_spec_md,
            generated_files=code_files,
            output_dir=str(temp_output_dir)
        )
        
        assert result["valid"] is True
        assert len(result["required_endpoints"]) == 0


# =============================================================================
# MD PARSING TESTS
# =============================================================================


class TestMarkdownEndpointExtraction:
    """Tests for endpoint extraction from various MD formats."""

    def test_table_format_extraction(self):
        """Extract endpoints from markdown table notation."""
        table_md = """
| Verb | Path | Notes |
|------|------|-------|
| GET | /api/widgets | Fetch all |
| POST | /api/widgets | Create one |
"""
        extracted = extract_endpoints_from_md(table_md)
        assert len(extracted) == 2
        paths = [e["path"] for e in extracted]
        assert "/api/widgets" in paths

    def test_bullet_list_extraction(self):
        """Extract endpoints from bullet point notation."""
        bullet_md = """
## Available Routes
- GET /api/gadgets
- POST /api/gadgets
- DELETE /api/gadgets/{gid}
"""
        extracted = extract_endpoints_from_md(bullet_md)
        assert len(extracted) == 3
        methods = [e["method"] for e in extracted]
        assert "DELETE" in methods

    def test_backtick_format_extraction(self):
        """Extract endpoints from inline code notation."""
        backtick_md = "Use `GET /api/status` to check health and `POST /api/actions` to trigger."
        extracted = extract_endpoints_from_md(backtick_md)
        assert len(extracted) == 2

    def test_deduplication_of_repeated_endpoints(self):
        """Verify duplicate endpoints are deduplicated."""
        repeated_md = """
- GET /api/items
- GET /api/items
- **GET** /api/items
"""
        extracted = extract_endpoints_from_md(repeated_md)
        assert len(extracted) == 1

    def test_deterministic_ordering(self):
        """Verify endpoints are sorted consistently."""
        unordered_md = """
- DELETE /z/last
- GET /a/first
- POST /m/middle
"""
        extracted = extract_endpoints_from_md(unordered_md)
        # Should be sorted by path
        assert extracted[0]["path"] == "/a/first"
        assert extracted[-1]["path"] == "/z/last"


# =============================================================================
# PROVENANCE TRACKING TESTS
# =============================================================================


class TestProvenanceTracking:
    """Tests for pipeline provenance and audit trail."""

    def test_stage_recording_completeness(self, unique_job_id: str):
        """Verify all pipeline stages can be recorded."""
        tracker = ProvenanceTracker(job_id=unique_job_id)
        
        stage_sequence = [
            (PipelineStage.READ_MD, {"spec.md": "# API Spec"}),
            (PipelineStage.CODEGEN, {"main.py": "import fastapi"}),
            (PipelineStage.VALIDATE, None),
            (PipelineStage.SPEC_VALIDATE, None),
            (PipelineStage.TESTGEN, {"test_main.py": "def test(): pass"}),
            (PipelineStage.DEPLOY_GEN, {"Dockerfile": "FROM python:3.11"}),
            (PipelineStage.PACKAGE, None),
        ]
        
        for stage, artifacts in stage_sequence:
            tracker.record_stage(stage, artifacts=artifacts)
        
        recorded = [entry["stage"] for entry in tracker.stages]
        assert len(recorded) == len(stage_sequence)
        assert "READ_MD" in recorded
        assert "PACKAGE" in recorded

    def test_artifact_hash_integrity(self, unique_job_id: str):
        """Verify SHA256 hashes are computed for artifacts."""
        tracker = ProvenanceTracker(job_id=unique_job_id)
        
        sample_content = "print('hello world')"
        tracker.record_stage(
            PipelineStage.CODEGEN,
            artifacts={"script.py": sample_content}
        )
        
        assert "script.py" in tracker.artifacts
        artifact_history = tracker.artifacts["script.py"]["history"]
        assert len(artifact_history) == 1
        assert "sha256" in artifact_history[0]
        assert len(artifact_history[0]["sha256"]) == 64  # SHA256 hex length

    def test_overwrite_detection(self, unique_job_id: str):
        """Verify artifact modification is detected."""
        tracker = ProvenanceTracker(job_id=unique_job_id)
        
        tracker.record_stage(
            PipelineStage.CODEGEN,
            artifacts={"app.py": "version_1"}
        )
        tracker.record_stage(
            PipelineStage.POSTPROCESS,
            artifacts={"app.py": "version_2_modified"}
        )
        
        assert tracker.check_artifact_changed("app.py") is True
        overwrites = tracker.get_artifact_overwrites()
        assert "app.py" in overwrites

    def test_provenance_file_persistence(
        self, 
        unique_job_id: str,
        temp_output_dir: Path
    ):
        """Verify provenance data is persisted correctly."""
        tracker = ProvenanceTracker(job_id=unique_job_id)
        tracker.record_stage(PipelineStage.PACKAGE, metadata={"final": True})
        
        saved_path = tracker.save_to_file(str(temp_output_dir))
        
        assert Path(saved_path).exists()
        with open(saved_path) as fh:
            persisted = json.load(fh)
        
        assert persisted["job_id"] == unique_job_id
        assert "stages" in persisted
        assert "summary" in persisted


# =============================================================================
# FAIL-FAST VALIDATION TESTS
# =============================================================================


class TestFailFastValidation:
    """Tests for fail-fast validation gates."""

    def test_syntax_error_detection(self):
        """Verify Python syntax errors are caught."""
        broken_code = {
            "broken.py": "def incomplete(",
            "requirements.txt": "flask"
        }
        
        result = run_fail_fast_validation(broken_code)
        
        assert result["valid"] is False
        error_messages = " ".join(result.get("errors", []))
        assert "syntax" in error_messages.lower() or "broken.py" in error_messages

    def test_missing_main_detection(self):
        """Verify missing main.py is detected."""
        no_main = {
            "helpers.py": "def util(): pass",
            "requirements.txt": "requests"
        }
        
        result = run_fail_fast_validation(no_main)
        
        assert result["valid"] is False
        assert any("main.py" in err for err in result.get("errors", []))

    def test_valid_project_passes(
        self,
        inventory_spec: ApiSpecFixture,
        complete_implementation: GeneratedCodeFixture,
        temp_output_dir: Path
    ):
        """Verify valid project passes all gates."""
        result = run_fail_fast_validation(
            generated_files=complete_implementation.file_contents,
            output_dir=str(temp_output_dir),
            md_content=inventory_spec.markdown_content
        )
        
        assert result["valid"] is True


# =============================================================================
# DEPLOYMENT ARTIFACT VALIDATION TESTS
# =============================================================================


class TestDeploymentArtifactValidation:
    """Tests for Docker deployment artifact validation."""

    def test_valid_dockerfile_passes(self):
        """Verify valid Dockerfile passes validation."""
        valid_files = {
            "Dockerfile": "FROM python:3.11-slim\nWORKDIR /app\nCMD python main.py"
        }
        
        result = validate_deployment_artifacts(valid_files)
        assert result["valid"] is True

    def test_missing_from_directive_fails(self):
        """Verify Dockerfile without FROM fails validation."""
        invalid_files = {
            "Dockerfile": "WORKDIR /app\nRUN pip install flask\nCMD python app.py"
        }
        
        result = validate_deployment_artifacts(invalid_files)
        assert result["valid"] is False
        assert any("FROM" in str(err) for err in result.get("errors", []))

    def test_valid_compose_passes(self):
        """Verify valid docker-compose.yml passes validation."""
        valid_files = {
            "Dockerfile": "FROM python:3.11\nCMD python app.py",
            "docker-compose.yml": "version: '3.8'\nservices:\n  web:\n    build: ."
        }
        
        result = validate_deployment_artifacts(valid_files)
        assert result["valid"] is True

    def test_missing_services_section_fails(self):
        """Verify docker-compose without services fails."""
        invalid_files = {
            "Dockerfile": "FROM python:3.11\nCMD python app.py",
            "docker-compose.yml": "version: '3.8'\nnetworks:\n  default:"
        }
        
        result = validate_deployment_artifacts(invalid_files)
        assert result["valid"] is False

