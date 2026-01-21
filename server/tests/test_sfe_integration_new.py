"""
Integration tests for SFE service with actual module connections.

Tests the integration between server and Self-Fixing Engineer components:
- Codebase analyzer connectivity
- Bug manager integration
- Arbiter for fix proposal/application
- Checkpoint manager for rollback
- Graceful degradation when components unavailable
"""

import pytest
from unittest.mock import Mock, patch, AsyncMock
from pathlib import Path
import tempfile

# Import the service
from server.services.sfe_service import SFEService


class TestSFEServiceIntegration:
    """Test suite for SFE service integration."""

    def test_service_initialization(self):
        """Test that SFEService initializes without errors."""
        service = SFEService()
        assert service is not None
        assert hasattr(service, '_sfe_components')
        assert hasattr(service, '_sfe_available')

    def test_component_availability_tracking(self):
        """Test that component availability is properly tracked."""
        service = SFEService()
        
        # Check that availability dict exists
        assert isinstance(service._sfe_available, dict)
        
        # Check expected keys
        expected_keys = ["codebase_analyzer", "bug_manager", "arbiter", "checkpoint", "mesh_metrics"]
        for key in expected_keys:
            assert key in service._sfe_available
            assert isinstance(service._sfe_available[key], bool)

    @pytest.mark.asyncio
    async def test_analyze_code_with_omnicore(self):
        """Test code analysis routing through OmniCore."""
        # Mock OmniCore service
        mock_omnicore = AsyncMock()
        mock_omnicore.route_job = AsyncMock(return_value={
            "data": {
                "job_id": "test-123",
                "issues_found": 2
            }
        })
        
        service = SFEService(omnicore_service=mock_omnicore)
        
        # Test analysis
        result = await service.analyze_code("test-123", "/path/to/code")
        
        assert "job_id" in result
        assert result["job_id"] == "test-123"
        
        # Verify OmniCore was called
        mock_omnicore.route_job.assert_called_once()

    @pytest.mark.asyncio
    async def test_analyze_code_with_actual_file(self):
        """Test code analysis with actual file."""
        service = SFEService()
        
        # Create temporary Python file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write("""
# Test file
def test_function():
    # TODO: Implement this
    pass
""")
            temp_path = f.name
        
        try:
            # Test analysis (will use fallback if analyzer not available)
            result = await service.analyze_code("test-123", temp_path)
            
            assert "job_id" in result
            assert result["job_id"] == "test-123"
            assert "code_path" in result
            
        finally:
            # Clean up
            Path(temp_path).unlink()

    @pytest.mark.asyncio
    async def test_analyze_code_fallback(self):
        """Test code analysis falls back when components unavailable."""
        service = SFEService()
        
        # Ensure no OmniCore service and no analyzer
        service.omnicore_service = None
        service._sfe_available["codebase_analyzer"] = False
        
        # Test analysis
        result = await service.analyze_code("test-123", "/path/to/code")
        
        assert "job_id" in result
        assert result["source"] == "fallback"

    @pytest.mark.asyncio
    async def test_detect_errors_with_omnicore(self):
        """Test error detection routing through OmniCore."""
        # Mock OmniCore service
        mock_omnicore = AsyncMock()
        mock_omnicore.route_job = AsyncMock(return_value={
            "data": [{"error_id": "err-001", "severity": "high"}]
        })
        
        service = SFEService(omnicore_service=mock_omnicore)
        
        # Test detection
        errors = await service.detect_errors("test-123")
        
        assert isinstance(errors, list)
        if errors:
            assert "error_id" in errors[0]

    @pytest.mark.asyncio
    async def test_detect_errors_fallback(self):
        """Test error detection falls back when OmniCore unavailable."""
        service = SFEService()
        service.omnicore_service = None
        
        # Test detection
        errors = await service.detect_errors("test-123")
        
        assert isinstance(errors, list)
        assert len(errors) > 0

    @pytest.mark.asyncio
    async def test_propose_fix(self):
        """Test fix proposal."""
        service = SFEService()
        
        # Test proposal
        fix = await service.propose_fix("err-001")
        
        assert "fix_id" in fix
        assert "error_id" in fix
        assert fix["error_id"] == "err-001"
        assert "confidence" in fix

    @pytest.mark.asyncio
    async def test_apply_fix(self):
        """Test fix application."""
        service = SFEService()
        
        # Test application (dry run)
        result = await service.apply_fix("fix-001", dry_run=True)
        
        assert "fix_id" in result
        assert result["fix_id"] == "fix-001"
        assert result["dry_run"] is True
        assert result["applied"] is False
        
        # Test actual application
        result = await service.apply_fix("fix-001", dry_run=False)
        
        assert result["applied"] is True
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_rollback_fix(self):
        """Test fix rollback."""
        service = SFEService()
        
        # Test rollback
        result = await service.rollback_fix("fix-001")
        
        assert "fix_id" in result
        assert result["fix_id"] == "fix-001"
        assert result["rolled_back"] is True
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_get_sfe_metrics(self):
        """Test SFE metrics retrieval."""
        service = SFEService()
        
        # Get metrics
        metrics = await service.get_sfe_metrics("test-123")
        
        assert "job_id" in metrics
        assert metrics["job_id"] == "test-123"
        assert "source" in metrics

    @pytest.mark.asyncio
    async def test_get_learning_insights_with_omnicore(self):
        """Test learning insights routing through OmniCore."""
        # Mock OmniCore service
        mock_omnicore = AsyncMock()
        mock_omnicore.route_job = AsyncMock(return_value={
            "data": {"total_fixes": 100, "success_rate": 0.85}
        })
        
        service = SFEService(omnicore_service=mock_omnicore)
        
        # Test insights
        insights = await service.get_learning_insights("test-123")
        
        assert "total_fixes" in insights or "job_id" in insights

    @pytest.mark.asyncio
    async def test_get_learning_insights_fallback(self):
        """Test learning insights falls back when OmniCore unavailable."""
        service = SFEService()
        service.omnicore_service = None
        
        # Test insights
        insights = await service.get_learning_insights()
        
        assert "total_fixes" in insights
        assert "success_rate" in insights


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
