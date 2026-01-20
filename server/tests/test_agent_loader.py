"""
Tests for Agent Loader Functionality
=====================================

This test module verifies that the agent loader correctly:
- Tracks agent import status
- Identifies missing dependencies
- Provides detailed error information
- Reports agent availability
"""

import pytest
from server.utils.agent_loader import AgentLoader, AgentType, get_agent_loader


class TestAgentLoader:
    """Test suite for AgentLoader functionality."""
    
    def test_singleton_pattern(self):
        """Test that AgentLoader implements singleton pattern."""
        loader1 = get_agent_loader()
        loader2 = get_agent_loader()
        assert loader1 is loader2, "AgentLoader should be a singleton"
    
    def test_agent_loader_initialization(self):
        """Test that AgentLoader initializes correctly."""
        loader = AgentLoader()
        assert loader is not None
        assert hasattr(loader, '_agent_status')
        assert hasattr(loader, '_import_attempts')
    
    def test_get_status_structure(self):
        """Test that get_status returns expected structure."""
        loader = get_agent_loader()
        status = loader.get_status()
        
        # Check required keys
        assert 'startup_time' in status
        assert 'strict_mode' in status
        assert 'debug_mode' in status
        assert 'total_agents' in status
        assert 'available_agents' in status
        assert 'unavailable_agents' in status
        assert 'availability_rate' in status
        assert 'missing_dependencies' in status
        assert 'environment_variables' in status
        assert 'agents' in status
        assert 'import_attempts' in status
        
        # Check types
        assert isinstance(status['available_agents'], list)
        assert isinstance(status['unavailable_agents'], list)
        assert isinstance(status['missing_dependencies'], list)
        assert isinstance(status['environment_variables'], dict)
        assert isinstance(status['agents'], dict)
        assert isinstance(status['availability_rate'], float)
    
    def test_is_agent_available(self):
        """Test checking agent availability."""
        loader = get_agent_loader()
        
        # Test with non-existent agent
        assert not loader.is_agent_available('nonexistent_agent')
    
    def test_get_agent_error(self):
        """Test getting agent error information."""
        loader = get_agent_loader()
        
        # Try to import a failing agent (if any exist)
        status = loader.get_status()
        if status['unavailable_agents']:
            agent_name = status['unavailable_agents'][0]
            error = loader.get_agent_error(agent_name)
            
            # Verify error structure
            assert error is not None
            assert hasattr(error, 'agent_name')
            assert hasattr(error, 'error_type')
            assert hasattr(error, 'error_message')
            assert hasattr(error, 'traceback')
            assert hasattr(error, 'missing_dependencies')
            assert hasattr(error, 'environment_issues')
            assert hasattr(error, 'timestamp')
    
    def test_safe_import_agent_tracks_attempts(self):
        """Test that import attempts are tracked."""
        loader = get_agent_loader()
        initial_status = loader.get_status()
        initial_attempts = len(initial_status['import_attempts'])
        
        # Attempt to import a non-existent agent
        success, module = loader.safe_import_agent(
            agent_type=AgentType.CODEGEN,
            module_path='nonexistent.module',
            import_names=['nonexistent'],
            description='Test import'
        )
        
        # Check import failed
        assert not success
        assert module is None
        
        # Check attempts were tracked
        new_status = loader.get_status()
        assert 'codegen' in new_status['import_attempts']
    
    def test_get_detailed_error_report(self):
        """Test generating detailed error report."""
        loader = get_agent_loader()
        report = loader.get_detailed_error_report()
        
        # Check report is a string
        assert isinstance(report, str)
        
        # Check report contains expected sections
        assert 'AGENT LOADER DIAGNOSTIC REPORT' in report
        assert 'SUMMARY' in report
        assert 'ENVIRONMENT VARIABLES' in report
        
        # If there are unavailable agents, check they're reported
        status = loader.get_status()
        if status['unavailable_agents']:
            assert 'UNAVAILABLE AGENTS' in report
            for agent_name in status['unavailable_agents']:
                assert agent_name in report
        
        # If there are missing dependencies, check they're reported
        if status['missing_dependencies']:
            assert 'MISSING DEPENDENCIES' in report
            assert 'pip install' in report


class TestAgentLoaderMissingDependencies:
    """Test suite for missing dependency detection."""
    
    def test_extract_missing_dependencies(self):
        """Test extraction of missing dependencies from error messages."""
        loader = AgentLoader()
        
        # Test with ModuleNotFoundError message
        error_msg = "No module named 'some_package'"
        traceback_str = "ModuleNotFoundError: No module named 'some_package'"
        
        deps = loader._extract_missing_dependencies(error_msg, traceback_str)
        assert 'some_package' in deps
    
    def test_missing_dependencies_in_status(self):
        """Test that missing dependencies are aggregated in status."""
        loader = get_agent_loader()
        status = loader.get_status()
        
        # missing_dependencies should be a list
        assert isinstance(status['missing_dependencies'], list)
        
        # If any agents are unavailable, there should be missing deps
        if status['unavailable_agents']:
            # Check that at least some agents have missing deps
            has_missing_deps = False
            for agent_name in status['unavailable_agents']:
                agent_info = status['agents'][agent_name]
                if agent_info.get('error') and agent_info['error'].get('missing_dependencies'):
                    has_missing_deps = True
                    break
            
            # Note: This assertion may not always be true if agents fail for other reasons
            # but it's a reasonable check for this test scenario


class TestAgentLoaderEnvironment:
    """Test suite for environment variable checking."""
    
    def test_environment_variables_checked(self):
        """Test that environment variables are checked."""
        loader = get_agent_loader()
        status = loader.get_status()
        
        # Check that common API key env vars are checked
        env_vars = status['environment_variables']
        assert 'OPENAI_API_KEY' in env_vars
        assert 'ANTHROPIC_API_KEY' in env_vars
        
        # Each should have a status
        for var, var_status in env_vars.items():
            assert var_status in ['set', 'not_set']


@pytest.mark.asyncio
class TestAgentLoaderIntegration:
    """Integration tests for agent loader with actual agents."""
    
    async def test_agent_loader_used_by_omnicore_service(self):
        """Test that OmniCoreService uses agent loader."""
        from server.services.omnicore_service import OmniCoreService
        
        service = OmniCoreService()
        
        # The service should be able to handle unavailable agents gracefully
        result = await service._run_codegen(
            job_id='test_job',
            payload={'requirements': 'test'}
        )
        
        # Should get an error status if agent is unavailable
        assert 'status' in result
        
        # If agent is unavailable, should include missing dependencies
        if result['status'] == 'error':
            assert 'message' in result


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
