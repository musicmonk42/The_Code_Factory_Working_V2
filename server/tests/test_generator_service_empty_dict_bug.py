# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for the empty dict bug fix in GeneratorService agent methods.

These tests validate that the service correctly handles the case where
route_job() returns routed: True but with an empty or missing data dict.
"""

import pytest
from unittest.mock import AsyncMock, Mock

from server.services.generator_service import GeneratorService


@pytest.fixture
def mock_omnicore_service():
    """Create a mock OmniCore service."""
    return Mock()


@pytest.fixture
def generator_service(mock_omnicore_service):
    """Create a GeneratorService with mocked OmniCore."""
    return GeneratorService(omnicore_service=mock_omnicore_service)


class TestEmptyDictBugFix:
    """Test suite for empty dict bug fix in all agent methods."""

    @pytest.mark.asyncio
    async def test_run_full_pipeline_routed_true_empty_data(self, generator_service, mock_omnicore_service):
        """Test run_full_pipeline with routed: True but empty data returns retryable error."""
        mock_omnicore_service.route_job = AsyncMock(return_value={
            "routed": True,
            "data": {}
        })
        
        result = await generator_service.run_full_pipeline(
            job_id="test-job",
            readme_content="test content",
            language="python"
        )
        
        assert result["status"] == "error"
        assert result["retry"] is True
        assert "agents are still loading" in result["message"].lower() or "no data" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_run_full_pipeline_routed_true_with_data(self, generator_service, mock_omnicore_service):
        """Test run_full_pipeline with routed: True and actual data returns data."""
        expected_data = {"status": "success", "result": "pipeline completed"}
        mock_omnicore_service.route_job = AsyncMock(return_value={
            "routed": True,
            "data": expected_data
        })
        
        result = await generator_service.run_full_pipeline(
            job_id="test-job",
            readme_content="test content",
            language="python"
        )
        
        assert result == expected_data

    @pytest.mark.asyncio
    async def test_run_full_pipeline_routed_false(self, generator_service, mock_omnicore_service):
        """Test run_full_pipeline with routed: False returns hard error."""
        mock_omnicore_service.route_job = AsyncMock(return_value={
            "routed": False,
            "error": "routing failed",
            "data": {"status": "error", "message": "routing failed"}
        })
        
        result = await generator_service.run_full_pipeline(
            job_id="test-job",
            readme_content="test content",
            language="python"
        )
        
        assert result["status"] == "error"
        # Should not have retry flag for hard errors
        assert result.get("retry") is not True

    @pytest.mark.asyncio
    async def test_run_full_pipeline_no_omnicore_service(self):
        """Test run_full_pipeline without OmniCore service returns hard error."""
        service = GeneratorService(omnicore_service=None)
        
        result = await service.run_full_pipeline(
            job_id="test-job",
            readme_content="test content",
            language="python"
        )
        
        assert result["status"] == "error"
        assert "unavailable" in result["message"].lower()
        # Should not have retry flag for hard errors
        assert result.get("retry") is not True

    @pytest.mark.asyncio
    async def test_run_codegen_agent_routed_true_empty_data(self, generator_service, mock_omnicore_service):
        """Test run_codegen_agent with routed: True but empty data returns retryable error."""
        mock_omnicore_service.route_job = AsyncMock(return_value={
            "routed": True,
            "data": {}
        })
        
        result = await generator_service.run_codegen_agent(
            job_id="test-job",
            requirements="test requirements",
            language="python"
        )
        
        assert result["status"] == "error"
        assert result["retry"] is True

    @pytest.mark.asyncio
    async def test_run_codegen_agent_routed_true_with_data(self, generator_service, mock_omnicore_service):
        """Test run_codegen_agent with routed: True and actual data returns data."""
        expected_data = {"status": "success", "code": "print('hello')"}
        mock_omnicore_service.route_job = AsyncMock(return_value={
            "routed": True,
            "data": expected_data
        })
        
        result = await generator_service.run_codegen_agent(
            job_id="test-job",
            requirements="test requirements",
            language="python"
        )
        
        assert result == expected_data

    @pytest.mark.asyncio
    async def test_run_testgen_agent_routed_true_empty_data(self, generator_service, mock_omnicore_service):
        """Test run_testgen_agent with routed: True but empty data returns retryable error."""
        mock_omnicore_service.route_job = AsyncMock(return_value={
            "routed": True,
            "data": {}
        })
        
        result = await generator_service.run_testgen_agent(
            job_id="test-job",
            code_path="/path/to/code",
            test_type="unit"
        )
        
        assert result["status"] == "error"
        assert result["retry"] is True

    @pytest.mark.asyncio
    async def test_run_deploy_agent_routed_true_empty_data(self, generator_service, mock_omnicore_service):
        """Test run_deploy_agent with routed: True but empty data returns retryable error."""
        mock_omnicore_service.route_job = AsyncMock(return_value={
            "routed": True,
            "data": {}
        })
        
        result = await generator_service.run_deploy_agent(
            job_id="test-job",
            code_path="/path/to/code",
            platform="docker"
        )
        
        assert result["status"] == "error"
        assert result["retry"] is True

    @pytest.mark.asyncio
    async def test_run_docgen_agent_routed_true_empty_data(self, generator_service, mock_omnicore_service):
        """Test run_docgen_agent with routed: True but empty data returns retryable error."""
        mock_omnicore_service.route_job = AsyncMock(return_value={
            "routed": True,
            "data": {}
        })
        
        result = await generator_service.run_docgen_agent(
            job_id="test-job",
            code_path="/path/to/code",
            doc_type="api",
            format="markdown"
        )
        
        assert result["status"] == "error"
        assert result["retry"] is True

    @pytest.mark.asyncio
    async def test_run_critique_agent_routed_true_empty_data(self, generator_service, mock_omnicore_service):
        """Test run_critique_agent with routed: True but empty data returns retryable error."""
        mock_omnicore_service.route_job = AsyncMock(return_value={
            "routed": True,
            "data": {}
        })
        
        result = await generator_service.run_critique_agent(
            job_id="test-job",
            code_path="/path/to/code",
            scan_types=["security"],
            auto_fix=False
        )
        
        assert result["status"] == "error"
        assert result["retry"] is True

    @pytest.mark.asyncio
    async def test_run_full_pipeline_routed_true_missing_data_key(self, generator_service, mock_omnicore_service):
        """Test run_full_pipeline with routed: True but missing data key (message bus case)."""
        mock_omnicore_service.route_job = AsyncMock(return_value={
            "routed": True,
            "topic": "generator.job_request",
            "message_bus": "ShardedMessageBus"
            # No "data" key at all - happens with message bus routing
        })
        
        result = await generator_service.run_full_pipeline(
            job_id="test-job",
            readme_content="test content",
            language="python"
        )
        
        assert result["status"] == "error"
        assert result["retry"] is True
        assert "agents are still loading" in result["message"].lower() or "no data" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_run_codegen_agent_data_with_error_status(self, generator_service, mock_omnicore_service):
        """Test run_codegen_agent properly returns error status from data."""
        error_data = {"status": "error", "message": "some error occurred"}
        mock_omnicore_service.route_job = AsyncMock(return_value={
            "routed": True,
            "data": error_data
        })
        
        result = await generator_service.run_codegen_agent(
            job_id="test-job",
            requirements="test requirements",
            language="python"
        )
        
        # Should return the error data as-is
        assert result == error_data
        assert result["status"] == "error"
