# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test suite for fixer_ast.py - AST-based import resolution and cycle healing module.
"""

import ast
import asyncio
import hashlib
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import networkx as nx
import pytest

# Fix the import path - add the import_fixer directory to path
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
import_fixer_dir = os.path.join(parent_dir, "import_fixer")
sys.path.insert(0, import_fixer_dir)

# Mock redis module before importing fixer_ast
# IMPORTANT: Must provide real classes for redis.client types to avoid breaking
# portalocker's type annotations (typing.Optional[PubSubWorkerThread])
import types

mock_redis_module = MagicMock()

# Create redis.client with proper PubSubWorkerThread class for type annotations
mock_redis_client = types.ModuleType("redis.client")

class PubSubWorkerThread:
    """Stub class for redis.client.PubSubWorkerThread to satisfy type annotations."""
    pass

class Redis:
    """Stub class for redis.client.Redis to satisfy type annotations (used by portalocker)."""
    pass

class PubSub:
    """Stub class for redis.client.PubSub to satisfy type annotations (used by portalocker)."""
    pass

mock_redis_client.PubSubWorkerThread = PubSubWorkerThread
mock_redis_client.Redis = Redis
mock_redis_client.PubSub = PubSub
mock_redis_module.client = mock_redis_client

# Mock redis.asyncio separately with PubSub (redis-py 5.x structure)
mock_redis_async = MagicMock()
mock_redis_async.PubSub = MagicMock()  # PubSub lives in redis.asyncio, not redis.client
mock_redis_async.Redis = MagicMock()
mock_redis_module.asyncio = mock_redis_async

sys.modules["redis"] = mock_redis_module
sys.modules["redis.asyncio"] = mock_redis_async
sys.modules["redis.client"] = mock_redis_client

# Mock core dependencies
sys.modules["core_utils"] = MagicMock()
sys.modules["core_audit"] = MagicMock()
sys.modules["core_secrets"] = MagicMock()
sys.modules["fixer_ai"] = MagicMock()

# Import the module to be tested
from fixer_ast import (
    AnalyzerCriticalError,
    CycleHealer,
    DynamicImportHealer,
    ImportResolver,
    _run_async_in_sync,
    get_ai_refactoring_suggestion,
)

# --- Fixtures ---


@pytest.fixture(autouse=True)
def reset_globals():
    """Reset global state between tests."""
    import fixer_ast

    fixer_ast.REDIS_CLIENT = None
    yield


@pytest.fixture
def mock_core_dependencies():
    """Mocks core dependencies used by fixer_ast.py."""
    with (
        patch("fixer_ast.alert_operator") as mock_alert,
        patch(
            "fixer_ast.scrub_secrets", side_effect=lambda x: str(x) if x else ""
        ) as mock_scrub,
        patch("fixer_ast.audit_logger") as mock_audit,
        patch("fixer_ast.SECRETS_MANAGER") as mock_secrets,
        patch(
            "fixer_ast.get_ai_suggestions_real", return_value=["AI suggestion"]
        ) as mock_ai_suggest,
        patch(
            "fixer_ast.get_ai_patch_real", return_value=["AI patch"]
        ) as mock_ai_patch,
    ):

        mock_secrets.get_secret.return_value = "dummy_secret_value"

        yield {
            "alert_operator": mock_alert,
            "scrub_secrets": mock_scrub,
            "audit_logger": mock_audit,
            "SECRETS_MANAGER": mock_secrets,
            "ai_suggest": mock_ai_suggest,
            "ai_patch": mock_ai_patch,
        }


@pytest.fixture
async def mock_redis_client():
    """Mocks the Redis client with async operations."""

    class FakeAsyncRedis:
        def __init__(self):
            self.cache = {}

        async def get(self, key):
            await asyncio.sleep(0)
            return self.cache.get(key)

        async def setex(self, key, expiry, value):
            await asyncio.sleep(0)
            self.cache[key] = value
            return True

    fake_redis = FakeAsyncRedis()
    with patch("fixer_ast.REDIS_CLIENT", fake_redis):
        yield fake_redis


@pytest.fixture
def test_project_setup(tmp_path):
    """Sets up a dummy project structure for testing."""
    project_root = tmp_path / "test_ast_healing_project"
    project_root.mkdir()

    # Create subdirectories
    my_package = project_root / "my_package"
    my_package.mkdir()
    sub_module = my_package / "sub_module"
    sub_module.mkdir()

    # Create test files
    resolver_file = sub_module / "analyzer.py"
    resolver_file.write_text(
        "from . import utils\n"
        "from . import helper\n"
        "import os\n"
        "from my_package.sub_module import config\n"
    )

    cycle_file_a = project_root / "module_a.py"
    cycle_file_a.write_text(
        "import module_b\n" "def func_a():\n" "    return module_b.func_b()\n"
    )

    cycle_file_b = project_root / "module_b.py"
    cycle_file_b.write_text(
        "import module_a\n" "def func_b():\n" "    return module_a.func_a()\n"
    )

    dynamic_file = project_root / "dynamic_importer.py"
    dynamic_file.write_text(
        "mod_name = 'sys'\n"
        "my_sys = __import__(mod_name)\n"
        "exec('print(\"Dynamic exec\")')\n"
        "result = eval('1 + 2')\n"
    )

    syntax_error_file = project_root / "syntax_error.py"
    syntax_error_file.write_text("def bad_syntax:\n")

    return {
        "project_root": str(project_root),
        "resolver_file": str(resolver_file),
        "cycle_file_a": str(cycle_file_a),
        "cycle_file_b": str(cycle_file_b),
        "dynamic_file": str(dynamic_file),
        "syntax_error_file": str(syntax_error_file),
        "whitelisted_paths": [str(project_root)],
    }


# --- ImportResolver Tests ---


def test_import_resolver_init(test_project_setup, mock_core_dependencies):
    """Test ImportResolver initialization."""
    resolver = ImportResolver(
        "my_package.sub_module.analyzer",
        test_project_setup["project_root"],
        test_project_setup["whitelisted_paths"],
        ["my_package"],
    )

    assert resolver.current_module_path == "my_package.sub_module.analyzer"
    assert resolver.project_root == test_project_setup["project_root"]
    assert not resolver.modified


def test_import_resolver_converts_relative_imports(
    test_project_setup, mock_core_dependencies
):
    """Test that ImportResolver converts relative imports to absolute."""
    with open(test_project_setup["resolver_file"], "r") as f:
        code = f.read()

    tree = ast.parse(code)
    resolver = ImportResolver(
        "my_package.sub_module.analyzer",
        test_project_setup["project_root"],
        test_project_setup["whitelisted_paths"],
        ["my_package"],
    )

    new_tree = resolver.visit(tree)

    assert resolver.modified

    # Check that relative imports were converted
    new_code = ast.unparse(new_tree)
    assert (
        "from my_package import utils" in new_code
        or "import my_package.utils" in new_code
    )


def test_import_resolver_validates_paths(test_project_setup, mock_core_dependencies):
    """Test that ImportResolver validates whitelisted paths."""
    # Create an AST with an import
    tree = ast.parse("from . import something")

    # Try with a path outside whitelisted directories
    with patch("fixer_ast.PRODUCTION_MODE", True):
        resolver = ImportResolver(
            "bad_module",
            "/evil/path",
            test_project_setup["whitelisted_paths"],
            ["my_package"],
        )

        with pytest.raises(AnalyzerCriticalError, match="outside whitelisted paths"):
            resolver.visit(tree)


# --- CycleHealer Tests ---


def test_cycle_healer_init_validates_file(test_project_setup, mock_core_dependencies):
    """Test that CycleHealer validates file existence."""
    graph = nx.DiGraph()

    with pytest.raises(AnalyzerCriticalError, match="File not found"):
        CycleHealer(
            "/nonexistent/file.py",
            ["module_a", "module_b"],
            graph,
            test_project_setup["project_root"],
            test_project_setup["whitelisted_paths"],
        )


def test_cycle_healer_init_validates_whitelist(
    test_project_setup, mock_core_dependencies
):
    """Test that CycleHealer validates whitelisted paths."""
    graph = nx.DiGraph()

    # Create a file outside the whitelisted path
    outside_file = "/tmp/outside.py"
    Path(outside_file).touch()

    try:
        with pytest.raises(AnalyzerCriticalError, match="outside whitelisted paths"):
            CycleHealer(
                outside_file,
                ["module_a", "module_b"],
                graph,
                test_project_setup["project_root"],
                test_project_setup["whitelisted_paths"],
            )
    finally:
        if os.path.exists(outside_file):
            os.remove(outside_file)


def test_cycle_healer_handles_syntax_error(test_project_setup, mock_core_dependencies):
    """Test that CycleHealer handles syntax errors properly."""
    graph = nx.DiGraph()

    with pytest.raises(AnalyzerCriticalError, match="Syntax error"):
        CycleHealer(
            test_project_setup["syntax_error_file"],
            ["module_a", "module_b"],
            graph,
            test_project_setup["project_root"],
            test_project_setup["whitelisted_paths"],
        )


@pytest.mark.asyncio
async def test_cycle_healer_finds_problematic_import(
    test_project_setup, mock_core_dependencies
):
    """Test that CycleHealer can find problematic imports."""
    graph = nx.DiGraph()
    graph.add_edge("module_a", "module_b")
    graph.add_edge("module_b", "module_a")
    graph.add_node("module_a", path=test_project_setup["cycle_file_a"])
    graph.add_node("module_b", path=test_project_setup["cycle_file_b"])

    healer = CycleHealer(
        test_project_setup["cycle_file_a"],
        ["module_a", "module_b"],
        graph,
        test_project_setup["project_root"],
        test_project_setup["whitelisted_paths"],
    )

    result = await healer.find_problematic_import()

    assert result is not None
    import_node, usage_names = result
    assert isinstance(import_node, ast.Import)


@pytest.mark.asyncio
async def test_cycle_healer_moves_import_to_function(
    test_project_setup, mock_core_dependencies
):
    """Test that CycleHealer moves imports into functions."""
    graph = nx.DiGraph()
    graph.add_edge("module_a", "module_b")
    graph.add_edge("module_b", "module_a")
    graph.add_node("module_a", path=test_project_setup["cycle_file_a"])
    graph.add_node("module_b", path=test_project_setup["cycle_file_b"])

    healer = CycleHealer(
        test_project_setup["cycle_file_a"],
        ["module_a", "module_b"],
        graph,
        test_project_setup["project_root"],
        test_project_setup["whitelisted_paths"],
    )

    new_code = await healer.heal()

    assert new_code is not None
    # The import should be moved inside the function
    assert "def func_a():\n    import module_b" in new_code


# --- DynamicImportHealer Tests ---


def test_dynamic_import_healer_finds_dynamic_imports(
    test_project_setup, mock_core_dependencies
):
    """Test that DynamicImportHealer finds dynamic import patterns."""
    healer = DynamicImportHealer(
        test_project_setup["dynamic_file"],
        test_project_setup["project_root"],
        test_project_setup["whitelisted_paths"],
    )

    fixes = healer.heal()

    assert len(fixes) > 0

    # Check that suggestions were generated
    for node, suggestion in fixes:
        assert isinstance(node, ast.Call)
        assert len(suggestion) > 0

        # Check for specific suggestions
        if node.func.id == "__import__":
            assert (
                "importlib.import_module" in suggestion or "static import" in suggestion
            )
        elif node.func.id in ["exec", "eval"]:
            assert "security risk" in suggestion


def test_dynamic_import_healer_validates_paths(
    test_project_setup, mock_core_dependencies
):
    """Test that DynamicImportHealer validates paths."""
    with pytest.raises(AnalyzerCriticalError, match="File not found"):
        DynamicImportHealer(
            "/nonexistent/file.py",
            test_project_setup["project_root"],
            test_project_setup["whitelisted_paths"],
        )


# --- AI Integration Tests ---


def test_get_ai_refactoring_suggestion(mock_core_dependencies):
    """Test AI refactoring suggestion wrapper."""
    context = "Fix circular import between A and B"

    result = get_ai_refactoring_suggestion(context)

    assert result == "AI suggestion"
    mock_core_dependencies["scrub_secrets"].assert_called_with(context)
    mock_core_dependencies["audit_logger"].log_event.assert_any_call(
        "ai_suggestion_request", context_snippet=context[:200]
    )


def test_get_ai_refactoring_suggestion_handles_error(mock_core_dependencies):
    """Test AI suggestion error handling."""
    mock_core_dependencies["ai_suggest"].side_effect = Exception("API error")

    with pytest.raises(Exception):
        get_ai_refactoring_suggestion("context")

    mock_core_dependencies["alert_operator"].assert_called()


# --- Async Helper Tests ---


def test_run_async_in_sync_no_loop():
    """Test _run_async_in_sync when no event loop is running."""

    async def async_func():
        return "result"

    result = _run_async_in_sync(async_func())
    assert result == "result"


@pytest.mark.asyncio
async def test_run_async_in_sync_with_loop():
    """Test _run_async_in_sync when event loop is running."""

    async def async_func():
        await asyncio.sleep(0)
        return "result"

    # In test context, loop is already running
    with patch("asyncio.run_coroutine_threadsafe") as mock_run:
        mock_future = Mock()
        mock_future.result.return_value = "threadsafe_result"
        mock_run.return_value = mock_future

        result = _run_async_in_sync(async_func())
        assert result == "threadsafe_result"


# --- Redis Cache Tests ---


@pytest.mark.asyncio
async def test_cycle_healer_uses_cache(
    test_project_setup, mock_core_dependencies, mock_redis_client
):
    """Test that CycleHealer uses Redis cache for AST."""
    graph = nx.DiGraph()
    graph.add_node("module_a", path=test_project_setup["cycle_file_a"])

    # Pre-populate cache
    with open(test_project_setup["cycle_file_a"], "r") as f:
        content = f.read()

    cache_key = (
        f"ast:{hashlib.sha256(test_project_setup['cycle_file_a'].encode()).hexdigest()}"
    )
    await mock_redis_client.setex(cache_key, 86400, content)

    healer = CycleHealer(
        test_project_setup["cycle_file_a"],
        ["module_a"],
        graph,
        test_project_setup["project_root"],
        test_project_setup["whitelisted_paths"],
    )

    assert healer.original_code == content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
