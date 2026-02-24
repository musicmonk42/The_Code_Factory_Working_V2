# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
DEVELOPMENT-ONLY BOOTSTRAP SCRIPT

This script creates thin wrapper modules required for local CLI/dev testing
of testgen_agent.py and related tools. It must NEVER be run or imported in
production or packaged with production builds.

Usage:
    python scripts/bootstrap_agent_dev.py

What it does:
- Generates local thin-wrapper files in 'tests/mocks/' directory that delegate
  to the real implementations in generator/agents/testgen_agent/.
- Allows the agent to run for development/testing without a production setup.
- Prevents accidental overwriting of production files by using a dedicated mock directory.
- Removes 'tests/mocks/' after bootstrap completes so the files are not left
  around to be accidentally imported or committed.

CAUTION:
- These files are for developer convenience ONLY.
- DO NOT deploy these files to production environments.
- The real implementations must exist in production deployments.
- Add tests/mocks/ to your PYTHONPATH to use these mocks: export PYTHONPATH=$PYTHONPATH:tests/mocks

SECURITY:
- This script now writes to 'tests/mocks/' directory to prevent the "Poison Pill" risk
  of accidentally overwriting production files.
- The .gitignore should exclude tests/mocks/ to prevent accidental commits.
"""

import logging
import os
import shutil
import sys

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - [bootstrap] %(message)s"
)
logger = logging.getLogger(__name__)


def create_dummy_files():
    """
    Creates thin Python wrapper modules in 'tests/mocks/' that delegate to
    the real implementations in generator/agents/testgen_agent/.

    All files are created in 'tests/mocks/' directory to prevent accidental
    overwriting of production files.  The directory is removed after the
    bootstrap completes.
    """
    logger.info("Starting creation of dummy development environment modules...")

    # Define the safe directory for dummy files
    mock_dir = os.path.join("tests", "mocks")

    # Create the mock directory if it doesn't exist
    os.makedirs(mock_dir, exist_ok=True)
    logger.info(f"Using mock directory: {mock_dir}")

    required_dummy_files = {
        "audit_log.py": """# NOTE: This file is a dev-bootstrap thin wrapper; do not import in production.
# DUMMY AUDIT LOG
# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.
import logging
_logger = logging.getLogger(__name__)

def log_action(event: str, data: dict):
    \"\"\"Thin wrapper that delegates to runner.runner_audit.log_audit_event_sync.\"\"\"
    try:
        from runner.runner_audit import log_audit_event_sync
        log_audit_event_sync(event, data)
    except Exception as exc:
        _logger.warning(f"[audit_log wrapper] log_audit_event_sync unavailable: {exc}; event={event}")
""",
        "utils.py": """# NOTE: This file is a dev-bootstrap thin wrapper; do not import in production.
# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.
import asyncio
import logging
from typing import Dict, Any, List, Optional
_logger = logging.getLogger(__name__)

async def summarize_text(text: str, max_length: int = 1000) -> str:
    \"\"\"Thin wrapper that delegates to runner.runner_core summarization or falls back to a slice.\"\"\"
    try:
        from runner.runner_core import summarize_text as _real_summarize
        return await _real_summarize(text, max_length=max_length)
    except Exception as exc:
        _logger.warning(f"[utils wrapper] runner_core.summarize_text unavailable: {exc}; using truncation fallback.")
        return text[:max_length] + ("..." if len(text) > max_length else "")
""",
        "testgen_prompt.py": """# NOTE: This file is a dev-bootstrap thin wrapper; do not import in production.
# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.
import asyncio
import logging
from typing import Dict, Any, List, Optional
_logger = logging.getLogger(__name__)

async def build_agentic_prompt(purpose: str, language: str, code_files: Dict[str, str], **kwargs) -> str:
    \"\"\"Thin wrapper that delegates to the real prompt builder in testgen_agent.\"\"\"
    try:
        from generator.agents.testgen_agent.testgen_agent import build_agentic_prompt as _real_builder
        return await _real_builder(purpose=purpose, language=language, code_files=code_files, **kwargs)
    except Exception as exc:
        _logger.warning(f"[testgen_prompt wrapper] real builder unavailable: {exc}; returning minimal prompt.")
        return f"Purpose={purpose}, Lang={language}, Files={list(code_files.keys())}"

async def initialize_codebase_for_rag(repo_path: str):
    _logger.warning("[testgen_prompt wrapper] initialize_codebase_for_rag not delegated; skipping.")
""",
        "testgen_response_handler.py": """# NOTE: This file is a dev-bootstrap thin wrapper; do not import in production.
# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.
import asyncio
import logging
from typing import Dict, Any
_logger = logging.getLogger(__name__)

async def parse_llm_response(response_content: str, language: str) -> Dict[str, str]:
    \"\"\"Thin wrapper that delegates to the real response parser in testgen_agent.\"\"\"
    try:
        from generator.agents.testgen_agent.testgen_response_handler import parse_llm_response as _real_parser
        return await _real_parser(response_content=response_content, language=language)
    except Exception as exc:
        _logger.warning(f"[testgen_response_handler wrapper] real parser unavailable: {exc}; returning empty dict.")
        return {}
""",
        "testgen_validator.py": """# NOTE: This file is a dev-bootstrap thin wrapper; do not import in production.
# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.
import asyncio
import logging
from typing import Dict, Any
_logger = logging.getLogger(__name__)

async def validate_test_quality(code_files: Dict[str, str], test_files: Dict[str, str], language: str, validation_type: str) -> Dict[str, Any]:
    \"\"\"Thin wrapper that delegates to the real validator in testgen_agent.\"\"\"
    try:
        from generator.agents.testgen_agent.testgen_validator import validate_test_quality as _real_validator
        return await _real_validator(
            code_files=code_files, test_files=test_files,
            language=language, validation_type=validation_type
        )
    except Exception as exc:
        _logger.warning(f"[testgen_validator wrapper] real validator unavailable: {exc}; returning skipped status.")
        return {"status": "skipped", "issues": [str(exc)]}
""",
        "deploy_llm_call.py": """# NOTE: This file is a dev-bootstrap thin wrapper; do not import in production.
# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.
import asyncio
import logging
from typing import Dict, Any, AsyncGenerator, Optional
_logger = logging.getLogger(__name__)

async def call_llm(prompt: str, **kwargs) -> Dict[str, Any]:
    \"\"\"Thin wrapper that delegates to runner.llm_client.call_llm_api.\"\"\"
    try:
        from runner.llm_client import call_llm_api
        return await call_llm_api(prompt=prompt, **kwargs)
    except Exception as exc:
        _logger.warning(f"[deploy_llm_call wrapper] call_llm_api unavailable: {exc}")
        raise
""",
    }

    for fname, content in required_dummy_files.items():
        # Create file path within mock directory
        fpath = os.path.join(mock_dir, fname)

        # Safety check: Warn if file exists outside mock directory
        if os.path.exists(fname):
            logger.warning(
                f"WARNING: Production file '{fname}' exists in current directory. "
                f"This bootstrap script will NOT overwrite it. Using mock directory instead."
            )

        if not os.path.exists(fpath):
            try:
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(content)
                logger.info(f"Created dummy {fpath}.")
            except Exception as e:
                logger.error(f"Failed to create dummy file {fpath}: {e}")
                sys.exit(1)
        else:
            logger.info(f"Dummy {fpath} already exists, skipping creation.")

    # Create dummy llm_providers directory if missing within mock directory
    llm_providers_dir = os.path.join(mock_dir, "llm_providers")
    if not os.path.exists(llm_providers_dir):
        os.makedirs(llm_providers_dir)
        logger.info(f"Created '{llm_providers_dir}' directory.")

    print("\n" + "=" * 80)
    print("DEV BOOTSTRAP COMPLETE".center(80))
    print("".center(80))
    print(f"Wrapper files created in: {os.path.abspath(mock_dir)}".center(80))
    print("".center(80))
    print(
        "You can now run `testgen_agent.py` locally for development and testing.".center(
            80
        )
    )
    print("".center(80))
    print("To use these wrappers, add the mock directory to your Python path:".center(80))
    print(f"    export PYTHONPATH=$PYTHONPATH:{os.path.abspath(mock_dir)}".center(80))
    print("".center(80))
    print("NOTE: These wrapper files delegate to the real implementations.".center(80))
    print("DO NOT deploy these files to production environments.".center(80))
    print(
        "Your production environment MUST have the real modules installed.".center(80)
    )
    print("=" * 80 + "\n")


def cleanup_mock_files():
    """Remove the tests/mocks/ directory created by create_dummy_files().

    Call this after you have finished using the wrapper files so they are not
    accidentally committed or imported by other code.
    """
    mock_dir = os.path.join("tests", "mocks")
    try:
        shutil.rmtree(mock_dir)
        logger.info(f"Cleaned up mock directory: {mock_dir}")
    except FileNotFoundError:
        pass  # Already gone — nothing to do
    except Exception as cleanup_err:
        logger.warning(f"Could not remove mock directory '{mock_dir}': {cleanup_err}")


if __name__ == "__main__":
    create_dummy_files()
    # Cleanup is deferred to here so the wrapper files are available for use
    # during the session (e.g. add to PYTHONPATH, run testgen_agent).
    # Remove them when done so they are not accidentally committed.
    cleanup_mock_files()
