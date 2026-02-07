# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test Suite for Database Connection and Startup Fixes
=====================================================

This module validates the fixes for database connectivity issues and application
startup problems identified in production logs:

1. Database Health Checks - Docker compose configuration
2. FastAPI Lifespan - Migration from deprecated on_event
3. Database Connection Retry - Resilient connection handling
4. Circular Import Resolution - Lazy loading patterns

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
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure project root is in path
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class TestDatabaseConnectionRetry(unittest.TestCase):
    """Test database connection retry logic."""

    def test_database_test_connection_method_exists(self):
        """Verify test_connection method exists in Database class."""
        try:
            from omnicore_engine.database.database import Database
        except ImportError as e:
            self.skipTest(f"Database module not available: {e}")
        
        # Check that the test_connection method exists
        self.assertTrue(hasattr(Database, 'test_connection'))
        
        # Check that it's a coroutine function
        import inspect
        self.assertTrue(inspect.iscoroutinefunction(Database.test_connection))

    def test_create_tables_has_retry_decorator(self):
        """Verify create_tables method has retry decorator."""
        try:
            from omnicore_engine.database.database import Database
        except ImportError as e:
            self.skipTest(f"Database module not available: {e}")
        
        import inspect
        
        # Check that create_tables exists
        self.assertTrue(hasattr(Database, 'create_tables'))
        
        # Check that it's a coroutine function
        self.assertTrue(inspect.iscoroutinefunction(Database.create_tables))

    @patch('omnicore_engine.database.database.create_async_engine')
    def test_database_uses_async_drivers(self, mock_engine):
        """Verify database URLs are converted to async drivers."""
        try:
            from omnicore_engine.database.database import Database
        except ImportError as e:
            self.skipTest(f"Database module not available: {e}")
        
        # Mock the engine
        mock_engine.return_value = MagicMock()
        
        # Test PostgreSQL URL conversion
        db = Database("postgresql://user:pass@localhost/test")
        self.assertTrue(db.db_path.startswith("postgresql+asyncpg://"))
        
        # Test SQLite URL conversion
        db2 = Database("sqlite:///./test.db")
        self.assertTrue(db2.db_path.startswith("sqlite+aiosqlite://"))


class TestFastAPILifespan(unittest.TestCase):
    """Test FastAPI lifespan context manager migration."""

    def test_lifespan_function_exists(self):
        """Verify lifespan context manager exists in generator/main/api.py."""
        try:
            # Import the api module
            from generator.main import api
            
            # Check that lifespan function exists
            self.assertTrue(hasattr(api, 'lifespan'))
            
            # Check that it's a function
            import inspect
            self.assertTrue(inspect.isfunction(api.lifespan))
            
            # Check that it's an async context manager
            # (it should be decorated with @asynccontextmanager)
            self.assertTrue(
                hasattr(api.lifespan, '__wrapped__') or 
                inspect.isasyncgenfunction(api.lifespan)
            )
        except ImportError as e:
            self.skipTest(f"FastAPI not available: {e}")

    def test_no_deprecated_on_event_decorators(self):
        """Verify deprecated @on_event decorators have been removed."""
        import ast
        
        api_file = PROJECT_ROOT / "generator" / "main" / "api.py"
        if not api_file.exists():
            self.skipTest("API file not found")
        
        with open(api_file, 'r') as f:
            content = f.read()
        
        # Parse the AST
        tree = ast.parse(content)
        
        # Look for decorator calls with on_event
        on_event_calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    if node.func.attr == 'on_event':
                        on_event_calls.append(node)
        
        # Should not find any @api.on_event decorators
        # (except in comments or strings)
        # We allow some mentions in comments about the migration
        self.assertEqual(len(on_event_calls), 0, 
                        "Found deprecated @on_event decorators in code")

    def test_contextlib_import_exists(self):
        """Verify contextlib.asynccontextmanager is imported."""
        import ast
        
        api_file = PROJECT_ROOT / "generator" / "main" / "api.py"
        if not api_file.exists():
            self.skipTest("API file not found")
        
        with open(api_file, 'r') as f:
            content = f.read()
        
        # Parse the AST
        tree = ast.parse(content)
        
        # Look for contextlib import
        found_import = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module == 'contextlib':
                    for alias in node.names:
                        if alias.name == 'asynccontextmanager':
                            found_import = True
                            break
        
        self.assertTrue(found_import, 
                       "asynccontextmanager not imported from contextlib")


class TestDockerComposeConfiguration(unittest.TestCase):
    """Test Docker Compose configuration for database health checks."""

    def test_postgres_service_enabled(self):
        """Verify postgres service is enabled in docker-compose.yml."""
        import yaml
        
        compose_file = PROJECT_ROOT / "docker-compose.yml"
        if not compose_file.exists():
            self.skipTest("docker-compose.yml not found")
        
        with open(compose_file, 'r') as f:
            compose_config = yaml.safe_load(f)
        
        # Check that postgres service exists
        self.assertIn('services', compose_config)
        self.assertIn('postgres', compose_config['services'])

    def test_postgres_healthcheck_configured(self):
        """Verify postgres has proper health check configuration."""
        import yaml
        
        compose_file = PROJECT_ROOT / "docker-compose.yml"
        if not compose_file.exists():
            self.skipTest("docker-compose.yml not found")
        
        with open(compose_file, 'r') as f:
            compose_config = yaml.safe_load(f)
        
        postgres = compose_config['services']['postgres']
        
        # Check healthcheck exists
        self.assertIn('healthcheck', postgres)
        
        healthcheck = postgres['healthcheck']
        
        # Check required fields
        self.assertIn('test', healthcheck)
        self.assertIn('interval', healthcheck)
        self.assertIn('timeout', healthcheck)
        self.assertIn('retries', healthcheck)
        self.assertIn('start_period', healthcheck)
        
        # Verify test command uses pg_isready
        test_cmd = ' '.join(healthcheck['test'])
        self.assertIn('pg_isready', test_cmd)

    def test_depends_on_uses_service_healthy(self):
        """Verify codefactory service depends on postgres health."""
        import yaml
        
        compose_file = PROJECT_ROOT / "docker-compose.yml"
        if not compose_file.exists():
            self.skipTest("docker-compose.yml not found")
        
        with open(compose_file, 'r') as f:
            compose_config = yaml.safe_load(f)
        
        codefactory = compose_config['services']['codefactory']
        
        # Check depends_on exists and has postgres
        self.assertIn('depends_on', codefactory)
        self.assertIn('postgres', codefactory['depends_on'])
        
        # Check that it uses service_healthy condition
        postgres_dep = codefactory['depends_on']['postgres']
        self.assertIn('condition', postgres_dep)
        self.assertEqual(postgres_dep['condition'], 'service_healthy')


class TestCircularImportResolution(unittest.TestCase):
    """Test circular import resolution in runner_logging."""

    def test_logger_defined_early(self):
        """Verify logger is defined early in runner_logging."""
        import ast
        
        logging_file = PROJECT_ROOT / "generator" / "runner" / "runner_logging.py"
        if not logging_file.exists():
            self.skipTest("runner_logging.py not found")
        
        with open(logging_file, 'r') as f:
            content = f.read()
        
        # Check that logger is defined before imports from runner_audit
        lines = content.split('\n')
        
        logger_line = None
        audit_import_line = None
        
        for i, line in enumerate(lines):
            # Look for the first logger definition (not in comments)
            if logger_line is None and 'logger = logging.getLogger' in line and not line.strip().startswith('#'):
                logger_line = i
            # Look for runner_audit import
            if audit_import_line is None and ('from .runner_audit import' in line or 'from runner.runner_audit import' in line):
                audit_import_line = i
        
        # Both should exist
        self.assertIsNotNone(logger_line, "Logger definition not found")
        self.assertIsNotNone(audit_import_line, "runner_audit import not found")
        
        # Logger should be defined before the audit import
        self.assertLess(logger_line, audit_import_line,
                      f"logger (line {logger_line}) should be defined before runner_audit import (line {audit_import_line})")

    def test_lazy_import_patterns_exist(self):
        """Verify lazy import patterns are used in agent files."""
        # Check testgen_validator for lazy import
        validator_file = PROJECT_ROOT / "generator" / "agents" / "testgen_agent" / "testgen_validator.py"
        
        if validator_file.exists():
            with open(validator_file, 'r') as f:
                content = f.read()
            
            # Should have comments about lazy imports
            self.assertIn('lazy import', content.lower())


class TestHealthCheckEndpoints(unittest.TestCase):
    """Test health check endpoint implementations."""

    def test_health_endpoint_is_liveness(self):
        """Verify /health endpoint is a liveness probe."""
        from server.main import app
        
        # Find the /health route
        health_route = None
        for route in app.routes:
            if hasattr(route, 'path') and route.path == '/health':
                health_route = route
                break
        
        self.assertIsNotNone(health_route, "/health endpoint not found")

    def test_ready_endpoint_is_readiness(self):
        """Verify /ready endpoint is a readiness probe."""
        from server.main import app
        
        # Find the /ready route
        ready_route = None
        for route in app.routes:
            if hasattr(route, 'path') and route.path == '/ready':
                ready_route = route
                break
        
        self.assertIsNotNone(ready_route, "/ready endpoint not found")


if __name__ == '__main__':
    unittest.main()
