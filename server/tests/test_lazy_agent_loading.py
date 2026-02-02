"""
Tests for Lazy Agent Loading in OmniCoreService
================================================

This test module verifies that:
- OmniCoreService initializes without loading agents
- Agents are loaded on-demand when first accessed
- No circular import errors occur during initialization
- Agents are only loaded once

Note: These tests require the full application to be installed.
Run with: pytest server/tests/test_lazy_agent_loading.py -v
"""

import pytest
from unittest.mock import patch, MagicMock


class TestLazyAgentLoading:
    """Test suite for lazy agent loading functionality."""
    
    def test_omnicore_service_initializes_without_loading_agents(self):
        """Test that OmniCoreService __init__ doesn't load agents immediately."""
        with patch('server.services.omnicore_service.get_agent_config') as mock_config, \
             patch('server.services.omnicore_service.get_llm_config') as mock_llm_config, \
             patch('server.services.omnicore_service.detect_available_llm_provider') as mock_detect, \
             patch('server.services.omnicore_service.logger') as mock_logger:
            
            mock_config.return_value = None
            mock_llm_config.return_value = None
            mock_detect.return_value = None
            
            # Import after mocking to avoid actual initialization
            from server.services.omnicore_service import OmniCoreService
            
            # Create service instance
            service = OmniCoreService()
            
            # Verify _agents_loaded flag is False
            assert hasattr(service, '_agents_loaded')
            assert service._agents_loaded is False
            
            # Verify the log message for lazy loading was called
            assert any(
                "agents will be loaded on demand" in str(call)
                for call in mock_logger.info.call_args_list
            ), "Expected log message about lazy loading not found"
    
    def test_ensure_agents_loaded_method_exists(self):
        """Test that _ensure_agents_loaded method exists."""
        with patch('server.services.omnicore_service.get_agent_config') as mock_config, \
             patch('server.services.omnicore_service.get_llm_config') as mock_llm_config, \
             patch('server.services.omnicore_service.detect_available_llm_provider') as mock_detect:
            
            mock_config.return_value = None
            mock_llm_config.return_value = None
            mock_detect.return_value = None
            
            from server.services.omnicore_service import OmniCoreService
            
            service = OmniCoreService()
            
            # Verify method exists
            assert hasattr(service, '_ensure_agents_loaded')
            assert callable(service._ensure_agents_loaded)
    
    def test_ensure_agents_loaded_loads_agents_once(self):
        """Test that _ensure_agents_loaded only loads agents once."""
        with patch('server.services.omnicore_service.get_agent_config') as mock_config, \
             patch('server.services.omnicore_service.get_llm_config') as mock_llm_config, \
             patch('server.services.omnicore_service.detect_available_llm_provider') as mock_detect:
            
            mock_config.return_value = None
            mock_llm_config.return_value = None
            mock_detect.return_value = None
            
            from server.services.omnicore_service import OmniCoreService
            
            service = OmniCoreService()
            
            # Mock _load_agents to track calls
            service._load_agents = MagicMock()
            
            # First call should load agents
            service._ensure_agents_loaded()
            assert service._load_agents.call_count == 1
            assert service._agents_loaded is True
            
            # Second call should NOT load agents again
            service._ensure_agents_loaded()
            assert service._load_agents.call_count == 1  # Still 1, not 2
    
    def test_no_circular_import_on_service_creation(self):
        """Test that creating OmniCoreService doesn't cause circular imports."""
        # This test simply tries to import and create the service
        # If there's a circular import, it will fail with ImportError
        try:
            from server.services.omnicore_service import OmniCoreService
            
            # Create service - should not raise circular import error
            with patch('server.services.omnicore_service.get_agent_config'), \
                 patch('server.services.omnicore_service.get_llm_config'), \
                 patch('server.services.omnicore_service.detect_available_llm_provider'):
                service = OmniCoreService()
                assert service is not None
                assert service._agents_loaded is False
        except ImportError as e:
            if "circular import" in str(e).lower():
                pytest.fail(f"Circular import detected: {e}")
            raise


class TestAgentMethodsCallEnsureLoaded:
    """Test that agent methods call _ensure_agents_loaded."""
    
    @pytest.fixture
    def mock_service(self):
        """Create a mocked OmniCoreService for testing."""
        with patch('server.services.omnicore_service.get_agent_config') as mock_config, \
             patch('server.services.omnicore_service.get_llm_config') as mock_llm_config, \
             patch('server.services.omnicore_service.detect_available_llm_provider') as mock_detect:
            
            mock_config.return_value = None
            mock_llm_config.return_value = None
            mock_detect.return_value = None
            
            from server.services.omnicore_service import OmniCoreService
            
            service = OmniCoreService()
            
            # Mock _ensure_agents_loaded to track calls
            service._ensure_agents_loaded = MagicMock()
            
            # Set agents as unavailable to avoid actual execution
            service.agents_available = {
                "codegen": False,
                "testgen": False,
                "deploy": False,
                "docgen": False,
                "critique": False,
                "clarifier": False,
            }
            
            yield service
    
    @pytest.mark.asyncio
    async def test_run_codegen_calls_ensure_agents_loaded(self, mock_service):
        """Test that _run_codegen calls _ensure_agents_loaded."""
        result = await mock_service._run_codegen("test_job", {})
        assert mock_service._ensure_agents_loaded.called
    
    @pytest.mark.asyncio
    async def test_run_testgen_calls_ensure_agents_loaded(self, mock_service):
        """Test that _run_testgen calls _ensure_agents_loaded."""
        result = await mock_service._run_testgen("test_job", {})
        assert mock_service._ensure_agents_loaded.called
    
    @pytest.mark.asyncio
    async def test_run_deploy_calls_ensure_agents_loaded(self, mock_service):
        """Test that _run_deploy calls _ensure_agents_loaded."""
        result = await mock_service._run_deploy("test_job", {})
        assert mock_service._ensure_agents_loaded.called
    
    @pytest.mark.asyncio
    async def test_run_docgen_calls_ensure_agents_loaded(self, mock_service):
        """Test that _run_docgen calls _ensure_agents_loaded."""
        result = await mock_service._run_docgen("test_job", {})
        assert mock_service._ensure_agents_loaded.called
    
    @pytest.mark.asyncio
    async def test_run_critique_calls_ensure_agents_loaded(self, mock_service):
        """Test that _run_critique calls _ensure_agents_loaded."""
        result = await mock_service._run_critique("test_job", {})
        assert mock_service._ensure_agents_loaded.called
    
    @pytest.mark.asyncio
    async def test_run_clarifier_calls_ensure_agents_loaded(self, mock_service):
        """Test that _run_clarifier calls _ensure_agents_loaded."""
        result = await mock_service._run_clarifier("test_job", {"readme_content": "test"})
        assert mock_service._ensure_agents_loaded.called
    
    @pytest.mark.asyncio
    async def test_run_full_pipeline_calls_ensure_agents_loaded(self, mock_service):
        """Test that _run_full_pipeline calls _ensure_agents_loaded."""
        result = await mock_service._run_full_pipeline("test_job", {})
        assert mock_service._ensure_agents_loaded.called


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
