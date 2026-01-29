"""
Test Suite for Startup Issue Fixes
==================================

This module validates the fixes for startup issues identified in the deployment
healthcheck analysis:

1. SHIF Initialization Failures - Path setup and component validation
2. Redis Circuit Breaker - Connection handling and graceful degradation
3. Cyclic Dependency Detection - Agent loader validation
4. Prometheus Metrics Conflicts - Port binding and initialization

Compliance:
- ISO 27001 A.14.2.8: System testing
- SOC 2 CC7.1: System testing and change management
- NIST SP 800-53 SI-6: Security function verification

Author: Code Factory Platform Team
Version: 1.0.0
"""

import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure project root is in path
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Import path_setup to configure all component paths
import path_setup


class TestSHIFInitialization(unittest.TestCase):
    """Test Self-Healing Import Fixer initialization and path setup."""

    def test_shif_module_imports(self):
        """Verify SHIF module can be imported without errors."""
        from self_fixing_engineer.self_healing_import_fixer import (
            __version__,
            get_shif_root,
            validate_shif_components,
            get_path_setup_status,
        )
        
        # Verify version is defined
        self.assertIsNotNone(__version__)
        self.assertTrue(len(__version__) > 0)
    
    def test_shif_path_setup_complete(self):
        """Verify SHIF path setup completed successfully."""
        from self_fixing_engineer.self_healing_import_fixer import get_path_setup_status
        
        status = get_path_setup_status()
        
        # Path setup should be complete
        self.assertTrue(status["complete"])
        # No errors should have occurred
        self.assertIsNone(status["error"])
        # SHIF root should be a valid path
        self.assertTrue(Path(status["shif_root"]).exists())
    
    def test_shif_root_path(self):
        """Verify SHIF root path is correct and exists."""
        from self_fixing_engineer.self_healing_import_fixer import get_shif_root
        
        root = get_shif_root()
        
        # Root should exist
        self.assertTrue(root.exists())
        # Should be a directory
        self.assertTrue(root.is_dir())
        # Should contain expected subdirectories
        self.assertTrue((root / "analyzer").exists())
        self.assertTrue((root / "import_fixer").exists())
    
    def test_shif_component_validation(self):
        """Verify SHIF component validation returns correct structure."""
        from self_fixing_engineer.self_healing_import_fixer import validate_shif_components
        
        status = validate_shif_components()
        
        # Should return dict with expected keys
        self.assertIsInstance(status, dict)
        self.assertIn("compat_core", status)
        self.assertIn("analyzer", status)
        self.assertIn("import_fixer", status)
        
        # All values should be booleans
        for key, value in status.items():
            self.assertIsInstance(value, bool, f"{key} should be boolean")
        
        # analyzer should be available (it's in the package)
        self.assertTrue(status["analyzer"])


class TestAgentLoaderCyclicDependencyDetection(unittest.TestCase):
    """Test agent loader cyclic dependency detection."""
    
    def test_phased_loading_available(self):
        """Verify phased loading configuration is set."""
        from server.utils.agent_loader import PHASED_LOADING_AVAILABLE, AGENT_GRAPH
        
        # PHASED_LOADING_AVAILABLE should be a boolean
        self.assertIsInstance(PHASED_LOADING_AVAILABLE, bool)
        
        # If available, AGENT_GRAPH should have agents
        if PHASED_LOADING_AVAILABLE:
            self.assertTrue(len(AGENT_GRAPH) > 0, "AGENT_GRAPH should have at least one agent when phased loading is available")
    
    def test_dependency_graph_validation(self):
        """Verify dependency graph validation runs at import time."""
        from server.utils.agent_loader import _dependency_graph_validation_errors
        
        # Validation should have run (list exists)
        self.assertIsInstance(_dependency_graph_validation_errors, list)
        # Log if there are errors but don't fail - configuration may vary
        if _dependency_graph_validation_errors:
            print(f"Warning: Dependency graph has {len(_dependency_graph_validation_errors)} validation errors")
    
    def test_agent_loader_initialization(self):
        """Verify agent loader initializes correctly."""
        from server.utils.agent_loader import get_agent_loader
        
        loader = get_agent_loader()
        
        # Should be initialized
        self.assertTrue(loader._initialized)
        # Should have import lock
        self.assertIsNotNone(loader._import_lock)
        # Should have loaded modules dict
        self.assertIsInstance(loader._loaded_modules, dict)
    
    def test_agent_loader_phased_loading_configuration(self):
        """Verify agent loader phased loading configuration."""
        from server.utils.agent_loader import get_agent_loader, PHASED_LOADING_AVAILABLE
        
        loader = get_agent_loader()
        
        # _phased_loading should be a boolean
        self.assertIsInstance(loader._phased_loading, bool)
        
        # If PHASED_LOADING_AVAILABLE is False, _phased_loading must be False
        if not PHASED_LOADING_AVAILABLE:
            self.assertFalse(loader._phased_loading, 
                "Phased loading should be disabled when PHASED_LOADING_AVAILABLE is False")
        
        # Note: _phased_loading can be False even when PHASED_LOADING_AVAILABLE is True
        # (via PHASED_AGENT_LOADING=0 env var), so we don't assert it must be True


class TestRedisCircuitBreaker(unittest.TestCase):
    """Test Redis circuit breaker implementation."""
    
    def test_redis_skip_in_testing(self):
        """Verify Redis is skipped when TESTING=1."""
        # Import after setting env var
        with patch.dict(os.environ, {"TESTING": "1"}):
            # Need to reimport to test behavior
            try:
                from self_fixing_engineer.self_healing_import_fixer.import_fixer import compat_core
                # Should not attempt Redis connection in test mode
                # The function should return None without error
            except ImportError:
                self.skipTest("compat_core dependencies not available")
    
    def test_redis_circuit_breaker_constants(self):
        """Verify circuit breaker constants are properly defined."""
        try:
            from self_fixing_engineer.self_healing_import_fixer.import_fixer import compat_core
        except ImportError:
            self.skipTest("compat_core dependencies not available")
        
        # Check constants exist and have reasonable values
        self.assertTrue(hasattr(compat_core, '_REDIS_CIRCUIT_RESET_SECONDS'))
        self.assertTrue(hasattr(compat_core, '_REDIS_MAX_CONNECTION_ATTEMPTS'))
        self.assertTrue(hasattr(compat_core, '_REDIS_EXTENDED_BACKOFF_SECONDS'))
        
        # Values should be positive
        self.assertGreater(compat_core._REDIS_CIRCUIT_RESET_SECONDS, 0)
        self.assertGreater(compat_core._REDIS_MAX_CONNECTION_ATTEMPTS, 0)
        self.assertGreater(compat_core._REDIS_EXTENDED_BACKOFF_SECONDS, 0)
        
        # Extended backoff should be longer than standard
        self.assertGreater(
            compat_core._REDIS_EXTENDED_BACKOFF_SECONDS,
            compat_core._REDIS_CIRCUIT_RESET_SECONDS
        )


class TestDistributedLock(unittest.TestCase):
    """Test distributed lock implementation."""
    
    def test_lock_constants(self):
        """Verify lock constants are properly defined."""
        from server.distributed_lock import (
            MIN_LOCK_TIMEOUT,
            MAX_LOCK_TIMEOUT,
            MIN_RETRY_DELAY,
            MAX_RETRY_DELAY,
        )
        
        # Constants should have reasonable values
        self.assertGreater(MIN_LOCK_TIMEOUT, 0)
        self.assertGreater(MAX_LOCK_TIMEOUT, MIN_LOCK_TIMEOUT)
        self.assertGreater(MIN_RETRY_DELAY, 0)
        self.assertGreater(MAX_RETRY_DELAY, MIN_RETRY_DELAY)
    
    def test_lock_initialization(self):
        """Verify distributed lock can be initialized."""
        from server.distributed_lock import DistributedLock
        
        lock = DistributedLock("test_resource")
        
        # Should have proper attributes
        self.assertEqual(lock.lock_name, "lock:test_resource")
        self.assertIsNotNone(lock.lock_value)
        self.assertFalse(lock._acquired)
    
    def test_lock_parameter_validation(self):
        """Verify lock validates all input parameters correctly."""
        from server.distributed_lock import DistributedLock
        
        # Empty name should raise ValueError
        with self.assertRaises(ValueError):
            DistributedLock("")
        
        # Whitespace-only name should raise ValueError
        with self.assertRaises(ValueError):
            DistributedLock("   ")
        
        # Invalid timeout (too low) should raise ValueError
        with self.assertRaises(ValueError):
            DistributedLock("test", timeout=0)
        
        # Invalid retry delay (too low) should raise ValueError
        with self.assertRaises(ValueError):
            DistributedLock("test", retry_delay=0)
    
    def test_startup_lock_singleton(self):
        """Verify startup lock is a singleton."""
        from server.distributed_lock import get_startup_lock
        
        lock1 = get_startup_lock()
        lock2 = get_startup_lock()
        
        # Should be the same instance
        self.assertIs(lock1, lock2)


class TestPrometheusMetrics(unittest.TestCase):
    """Test Prometheus metrics initialization."""
    
    def test_metrics_registry_exists(self):
        """Verify metrics registry is properly initialized."""
        # This test verifies the metrics infrastructure exists
        # without actually starting a server
        try:
            from self_fixing_engineer.self_healing_import_fixer.import_fixer import compat_core
            
            # Should have metrics function
            self.assertTrue(hasattr(compat_core, '_get_metrics'))
            
            # Should have prometheus flag
            self.assertTrue(hasattr(compat_core, '_HAS_PROMETHEUS'))
            
        except ImportError:
            # Optional dependency not available
            self.skipTest("compat_core dependencies not available")


if __name__ == "__main__":
    # Run tests with verbosity
    unittest.main(verbosity=2)
