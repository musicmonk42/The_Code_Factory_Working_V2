"""
DEVELOPMENT-ONLY BOOTSTRAP SCRIPT

This script creates dummy/stub modules required for local CLI/dev testing
of testgen_agent.py and related tools. It must NEVER be run or imported in
production or packaged with production builds.

Usage:
    python scripts/bootstrap_agent_dev.py

What it does:
- Generates local dummy files that mimic interfaces of real dependencies.
- Allows the agent to run for development/testing without a production setup.

CAUTION:
- These files are for developer convenience ONLY.
- DO NOT deploy these files to production environments.
- The real implementations must exist in production deployments.
"""

import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - [bootstrap] %(message)s"
)
logger = logging.getLogger(__name__)


def create_dummy_files():
    """
    Creates dummy Python modules and a temporary Git repository
    required for local development and testing of testgen_agent.py.
    """
    logger.info("Starting creation of dummy development environment modules...")

    required_dummy_files = {
        "audit_log.py": """
def log_action(event: str, data: dict):
    # DUMMY AUDIT LOG: For development and local testing ONLY.
    # In production, this would securely log to a persistent, tamper-evident system (e.g., SIEM, ELK).
    print(f"[AUDIT_LOG_DUMMY] Event: {event}, Data: {data}")
""",
        "utils.py": """
import asyncio
from typing import Dict, Any, List, Optional
# DUMMY UTILS: For development and local testing ONLY.
# In production, this would provide real utility functions.
async def summarize_text(text: str, max_length: int = 1000) -> str:
    return text[:max_length] + ("..." if len(text) > max_length else "")
""",
        "testgen_prompt.py": """
import asyncio
from typing import Dict, Any, List, Optional
# DUMMY PROMPT BUILDER: For development and local testing ONLY.
async def build_agentic_prompt(purpose: str, language: str, code_files: Dict[str, str], **kwargs) -> str:
    return f"DUMMY_PROMPT: Purpose={purpose}, Lang={language}, Files={list(code_files.keys())}, Kwargs={kwargs}"
async def initialize_codebase_for_rag(repo_path: str):
    print(f"[RAG_DUMMY] Initializing RAG for {repo_path}")
""",
        "testgen_response_handler.py": """
from typing import Dict, Any
# DUMMY RESPONSE HANDLER: For development and local testing ONLY.
def parse_llm_response(response_content: str, language: str) -> Dict[str, str]:
    if "fix" in response_content.lower() or "heal" in response_content.lower():
        return {"fixed_test_dummy.py": "// Healed test content (DUMMY)"}
    return {"test_file_dummy.py": f"// Parsed test content for {language} (DUMMY): {response_content[:50]}"}
""",
        "testgen_validator.py": """
import asyncio
from typing import Dict, Any, List
# DUMMY TEST VALIDATOR: For development and local testing ONLY.
async def validate_test_quality(code_files: Dict[str, str], test_files: Dict[str, str], language: str, validation_type: str) -> Dict[str, Any]:
    print(f"[VALIDATOR_DUMMY] Validating quality for {language} with type {validation_type}")
    if validation_type == 'coverage':
        return {"status": "success", "coverage_percentage": 85.0, "issues": []}
    if validation_type == 'mutation':
        return {"status": "success", "mutation_score": 70.0, "issues": []}
    if validation_type == 'stress_performance':
        return {"status": "success", "performance_score": 0.9, "issues": []}
    return {"status": "failed", "score": 0.0, "issues": [f"Unsupported validation type (DUMMY): {validation_type}"]}
""",
        "deploy_llm_call.py": """
import asyncio
from typing import Dict, Any, AsyncGenerator, Optional, List, Tuple, Type
# DUMMY LLM CALL ORCHESTRATOR: For development and local testing ONLY.
try:
    from presidio_analyzer import AnalyzerEngine
    from presidio_anonymizer import AnonymizerEngine
    print("[PRESIDIO_DUMMY] Presidio modules available for dummy use.")
except ImportError:
    AnalyzerEngine = None
    AnonymizerEngine = None
    print("[PRESIDIO_DUMMY] Presidio not installed for dummy deploy_llm_call.")

class DummyClientSession:
    async def __aenter__(self): return self
    async def __aexit__(self, exc_type, exc_val, exc_tb): pass
    async def post(self, url, json, headers=None, timeout=None):
        class DummyResponse:
            async def json(self): return {"choices": [{"message": {"content": "mocked LLM response content"}}]}
            async def text(self): return "mocked LLM response content"
            @property
            def content(self):
                class DummyContent:
                    async def iter_any(self): yield b'data: {"choices":[{"delta":{"content":"mocked"}}]}'
                return DummyContent()
            def raise_for_status(self): pass
            @property
            def status(self): return 200
        return DummyResponse()
    @property
    def closed(self): return False
    async def close(self): pass
""",
    }

    for fname, content in required_dummy_files.items():
        if not os.path.exists(fname):
            try:
                with open(fname, "w", encoding="utf-8") as f:
                    f.write(content)
                logger.info(f"Created dummy {fname}.")
            except Exception as e:
                logger.error(f"Failed to create dummy file {fname}: {e}")
                sys.exit(1)
        else:
            logger.info(f"Dummy {fname} already exists, skipping creation.")

    # Create dummy llm_providers directory if missing
    if not os.path.exists("llm_providers"):
        os.makedirs("llm_providers")
        logger.info("Created 'llm_providers' directory.")

    print("\n" + "=" * 80)
    print("DUMMY DEVELOPMENT ENVIRONMENT BOOTSTRAP COMPLETE".center(80))
    print("".center(80))
    print(
        "You can now run `testgen_agent.py` locally for development and testing.".center(
            80
        )
    )
    print("".center(80))
    print("REMEMBER: These are DUMMY implementations.".center(80))
    print("DO NOT package or deploy these dummy files to production.".center(80))
    print(
        "Your production environment MUST have the real modules installed.".center(80)
    )
    print("=" * 80 + "\n")


if __name__ == "__main__":
    create_dummy_files()
