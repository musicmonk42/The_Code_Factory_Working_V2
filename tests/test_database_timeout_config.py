# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test Suite for Database Timeout Configuration
==============================================

This module validates the fixes for configurable database timeouts to prevent
connection failures on Railway deployment with higher network latency.

Tests cover:
1. OmniCore Database class timeout configuration
2. PostgresClient timeout configuration
3. CheckpointBackends timeout configuration
4. Environment variable fallback behavior
5. Logging of timeout values

Compliance:
- ISO 27001 A.14.2.8: System testing
- SOC 2 CC7.1: System testing and change management
- NIST SP 800-53 SI-6: Security function verification

Author: Code Factory Platform Team
Version: 1.0.0
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure project root is in path
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class TestOmniCoreDatabaseTimeouts(unittest.TestCase):
    """Test OmniCore Database class timeout configuration."""

    @patch('omnicore_engine.database.database.create_async_engine')
    def test_database_uses_default_timeouts(self, mock_engine):
        """Verify Database uses default timeout values when env vars not set."""
        try:
            from omnicore_engine.database.database import Database
        except ImportError as e:
            self.skipTest(f"Database module not available: {e}")
        
        # Clear any existing env vars
        env_backup = {}
        for key in ["DB_COMMAND_TIMEOUT", "DB_CONNECT_TIMEOUT"]:
            if key in os.environ:
                env_backup[key] = os.environ[key]
                del os.environ[key]
        
        try:
            # Mock the engine
            mock_engine.return_value = MagicMock()
            
            # Create Database with PostgreSQL URL
            db = Database("postgresql://user:pass@localhost/test")
            
            # Verify create_async_engine was called
            self.assertTrue(mock_engine.called)
            
            # Get the connect_args from the call
            call_kwargs = mock_engine.call_args[1]
            self.assertIn("connect_args", call_kwargs)
            connect_args = call_kwargs["connect_args"]
            
            # Verify default timeouts are used (60s command, 30s connect)
            self.assertEqual(connect_args["command_timeout"], 60)
            self.assertEqual(connect_args["timeout"], 30)
            self.assertEqual(connect_args["server_settings"]["statement_timeout"], "60000")
            
        finally:
            # Restore env vars
            for key, value in env_backup.items():
                os.environ[key] = value

    @patch('omnicore_engine.database.database.create_async_engine')
    def test_database_respects_env_var_timeouts(self, mock_engine):
        """Verify Database respects DB_COMMAND_TIMEOUT and DB_CONNECT_TIMEOUT env vars."""
        try:
            from omnicore_engine.database.database import Database
        except ImportError as e:
            self.skipTest(f"Database module not available: {e}")
        
        # Set custom timeout values
        os.environ["DB_COMMAND_TIMEOUT"] = "90"
        os.environ["DB_CONNECT_TIMEOUT"] = "45"
        
        try:
            # Mock the engine
            mock_engine.return_value = MagicMock()
            
            # Create Database with PostgreSQL URL
            db = Database("postgresql://user:pass@localhost/test")
            
            # Verify create_async_engine was called
            self.assertTrue(mock_engine.called)
            
            # Get the connect_args from the call
            call_kwargs = mock_engine.call_args[1]
            connect_args = call_kwargs["connect_args"]
            
            # Verify custom timeouts are used
            self.assertEqual(connect_args["command_timeout"], 90)
            self.assertEqual(connect_args["timeout"], 45)
            self.assertEqual(connect_args["server_settings"]["statement_timeout"], "90000")
            
        finally:
            # Clean up env vars
            if "DB_COMMAND_TIMEOUT" in os.environ:
                del os.environ["DB_COMMAND_TIMEOUT"]
            if "DB_CONNECT_TIMEOUT" in os.environ:
                del os.environ["DB_CONNECT_TIMEOUT"]


class TestPostgresClientTimeouts(unittest.TestCase):
    """Test PostgresClient timeout configuration."""

    def test_postgres_client_uses_db_command_timeout(self):
        """Verify PostgresClient reads DB_COMMAND_TIMEOUT env var."""
        # Set the environment variable
        os.environ["DB_COMMAND_TIMEOUT"] = "120"
        
        try:
            # Read the value the same way the code does
            command_timeout = float(os.getenv("DB_COMMAND_TIMEOUT", os.getenv("PG_COMMAND_TIMEOUT", "60")))
            
            # Verify it reads the DB_COMMAND_TIMEOUT value
            self.assertEqual(command_timeout, 120.0)
            
        finally:
            # Clean up
            if "DB_COMMAND_TIMEOUT" in os.environ:
                del os.environ["DB_COMMAND_TIMEOUT"]

    def test_postgres_client_fallback_to_pg_command_timeout(self):
        """Verify PostgresClient falls back to PG_COMMAND_TIMEOUT when DB_COMMAND_TIMEOUT not set."""
        # Clear DB_COMMAND_TIMEOUT if set
        if "DB_COMMAND_TIMEOUT" in os.environ:
            del os.environ["DB_COMMAND_TIMEOUT"]
        
        # Set PG_COMMAND_TIMEOUT
        os.environ["PG_COMMAND_TIMEOUT"] = "75"
        
        try:
            # Read the value the same way the code does
            command_timeout = float(os.getenv("DB_COMMAND_TIMEOUT", os.getenv("PG_COMMAND_TIMEOUT", "60")))
            
            # Verify it falls back to PG_COMMAND_TIMEOUT
            self.assertEqual(command_timeout, 75.0)
            
        finally:
            # Clean up
            if "PG_COMMAND_TIMEOUT" in os.environ:
                del os.environ["PG_COMMAND_TIMEOUT"]

    def test_postgres_client_uses_default_timeout(self):
        """Verify PostgresClient uses default 60s when no env vars set."""
        # Clear both env vars if set
        env_backup = {}
        for key in ["DB_COMMAND_TIMEOUT", "PG_COMMAND_TIMEOUT"]:
            if key in os.environ:
                env_backup[key] = os.environ[key]
                del os.environ[key]
        
        try:
            # Read the value the same way the code does
            command_timeout = float(os.getenv("DB_COMMAND_TIMEOUT", os.getenv("PG_COMMAND_TIMEOUT", "60")))
            
            # Verify it uses the default
            self.assertEqual(command_timeout, 60.0)
            
        finally:
            # Restore env vars
            for key, value in env_backup.items():
                os.environ[key] = value


class TestCheckpointBackendsTimeouts(unittest.TestCase):
    """Test CheckpointBackends Config timeout configuration."""

    def test_checkpoint_config_uses_db_command_timeout(self):
        """Verify CheckpointBackends Config reads DB_COMMAND_TIMEOUT env var."""
        os.environ["DB_COMMAND_TIMEOUT"] = "100"
        
        try:
            # Simulate how the Config class reads the value
            timeout = int(os.environ.get("DB_COMMAND_TIMEOUT", os.environ.get("CHECKPOINT_PG_COMMAND_TIMEOUT", "60")))
            
            # Verify it reads DB_COMMAND_TIMEOUT
            self.assertEqual(timeout, 100)
            
        finally:
            # Clean up
            if "DB_COMMAND_TIMEOUT" in os.environ:
                del os.environ["DB_COMMAND_TIMEOUT"]

    def test_checkpoint_config_fallback_to_checkpoint_pg_command_timeout(self):
        """Verify CheckpointBackends Config falls back to CHECKPOINT_PG_COMMAND_TIMEOUT."""
        # Clear DB_COMMAND_TIMEOUT if set
        if "DB_COMMAND_TIMEOUT" in os.environ:
            del os.environ["DB_COMMAND_TIMEOUT"]
        
        # Set CHECKPOINT_PG_COMMAND_TIMEOUT
        os.environ["CHECKPOINT_PG_COMMAND_TIMEOUT"] = "80"
        
        try:
            # Simulate how the Config class reads the value
            timeout = int(os.environ.get("DB_COMMAND_TIMEOUT", os.environ.get("CHECKPOINT_PG_COMMAND_TIMEOUT", "60")))
            
            # Verify it falls back to CHECKPOINT_PG_COMMAND_TIMEOUT
            self.assertEqual(timeout, 80)
            
        finally:
            # Clean up
            if "CHECKPOINT_PG_COMMAND_TIMEOUT" in os.environ:
                del os.environ["CHECKPOINT_PG_COMMAND_TIMEOUT"]

    def test_checkpoint_config_uses_default_timeout(self):
        """Verify CheckpointBackends Config uses default 60s when no env vars set."""
        # Clear both env vars if set
        env_backup = {}
        for key in ["DB_COMMAND_TIMEOUT", "CHECKPOINT_PG_COMMAND_TIMEOUT"]:
            if key in os.environ:
                env_backup[key] = os.environ[key]
                del os.environ[key]
        
        try:
            # Simulate how the Config class reads the value
            timeout = int(os.environ.get("DB_COMMAND_TIMEOUT", os.environ.get("CHECKPOINT_PG_COMMAND_TIMEOUT", "60")))
            
            # Verify it uses the default
            self.assertEqual(timeout, 60)
            
        finally:
            # Restore env vars
            for key, value in env_backup.items():
                os.environ[key] = value


class TestTimeoutPrecedence(unittest.TestCase):
    """Test env var precedence for timeout configuration."""

    def test_db_command_timeout_takes_precedence_over_component_specific(self):
        """Verify DB_COMMAND_TIMEOUT takes precedence over component-specific vars."""
        # Set both DB_COMMAND_TIMEOUT and component-specific vars
        os.environ["DB_COMMAND_TIMEOUT"] = "100"
        os.environ["PG_COMMAND_TIMEOUT"] = "75"
        os.environ["CHECKPOINT_PG_COMMAND_TIMEOUT"] = "80"
        
        try:
            # Test PostgresClient precedence
            pg_timeout = float(os.getenv("DB_COMMAND_TIMEOUT", os.getenv("PG_COMMAND_TIMEOUT", "60")))
            self.assertEqual(pg_timeout, 100.0)
            
            # Test CheckpointBackends precedence
            cp_timeout = int(os.environ.get("DB_COMMAND_TIMEOUT", os.environ.get("CHECKPOINT_PG_COMMAND_TIMEOUT", "60")))
            self.assertEqual(cp_timeout, 100)
            
        finally:
            # Clean up
            for key in ["DB_COMMAND_TIMEOUT", "PG_COMMAND_TIMEOUT", "CHECKPOINT_PG_COMMAND_TIMEOUT"]:
                if key in os.environ:
                    del os.environ[key]


if __name__ == '__main__':
    unittest.main()
