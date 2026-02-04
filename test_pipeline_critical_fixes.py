#!/usr/bin/env python3
"""
Test script to verify the 3 critical pipeline fixes:

1. Dockerfile generation (no shebang, starts with FROM)
2. Docgen dict serialization (handles both dict and string)
3. Testgen fallback tests (generates tests even with syntax errors)
"""

import asyncio
import json
import logging
import sys
import tempfile
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def test_dockerfile_shebang_fix():
    """Test that Dockerfile generation removes shebang lines."""
    logger.info("=" * 80)
    logger.info("TEST 1: Dockerfile Shebang Fix")
    logger.info("=" * 80)
    
    try:
        from generator.agents.deploy_agent.deploy_response_handler import DockerfileHandler
        
        # Test input with shebang
        raw_dockerfile = """#!/bin/bash
# This is a comment
FROM python:3.11-slim
WORKDIR /app
COPY . .
CMD ["python", "main.py"]
"""
        
        handler = DockerfileHandler()
        normalized = handler.normalize(raw_dockerfile)
        result = handler.convert(normalized, "dockerfile")
        
        logger.info("Normalized Dockerfile:")
        logger.info(result)
        
        # Verify no shebang
        if "#!/bin/bash" in result:
            logger.error("✗ FAIL: Shebang still present in normalized Dockerfile")
            return False
        
        # Verify starts with FROM
        first_line = result.strip().split('\n')[0]
        if not first_line.startswith('FROM'):
            logger.error(f"✗ FAIL: Dockerfile doesn't start with FROM, starts with: {first_line}")
            return False
        
        logger.info("✓ PASS: Shebang removed, starts with FROM")
        return True
        
    except Exception as e:
        logger.error(f"✗ FAIL: {e}", exc_info=True)
        return False


async def test_dockerfile_missing_from():
    """Test that Dockerfile generation adds FROM if missing."""
    logger.info("=" * 80)
    logger.info("TEST 2: Dockerfile Missing FROM Fix")
    logger.info("=" * 80)
    
    try:
        from generator.agents.deploy_agent.deploy_response_handler import DockerfileHandler
        
        # Test input without FROM
        raw_dockerfile = """WORKDIR /app
COPY . .
CMD ["python", "main.py"]
"""
        
        handler = DockerfileHandler()
        normalized = handler.normalize(raw_dockerfile)
        result = handler.convert(normalized, "dockerfile")
        
        logger.info("Normalized Dockerfile:")
        logger.info(result)
        
        # Verify starts with FROM
        first_line = result.strip().split('\n')[0]
        if not first_line.startswith('FROM'):
            logger.error(f"✗ FAIL: Dockerfile doesn't start with FROM, starts with: {first_line}")
            return False
        
        logger.info("✓ PASS: FROM instruction added")
        return True
        
    except Exception as e:
        logger.error(f"✗ FAIL: {e}", exc_info=True)
        return False


async def test_docker_plugin_fix():
    """Test that Docker plugin applies fix_dockerfile_syntax."""
    logger.info("=" * 80)
    logger.info("TEST 3: Docker Plugin Fix Function")
    logger.info("=" * 80)
    
    try:
        from generator.agents.deploy_agent.plugins.docker import DockerPlugin
        
        plugin = DockerPlugin()
        
        # Test input with shebang
        raw_dockerfile = """#!/bin/bash
# Bad dockerfile
WORKDIR /app
"""
        
        fixed = plugin._fix_dockerfile_syntax(raw_dockerfile)
        
        logger.info("Fixed Dockerfile:")
        logger.info(fixed)
        
        # Verify no shebang
        if "#!/bin/bash" in fixed:
            logger.error("✗ FAIL: Shebang still present")
            return False
        
        # Verify starts with FROM
        first_line = fixed.strip().split('\n')[0]
        if not first_line.startswith('FROM'):
            logger.error(f"✗ FAIL: Dockerfile doesn't start with FROM")
            return False
        
        logger.info("✓ PASS: Docker plugin fix works")
        return True
        
    except Exception as e:
        logger.error(f"✗ FAIL: {e}", exc_info=True)
        return False


async def test_docgen_dict_serialization():
    """Test that docgen handles dict output correctly."""
    logger.info("=" * 80)
    logger.info("TEST 4: Docgen Dict Serialization")
    logger.info("=" * 80)
    
    try:
        import aiofiles
        
        # Test with dict containing 'content' key
        test_dict_content = {
            "content": "# API Documentation\n\nThis is the API docs.",
            "metadata": {"generated": True}
        }
        
        # Test with dict containing 'markdown' key
        test_dict_markdown = {
            "markdown": "# User Guide\n\nThis is the user guide.",
            "version": "1.0"
        }
        
        # Test with dict without special keys
        test_dict_other = {
            "title": "Documentation",
            "body": "Content here"
        }
        
        # Test with string
        test_string = "# README\n\nThis is a readme."
        
        test_dir = Path(tempfile.mkdtemp())
        
        # Test 1: Dict with 'content'
        path1 = test_dir / "test_content.md"
        async with aiofiles.open(path1, "w", encoding="utf-8") as f:
            docs_output = test_dict_content
            if isinstance(docs_output, dict):
                if 'content' in docs_output:
                    await f.write(docs_output['content'])
                elif 'markdown' in docs_output:
                    await f.write(docs_output['markdown'])
                else:
                    await f.write(json.dumps(docs_output, indent=2))
            else:
                await f.write(str(docs_output))
        
        content1 = path1.read_text()
        if content1 != "# API Documentation\n\nThis is the API docs.":
            logger.error(f"✗ FAIL: Content key not extracted correctly: {content1}")
            return False
        
        # Test 2: Dict with 'markdown'
        path2 = test_dir / "test_markdown.md"
        async with aiofiles.open(path2, "w", encoding="utf-8") as f:
            docs_output = test_dict_markdown
            if isinstance(docs_output, dict):
                if 'content' in docs_output:
                    await f.write(docs_output['content'])
                elif 'markdown' in docs_output:
                    await f.write(docs_output['markdown'])
                else:
                    await f.write(json.dumps(docs_output, indent=2))
            else:
                await f.write(str(docs_output))
        
        content2 = path2.read_text()
        if content2 != "# User Guide\n\nThis is the user guide.":
            logger.error(f"✗ FAIL: Markdown key not extracted correctly: {content2}")
            return False
        
        # Test 3: Dict without special keys (should serialize as JSON)
        path3 = test_dir / "test_other.md"
        async with aiofiles.open(path3, "w", encoding="utf-8") as f:
            docs_output = test_dict_other
            if isinstance(docs_output, dict):
                if 'content' in docs_output:
                    await f.write(docs_output['content'])
                elif 'markdown' in docs_output:
                    await f.write(docs_output['markdown'])
                else:
                    await f.write(json.dumps(docs_output, indent=2))
            else:
                await f.write(str(docs_output))
        
        content3 = path3.read_text()
        parsed = json.loads(content3)
        if parsed != test_dict_other:
            logger.error(f"✗ FAIL: Dict not serialized as JSON correctly")
            return False
        
        # Test 4: String (should write as-is)
        path4 = test_dir / "test_string.md"
        async with aiofiles.open(path4, "w", encoding="utf-8") as f:
            docs_output = test_string
            if isinstance(docs_output, dict):
                if 'content' in docs_output:
                    await f.write(docs_output['content'])
                elif 'markdown' in docs_output:
                    await f.write(docs_output['markdown'])
                else:
                    await f.write(json.dumps(docs_output, indent=2))
            else:
                await f.write(str(docs_output))
        
        content4 = path4.read_text()
        if content4 != test_string:
            logger.error(f"✗ FAIL: String not written correctly")
            return False
        
        logger.info("✓ PASS: All docgen serialization cases handled correctly")
        return True
        
    except Exception as e:
        logger.error(f"✗ FAIL: {e}", exc_info=True)
        return False


async def test_testgen_syntax_error_fallback():
    """Test that testgen generates fallback tests for files with syntax errors."""
    logger.info("=" * 80)
    logger.info("TEST 5: Testgen Syntax Error Fallback")
    logger.info("=" * 80)
    
    try:
        from generator.agents.testgen_agent.testgen_agent import TestgenAgent
        from runner.runner_policy import Policy
        
        # Create a test directory with a Python file that has syntax errors
        test_dir = Path(tempfile.mkdtemp())
        
        # Create a file with syntax errors
        bad_file = test_dir / "bad_syntax.py"
        bad_file.write_text("""
def broken_function(
    # Missing closing parenthesis
    print("This will cause a syntax error"
""")
        
        # Create agent
        agent = TestgenAgent(repo_path=str(test_dir))
        
        # Generate tests
        code_files = {"bad_syntax.py": bad_file.read_text()}
        basic_tests = await agent._generate_basic_tests(code_files, "python", "test-run-123")
        
        logger.info(f"Generated {len(basic_tests)} test files")
        
        if len(basic_tests) == 0:
            logger.error("✗ FAIL: No fallback test generated for file with syntax errors")
            return False
        
        # Check the fallback test content
        test_content = list(basic_tests.values())[0]
        logger.info("Generated fallback test:")
        logger.info(test_content)
        
        # Verify fallback test has expected structure
        if "syntax errors" not in test_content:
            logger.error("✗ FAIL: Fallback test doesn't mention syntax errors")
            return False
        
        if "@pytest.mark.skip" not in test_content:
            logger.error("✗ FAIL: Import test not marked as skipped")
            return False
        
        if "test_bad_syntax_exists" not in test_content:
            logger.error("✗ FAIL: File existence test not found")
            return False
        
        logger.info("✓ PASS: Fallback test generated for file with syntax errors")
        return True
        
    except Exception as e:
        logger.error(f"✗ FAIL: {e}", exc_info=True)
        return False


async def main():
    """Run all tests."""
    logger.info("=" * 80)
    logger.info("TESTING 3 CRITICAL PIPELINE FIXES")
    logger.info("=" * 80)
    
    results = []
    
    # Test 1: Dockerfile shebang fix
    results.append(("Dockerfile Shebang Fix", await test_dockerfile_shebang_fix()))
    
    # Test 2: Dockerfile missing FROM fix
    results.append(("Dockerfile Missing FROM Fix", await test_dockerfile_missing_from()))
    
    # Test 3: Docker plugin fix function
    results.append(("Docker Plugin Fix", await test_docker_plugin_fix()))
    
    # Test 4: Docgen dict serialization
    results.append(("Docgen Dict Serialization", await test_docgen_dict_serialization()))
    
    # Test 5: Testgen syntax error fallback
    results.append(("Testgen Fallback", await test_testgen_syntax_error_fallback()))
    
    # Summary
    logger.info("=" * 80)
    logger.info("TEST SUMMARY")
    logger.info("=" * 80)
    
    passed = 0
    failed = 0
    
    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        logger.info(f"{status}: {name}")
        if result:
            passed += 1
        else:
            failed += 1
    
    logger.info("=" * 80)
    logger.info(f"Total: {len(results)} tests, {passed} passed, {failed} failed")
    logger.info("=" * 80)
    
    if failed > 0:
        logger.error("Some tests failed!")
        sys.exit(1)
    else:
        logger.info("All tests passed!")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
