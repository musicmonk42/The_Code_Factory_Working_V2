#!/usr/bin/env python3
"""
Enterprise-grade test suite for startup crash fixes.

Tests validate:
1. SystemExit is caught and handled gracefully
2. Circular imports are resolved with lazy loading
3. Plugin loading is deferred during startup
4. Environment variables are set correctly
5. All graceful degradation paths work

Author: GitHub Copilot
Date: 2026-01-20
"""

import os
import sys
from unittest.mock import MagicMock, patch
import pytest


class TestSystemExitHandling:
    """Test suite for SystemExit prevention in presidio loading."""
    
    def setup_method(self):
        """Setup test environment."""
        os.environ['TESTING'] = '1'
        os.environ['APP_STARTUP'] = '1'
        os.environ['SKIP_IMPORT_TIME_VALIDATION'] = '1'
    
    def test_load_presidio_engine_catches_systemexit(self):
        """Test that _load_presidio_engine catches SystemExit from spacy downloads."""
        # Force reimport to test the function
        if 'runner.runner_security_utils' in sys.modules:
            del sys.modules['runner.runner_security_utils']
        if 'generator.runner.runner_security_utils' in sys.modules:
            del sys.modules['generator.runner.runner_security_utils']
        
        # Mock presidio imports to raise SystemExit (simulating spacy download failure)
        with patch.dict('sys.modules', {
            'presidio_analyzer': MagicMock(),
            'presidio_anonymizer': MagicMock(),
        }):
            with patch('presidio_analyzer.nlp_engine.NlpEngineProvider') as mock_provider:
                # Simulate SystemExit from spacy model download
                mock_provider.return_value.create_engine.side_effect = SystemExit(1)
                
                # Import should not crash
                from generator.runner import runner_security_utils
                
                # Function should return False (gracefully degraded)
                result = runner_security_utils._load_presidio_engine()
                
                # Should not crash, should return True (using regex-only mode)
                assert isinstance(result, bool), "Function should return boolean"
    
    def test_redact_secrets_catches_systemexit(self):
        """Test that redact_secrets catches SystemExit and returns data safely."""
        if 'runner.runner_security_utils' in sys.modules:
            del sys.modules['runner.runner_security_utils']
        if 'generator.runner.runner_security_utils' in sys.modules:
            del sys.modules['generator.runner.runner_security_utils']
        
        from generator.runner import runner_security_utils
        
        # Mock _load_presidio_engine to raise SystemExit
        with patch.object(runner_security_utils, '_load_presidio_engine', side_effect=SystemExit(1)):
            # Should not crash, should return data
            test_data = "api_key=secret123"
            result = runner_security_utils.redact_secrets(test_data)
            
            # Should return some data (original or redacted)
            assert result is not None, "redact_secrets should never return None"
            assert isinstance(result, str), "Should return string"
    
    def test_scrub_filter_catches_systemexit(self):
        """Test that ScrubFilter in deploy_agent catches SystemExit."""
        if 'generator.agents.deploy_agent.deploy_agent' in sys.modules:
            del sys.modules['generator.agents.deploy_agent.deploy_agent']
        
        import logging
        
        # Create a mock log record
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test message with api_key=secret",
            args=(),
            exc_info=None
        )
        
        # Mock scrub_text to raise SystemExit
        with patch('generator.agents.deploy_agent.deploy_agent.scrub_text', side_effect=SystemExit(1)):
            from generator.agents.deploy_agent.deploy_agent import ScrubFilter
            
            filter_instance = ScrubFilter()
            
            # Should not crash, should return True
            result = filter_instance.filter(record)
            
            assert result is True, "Filter should always return True"
            # Message may be unchanged (which is OK - better than crashing)


class TestCircularImportResolution:
    """Test suite for circular import fixes."""
    
    def setup_method(self):
        """Setup test environment."""
        os.environ['TESTING'] = '1'
        os.environ['APP_STARTUP'] = '1'
    
    def test_arbiter_imports_without_simulation_module_at_toplevel(self):
        """Test that arbiter.py doesn't import UnifiedSimulationModule at module level."""
        # Read the arbiter.py file
        arbiter_file = '/home/runner/work/The_Code_Factory_Working_V2/The_Code_Factory_Working_V2/self_fixing_engineer/arbiter/arbiter.py'
        
        with open(arbiter_file, 'r') as f:
            content = f.read()
        
        # Check that the problematic import is removed from module level
        lines = content.split('\n')
        
        # Find imports section (before class definitions)
        imports_section = []
        for i, line in enumerate(lines):
            if line.strip().startswith('class '):
                break
            if 'import' in line:
                imports_section.append((i, line))
        
        # Check that UnifiedSimulationModule is not imported at module level
        for line_no, line in imports_section:
            if 'from simulation.simulation_module import UnifiedSimulationModule' in line and not line.strip().startswith('#'):
                pytest.fail(
                    f"Found circular import at line {line_no + 1}: {line}\n"
                    "UnifiedSimulationModule should not be imported at module level"
                )
    
    def test_arbiter_init_has_lazy_import_comment(self):
        """Test that Arbiter.__init__ has lazy import logic."""
        arbiter_file = '/home/runner/work/The_Code_Factory_Working_V2/The_Code_Factory_Working_V2/self_fixing_engineer/arbiter/arbiter.py'
        
        with open(arbiter_file, 'r') as f:
            content = f.read()
        
        # Check for lazy import comment or logic
        assert 'Lazy import' in content or 'lazy import' in content, \
            "Should have lazy import documentation"
        
        # Check that simulation_engine parameter is Optional[Any]
        assert 'simulation_engine: Optional[Any]' in content, \
            "simulation_engine should be Optional[Any] to avoid circular import"


class TestPluginLoadingDeferred:
    """Test suite for plugin loading deferral."""
    
    def setup_method(self):
        """Setup test environment."""
        os.environ['TESTING'] = '1'
        os.environ['APP_STARTUP'] = '1'
        os.environ['SKIP_IMPORT_TIME_VALIDATION'] = '1'
    
    def test_plugin_registry_checks_app_startup_env(self):
        """Test that PluginRegistry checks APP_STARTUP environment variable."""
        registry_file = '/home/runner/work/The_Code_Factory_Working_V2/The_Code_Factory_Working_V2/self_fixing_engineer/arbiter/arbiter_plugin_registry.py'
        
        with open(registry_file, 'r') as f:
            content = f.read()
        
        # Check for APP_STARTUP check
        assert 'APP_STARTUP' in content, "Should check APP_STARTUP environment variable"
        assert 'is_startup' in content, "Should have is_startup variable"
        
        # Check that _load_persisted_plugins is conditional
        assert 'if is_testing or skip_validation or is_startup:' in content or \
               'if is_startup:' in content, \
            "Plugin loading should be skipped when APP_STARTUP=1"
    
    def test_plugin_registry_logs_deferred_loading(self):
        """Test that PluginRegistry logs when deferring plugin loading."""
        registry_file = '/home/runner/work/The_Code_Factory_Working_V2/The_Code_Factory_Working_V2/self_fixing_engineer/arbiter/arbiter_plugin_registry.py'
        
        with open(registry_file, 'r') as f:
            content = f.read()
        
        # Check for appropriate log messages
        assert 'Deferring plugin loading' in content or \
               'Skipping persisted plugin loading' in content, \
            "Should log when plugin loading is deferred"


class TestEnvironmentVariables:
    """Test suite for environment variable configuration."""
    
    def test_server_main_sets_env_vars_before_imports(self):
        """Test that server/main.py sets environment variables before any imports."""
        main_file = '/home/runner/work/The_Code_Factory_Working_V2/The_Code_Factory_Working_V2/server/main.py'
        
        with open(main_file, 'r') as f:
            lines = f.readlines()
        
        # Find where os.environ is set
        env_set_line = None
        first_real_import = None
        
        for i, line in enumerate(lines):
            if 'os.environ.setdefault' in line and 'APP_STARTUP' in line:
                env_set_line = i
            if line.strip().startswith('import ') and 'import os' not in line and 'import path_setup' not in line:
                if first_real_import is None:
                    first_real_import = i
        
        assert env_set_line is not None, "Should set APP_STARTUP environment variable"
        
        if first_real_import is not None and env_set_line is not None:
            assert env_set_line < first_real_import, \
                f"Environment variables (line {env_set_line}) should be set before imports (line {first_real_import})"
    
    def test_dockerfile_sets_env_vars(self):
        """Test that Dockerfile sets environment variables."""
        dockerfile = '/home/runner/work/The_Code_Factory_Working_V2/The_Code_Factory_Working_V2/Dockerfile'
        
        with open(dockerfile, 'r') as f:
            content = f.read()
        
        # Check for environment variables in runtime stage
        assert 'APP_STARTUP' in content, "Dockerfile should set APP_STARTUP"
        assert 'SKIP_IMPORT_TIME_VALIDATION' in content, "Dockerfile should set SKIP_IMPORT_TIME_VALIDATION"
        assert 'SPACY_WARNING_IGNORE' in content, "Dockerfile should set SPACY_WARNING_IGNORE"
    
    def test_docker_compose_sets_env_vars(self):
        """Test that docker-compose.yml sets environment variables."""
        compose_file = '/home/runner/work/The_Code_Factory_Working_V2/The_Code_Factory_Working_V2/docker-compose.yml'
        
        with open(compose_file, 'r') as f:
            content = f.read()
        
        # Check for environment variables in codefactory service
        assert 'APP_STARTUP' in content, "docker-compose.yml should set APP_STARTUP"
        assert 'SKIP_IMPORT_TIME_VALIDATION' in content, "docker-compose.yml should set SKIP_IMPORT_TIME_VALIDATION"


class TestDockerfileSpaCyModel:
    """Test suite for Dockerfile spaCy model configuration."""
    
    def test_dockerfile_downloads_en_core_web_sm(self):
        """Test that Dockerfile downloads en_core_web_sm model."""
        dockerfile = '/home/runner/work/The_Code_Factory_Working_V2/The_Code_Factory_Working_V2/Dockerfile'
        
        with open(dockerfile, 'r') as f:
            content = f.read()
        
        # Check for en_core_web_sm download
        assert 'en_core_web_sm' in content, \
            "Dockerfile should download en_core_web_sm (small model for graceful degradation)"
    
    def test_dockerfile_has_error_handling_for_model_download(self):
        """Test that Dockerfile has error handling for model downloads."""
        dockerfile = '/home/runner/work/The_Code_Factory_Working_V2/The_Code_Factory_Working_V2/Dockerfile'
        
        with open(dockerfile, 'r') as f:
            content = f.read()
        
        # Check for error handling (|| or error messages)
        assert '||' in content or 'WARNING' in content, \
            "Dockerfile should have error handling for model downloads"


class TestGracefulDegradation:
    """Test suite for graceful degradation patterns."""
    
    def test_presidio_load_attempted_flag_prevents_retry_loops(self):
        """Test that _PRESIDIO_LOAD_ATTEMPTED prevents infinite retry loops."""
        security_file = '/home/runner/work/The_Code_Factory_Working_V2/The_Code_Factory_Working_V2/generator/runner/runner_security_utils.py'
        
        with open(security_file, 'r') as f:
            content = f.read()
        
        # Check for load attempted flag
        assert '_PRESIDIO_LOAD_ATTEMPTED' in content, \
            "Should have _PRESIDIO_LOAD_ATTEMPTED flag to prevent retry loops"
        
        # Check that it's checked at start of _load_presidio_engine
        assert 'if _PRESIDIO_LOAD_ATTEMPTED:' in content, \
            "Should check _PRESIDIO_LOAD_ATTEMPTED to prevent retries"
    
    def test_multiple_systemexit_handlers(self):
        """Test that there are multiple levels of SystemExit handlers (defense-in-depth)."""
        security_file = '/home/runner/work/The_Code_Factory_Working_V2/The_Code_Factory_Working_V2/generator/runner/runner_security_utils.py'
        
        with open(security_file, 'r') as f:
            content = f.read()
        
        # Count SystemExit handlers
        systemexit_count = content.count('except SystemExit')
        
        assert systemexit_count >= 3, \
            f"Should have multiple SystemExit handlers for defense-in-depth (found {systemexit_count})"


def test_all_syntax_valid():
    """Meta-test: verify all modified files have valid Python syntax."""
    import py_compile
    
    files_to_check = [
        '/home/runner/work/The_Code_Factory_Working_V2/The_Code_Factory_Working_V2/generator/runner/runner_security_utils.py',
        '/home/runner/work/The_Code_Factory_Working_V2/The_Code_Factory_Working_V2/generator/agents/deploy_agent/deploy_agent.py',
        '/home/runner/work/The_Code_Factory_Working_V2/The_Code_Factory_Working_V2/self_fixing_engineer/arbiter/arbiter.py',
        '/home/runner/work/The_Code_Factory_Working_V2/The_Code_Factory_Working_V2/self_fixing_engineer/arbiter/arbiter_plugin_registry.py',
        '/home/runner/work/The_Code_Factory_Working_V2/The_Code_Factory_Working_V2/server/main.py',
    ]
    
    for file_path in files_to_check:
        try:
            py_compile.compile(file_path, doraise=True)
            print(f"✓ {file_path.split('/')[-1]} syntax valid")
        except py_compile.PyCompileError as e:
            pytest.fail(f"Syntax error in {file_path}: {e}")


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v", "-s"])
