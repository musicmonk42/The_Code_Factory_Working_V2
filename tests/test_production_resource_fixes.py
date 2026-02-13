# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for production resource usage fixes.

Tests for:
1. SFEService singleton pattern
2. Clarifier metrics logging level
3. Arbiter control validation (job_id requirement)
"""

import pytest


class TestSFEServiceSingleton:
    """Test that SFEService uses singleton pattern."""

    def test_sfe_service_singleton_pattern_in_code(self):
        """Test that get_sfe_service implements singleton pattern in source code."""
        with open('/home/runner/work/The_Code_Factory_Working_V2/The_Code_Factory_Working_V2/server/routers/sfe.py', 'r') as f:
            sfe_content = f.read()
        
        # Check for singleton pattern elements
        assert '_sfe_service_instance' in sfe_content
        assert '_sfe_service_lock' in sfe_content
        assert 'global _sfe_service_instance' in sfe_content
        assert 'if _sfe_service_instance is None:' in sfe_content
        assert 'with _sfe_service_lock:' in sfe_content
        
    def test_get_sfe_service_instance_function_exists(self):
        """Test that get_sfe_service_instance function exists in source code."""
        with open('/home/runner/work/The_Code_Factory_Working_V2/The_Code_Factory_Working_V2/server/routers/sfe.py', 'r') as f:
            sfe_content = f.read()
        
        # Check that the function exists
        assert 'def get_sfe_service_instance()' in sfe_content
        assert 'return get_sfe_service()' in sfe_content


class TestClarifierMetricsLogging:
    """Test that Clarifier metrics use debug level logging."""

    def test_clarifier_metrics_uses_debug_level(self):
        """Test that _monitor_metrics uses logger.debug instead of logger.info."""
        # Read the source code to verify logging level
        with open('/home/runner/work/The_Code_Factory_Working_V2/The_Code_Factory_Working_V2/generator/clarifier/clarifier.py', 'r') as f:
            source = f.read()
        
        # Find the _monitor_metrics method
        monitor_pos = source.find('async def _monitor_metrics(self):')
        assert monitor_pos > 0, "_monitor_metrics method not found"
        
        # Get method content (next 1000 chars to cover the whole method)
        method_content = source[monitor_pos:monitor_pos + 1000]
        
        # Should contain logger.debug for metrics monitoring, not logger.info
        assert 'logger.debug' in method_content or 'self.logger.debug' in method_content
        assert 'Metrics monitoring active' in method_content


class TestArbiterJobIdValidation:
    """Test that Arbiter control validates job_id requirement."""

    def test_arbiter_start_requires_job_id_backend(self):
        """Test that backend validates job_id for start command."""
        # This tests the backend validation logic mentioned in the problem statement
        # The actual endpoint validation is in server/routers/sfe.py lines 561-562
        
        # Verify the validation logic exists
        with open('/home/runner/work/The_Code_Factory_Working_V2/The_Code_Factory_Working_V2/server/routers/sfe.py', 'r') as f:
            sfe_content = f.read()
        
        # Check that validation for job_id exists
        assert 'commands_requiring_job' in sfe_content
        assert '"start"' in sfe_content
        assert 'job_id_required' in sfe_content or 'job_id is required' in sfe_content

    def test_arbiter_frontend_includes_sanitize_job_id(self):
        """Test that frontend startArbiter uses sanitizeJobId."""
        with open('/home/runner/work/The_Code_Factory_Working_V2/The_Code_Factory_Working_V2/server/static/js/main.js', 'r') as f:
            main_js = f.read()
        
        # Find the startArbiter function
        start_arbiter_pos = main_js.find('async function startArbiter()')
        assert start_arbiter_pos > 0, "startArbiter function not found"
        
        # Get the function content (next 1000 chars should be enough)
        function_content = main_js[start_arbiter_pos:start_arbiter_pos + 2000]
        
        # Should use prompt to get job ID
        assert 'prompt(' in function_content
        
        # Should use sanitizeJobId
        assert 'sanitizeJobId' in function_content
        
        # Should include job_id in the request
        assert 'job_id' in function_content


class TestFileDiscoveryCache:
    """Test that file discovery uses client-side cache."""

    def test_completed_job_files_cache_exists(self):
        """Test that completedJobFilesCache Map is defined."""
        with open('/home/runner/work/The_Code_Factory_Working_V2/The_Code_Factory_Working_V2/server/static/js/main.js', 'r') as f:
            main_js = f.read()
        
        # Check that cache is defined
        assert 'completedJobFilesCache' in main_js
        assert 'new Map()' in main_js
        
    def test_create_job_card_uses_cache(self):
        """Test that createJobCard checks cache before fetching."""
        with open('/home/runner/work/The_Code_Factory_Working_V2/The_Code_Factory_Working_V2/server/static/js/main.js', 'r') as f:
            main_js = f.read()
        
        # Find createJobCard function
        create_card_pos = main_js.find('async function createJobCard(job)')
        assert create_card_pos > 0, "createJobCard function not found"
        
        # Get function content (next 3000 chars should cover the relevant part)
        function_content = main_js[create_card_pos:create_card_pos + 4000]
        
        # Should check cache
        assert 'completedJobFilesCache.get' in function_content
        
        # Should set cache on successful fetch
        assert 'completedJobFilesCache.set' in function_content
        
        # Should only fetch if not cached
        assert 'cachedData' in function_content
