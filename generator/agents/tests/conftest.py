import os
import sys
from pathlib import Path

# --------------------------------------------------------------
# 1. ADD PROJECT ROOT TO PYTHONPATH (FIRST!)
# --------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[3]  # tests → agents → generator → repo root
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# --------------------------------------------------------------
# 2. DISABLE WATCHERS / ENABLE TESTING MODE
# --------------------------------------------------------------
os.environ["TESTING"] = "1"

# Note: OpenTelemetry, Prometheus, and other optional dependencies are now
# handled by the root conftest.py. This avoids module pollution issues.

# --------------------------------------------------------------
# 3. IMPORT LIGHTWEIGHT PIECES
# --------------------------------------------------------------

import shutil
import tempfile
from unittest.mock import AsyncMock, patch

# --------------------------------------------------------------
# 4. FIXTURES
# --------------------------------------------------------------
import pytest
import yaml


@pytest.fixture
def codegen_env():
    dir_path = Path(tempfile.mkdtemp(prefix="codegen_test_"))
    config = dir_path / "config.yaml"
    db = dir_path / "feedback.db"
    templates = dir_path / "templates"
    templates.mkdir()

    (templates / "python.jinja2").write_text(
        "Generate: {{ requirements.features }}. "
        'JSON: {"files": {"main.py": "def x(): pass"}}',
        encoding="utf-8",
    )

    cfg = {
        "backend": "openai",
        "api_keys": {"openai": "sk-test"},
        "model": {"openai": "gpt-4o"},
        "allow_interactive_hitl": True,
        "enable_security_scan": True,
        "feedback_store": {"type": "sqlite", "path": str(db)},
        "template_dir": str(templates),
        "compliance": {
            "banned_functions": ["eval"],
            "max_line_length": 100,
        },
    }

    with open(config, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f)

    yield {
        "config": str(config),
        "db": str(db),
        "req": {"features": ["fib"], "target_language": "python"},
    }

    shutil.rmtree(dir_path, ignore_errors=True)


@pytest.fixture
def mock_llm():
    """Mock LLM for testing code generation."""
    with patch(
        "generator.agents.codegen_agent.codegen_agent.call_llm_api",
        new_callable=AsyncMock,
    ) as m:
        m.return_value = {"content": '{"files": {"main.py": "def fib(n): return n"}}'}
        yield m


@pytest.fixture(autouse=True)
def cleanup_chromadb():
    """
    Clean up ChromaDB singleton instances between tests to prevent
    'An instance of Chroma already exists' errors.
    
    Note: This fixture accesses ChromaDB's internal _identifier_to_system registry
    because ChromaDB maintains singleton instances based on settings and doesn't
    provide a public API to clear them. Without this cleanup, tests that create
    ChromaDB clients with the same path but different settings will fail.
    
    Tested with ChromaDB 1.3.x. If ChromaDB's internal API changes in future versions,
    this cleanup will gracefully skip via the try-except block.
    """
    yield
    # Clean up after each test
    try:
        import chromadb
        from chromadb.api.shared_system_client import SharedSystemClient
        
        # Clear the singleton registry (internal API, but necessary for test isolation)
        # ChromaDB 1.x stores client instances in _identifier_to_system class variable
        if hasattr(SharedSystemClient, '_identifier_to_system'):
            SharedSystemClient._identifier_to_system.clear()
    except (ImportError, AttributeError):
        # ChromaDB not installed or API changed, skip cleanup
        pass
