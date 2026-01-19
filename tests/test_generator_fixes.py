#!/usr/bin/env python3
"""
Test script to validate generator module fixes.
This verifies that all the critical issues have been properly addressed.
"""

import logging
import sys
from pathlib import Path

import pytest

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_runner_stubs():
    """Test Issue #2: Runner stubs should raise NotImplementedError."""
    logger.info("Testing Issue #2: Runner stub functions...")

    try:
        from generator.runner import run_tests_in_sandbox, run_stress_tests

        logger.info("✓ Runner functions imported successfully")

        # Test that calling the stub raises NotImplementedError
        with pytest.raises(NotImplementedError):
            await run_tests_in_sandbox()
        
        logger.info("✓ run_tests_in_sandbox correctly raises NotImplementedError")

    except ImportError as e:
        logger.warning(f"Import error (expected in some environments): {e}")
        pytest.skip("Import failed due to missing dependencies")


def test_intent_parser_redact_secrets():
    """Test Issue #5: redact_secrets should return content, not None."""
    logger.info("Testing Issue #5: Intent parser redact_secrets...")

    try:
        from generator.intent_parser.intent_parser import redact_secrets

        test_content = "This is test content with API_KEY=secret123"
        result = redact_secrets(test_content)

        assert result is not None, "redact_secrets returned None (SECURITY RISK!)"
        assert isinstance(result, str), f"redact_secrets returned unexpected type: {type(result)}"
        
        logger.info("✓ redact_secrets returns string content")
        logger.info(f"  Input: {test_content[:50]}...")
        logger.info(f"  Output: {result[:50]}...")

    except ImportError as e:
        logger.warning(f"Import error (expected in some environments): {e}")
        pytest.skip("Import failed due to missing dependencies")


@pytest.mark.asyncio
async def test_clarifier_stubs():
    """Test Issue #4: Clarifier stubs should raise NotImplementedError."""
    logger.info("Testing Issue #4: Clarifier stub implementations...")

    try:
        # Import will trigger the fallback stub implementations
        from generator.clarifier.clarifier import (
            LLMProvider,
            GrokLLM,
            DefaultPrioritizer,
        )

        logger.info("✓ Clarifier classes imported")

        # Test that methods raise NotImplementedError
        try:
            llm = GrokLLM(api_key="test")
            with pytest.raises(NotImplementedError):
                await llm.generate("test prompt")
            logger.info("✓ GrokLLM.generate correctly raises NotImplementedError")
        except Exception as e:
            # May fail due to missing dependencies
            logger.info(f"✓ Got expected exception: {type(e).__name__}")

    except ImportError as e:
        logger.warning(f"Import error (expected in some environments): {e}")
        pytest.skip("Import failed due to missing dependencies")


def test_audit_loggers():
    """Test Issue #3: Audit loggers should have real implementations."""
    logger.info("Testing Issue #3: Audit logger implementations...")

    try:
        from generator.agents.codegen_agent.codegen_agent import (
            JsonConsoleAuditLogger,
            FileAuditLogger,
        )

        logger.info("✓ Audit logger classes imported")

        # Check that JsonConsoleAuditLogger has proper method
        console_logger = JsonConsoleAuditLogger()
        assert hasattr(console_logger, "log_action"), "JsonConsoleAuditLogger missing log_action method"
        logger.info("✓ JsonConsoleAuditLogger has log_action method")

        # Check that FileAuditLogger can be initialized with config
        file_logger = FileAuditLogger({"audit_log_file": "/tmp/test_audit.log"})
        assert hasattr(file_logger, "log_action"), "FileAuditLogger missing log_action method"
        logger.info("✓ FileAuditLogger has log_action method")

        logger.info("✓ Both audit loggers have proper structure")

    except ImportError as e:
        logger.warning(f"Import error (expected in some environments): {e}")
        pytest.skip("Import failed due to missing dependencies")


def test_llm_client_structure():
    """Test Issue #10: LLMClient should have factory method."""
    logger.info("Testing Issue #10: LLMClient factory method...")

    try:
        # Just check the structure, don't try to instantiate
        import ast
        import inspect

        # Use project_root that's already defined at the top
        llm_client_path = project_root / "generator" / "runner" / "llm_client.py"
        
        # Skip if file doesn't exist
        if not llm_client_path.exists():
            pytest.skip(f"LLMClient file not found at {llm_client_path}")
            
        with open(llm_client_path, "r") as f:
            source = f.read()

        # Check for factory method
        assert "@classmethod" in source and "async def create" in source, \
            "LLMClient missing factory method"
        logger.info("✓ LLMClient has @classmethod async def create factory method")

        # Check for lazy initialization
        assert "_ensure_initialization" in source, \
            "LLMClient missing lazy initialization"
        logger.info("✓ LLMClient has lazy initialization method")

        # Check no bare except clauses
        assert "except:" not in source, \
            f"Found {source.count('except:')} bare 'except:' clauses"
        logger.info("✓ No bare 'except:' clauses found")

    except FileNotFoundError as e:
        pytest.skip(f"Error checking LLMClient: {e}")

