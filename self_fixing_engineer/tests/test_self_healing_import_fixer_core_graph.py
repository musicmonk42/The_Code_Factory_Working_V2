# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
test_core_graph.py - Test suite for ImportGraphAnalyzer
Fixed version with import patching before module load
"""

import importlib.util
import os
import sys
from unittest.mock import AsyncMock, Mock, patch

import pytest

# Setup paths
current_dir = os.path.dirname(os.path.abspath(__file__))
# The analyzer directory is under self_healing_import_fixer, not at the root level
analyzer_dir = os.path.join(os.path.dirname(current_dir), "self_healing_import_fixer", "analyzer")
sys.path.insert(0, analyzer_dir)

# Create all mock objects
mock_alert_operator = Mock()
mock_scrub_secrets = Mock(side_effect=lambda x: x)
mock_audit_logger = Mock()
mock_audit_logger.log_event = Mock()
mock_secrets_manager = Mock()
mock_secrets_manager.get_secret = Mock(return_value="dummy_secret")


# Create mock modules with proper structure
class MockCoreUtils:
    alert_operator = mock_alert_operator
    scrub_secrets = mock_scrub_secrets


class MockCoreAudit:
    audit_logger = mock_audit_logger


class MockCoreSecrets:
    SECRETS_MANAGER = mock_secrets_manager


# Install mock modules BEFORE importing core_graph
sys.modules["core_utils"] = MockCoreUtils()
sys.modules["core_audit"] = MockCoreAudit()
sys.modules["core_secrets"] = MockCoreSecrets()

# Now load core_graph with patched imports
core_graph_path = os.path.join(analyzer_dir, "core_graph.py")

# Read and modify the source to fix relative imports
with open(core_graph_path, "r") as f:
    source = f.read()

# Replace relative imports with absolute imports
source = source.replace("from .core_utils", "from core_utils")
source = source.replace("from .core_audit", "from core_audit")
source = source.replace("from .core_secrets", "from core_secrets")

# Create module from modified source
spec = importlib.util.spec_from_loader("core_graph", loader=None)
core_graph_module = importlib.util.module_from_spec(spec)

# Mock Redis before executing module
mock_redis_class = Mock()
mock_redis_instance = AsyncMock()
mock_redis_instance.ping = AsyncMock()
mock_redis_class.Redis.return_value = mock_redis_instance

# Patch redis.asyncio before executing the module
with patch.dict("sys.modules", {"redis": Mock(), "redis.asyncio": mock_redis_class}):
    # Execute the modified source
    exec(source, core_graph_module.__dict__)

# Add to sys.modules
sys.modules["core_graph"] = core_graph_module

# Extract classes from the module
ImportGraphAnalyzer = core_graph_module.ImportGraphAnalyzer
AnalyzerCriticalError = core_graph_module.AnalyzerCriticalError
NonCriticalError = core_graph_module.NonCriticalError

# Mark all tests to use asyncio
pytestmark = pytest.mark.asyncio

# --- Fixtures ---


@pytest.fixture
def mock_alert_operator_graph():
    """Mock for alert_operator function"""
    return mock_alert_operator


@pytest.fixture
def mock_audit_logger_graph():
    """Mock for audit_logger"""
    return mock_audit_logger


@pytest.fixture
def mock_os_env_graph():
    """Mock for os.environ"""
    with patch.dict(os.environ, {"PRODUCTION_MODE": "false"}):
        yield


@pytest.fixture
def mock_graphviz():
    """Mock for graphviz module"""
    mock_dot = Mock()
    mock_dot.node = Mock()
    mock_dot.edge = Mock()
    mock_dot.render = Mock()

    mock_digraph = Mock(return_value=mock_dot)

    with patch.object(core_graph_module, "graphviz") as mock_graphviz_module:
        mock_graphviz_module.Digraph = mock_digraph
        yield mock_dot


@pytest.fixture
def mock_shutil_which():
    """Mock for shutil.which"""
    with patch("shutil.which") as mock:
        mock.return_value = "/usr/bin/dot"
        yield mock


@pytest.fixture
def test_project_setup(tmp_path):
    """Creates a test project structure with Python files"""
    project_root = tmp_path / "test_project"
    project_root.mkdir()

    # Create main.py
    main_file = project_root / "main.py"
    main_file.write_text("""
import utils.helpers
import lib.component_a

def main():
    pass
""")

    # Create utils package
    utils_dir = project_root / "utils"
    utils_dir.mkdir()
    (utils_dir / "__init__.py").write_text("")
    (utils_dir / "helpers.py").write_text("""
import lib.component_b

def helper():
    pass
""")

    # Create lib package with circular imports
    lib_dir = project_root / "lib"
    lib_dir.mkdir()
    (lib_dir / "__init__.py").write_text("")
    (lib_dir / "component_a.py").write_text("""
import lib.component_b

class ComponentA:
    pass
""")
    (lib_dir / "component_b.py").write_text("""
import lib.component_a

class ComponentB:
    pass
""")

    # Create a dead code file
    dead_file = project_root / "dead_code.py"
    dead_file.write_text("""
def unused():
    pass
""")

    # Create a file with syntax error
    bad_file = project_root / "bad_syntax.py"
    bad_file.write_text("def bad_func:\n")  # Missing parentheses

    return str(project_root)


# --- Tests ---


def test_init_success(test_project_setup, mock_audit_logger_graph):
    """Verifies successful initialization of the analyzer."""
    # Clear previous calls
    mock_audit_logger_graph.log_event.reset_mock()

    analyzer = ImportGraphAnalyzer(test_project_setup)
    assert analyzer.project_root == os.path.abspath(test_project_setup)

    # Check that initialization was logged
    assert mock_audit_logger_graph.log_event.called
    call_args = mock_audit_logger_graph.log_event.call_args_list[-1]  # Get last call
    assert call_args[0][0] == "graph_analyzer_init"


def test_init_invalid_project_root(mock_alert_operator_graph):
    """Tests that initialization fails for a non-existent project directory."""
    mock_alert_operator_graph.reset_mock()

    with pytest.raises(AnalyzerCriticalError) as excinfo:
        ImportGraphAnalyzer("/non/existent/path")
    assert "is not a valid directory" in str(excinfo.value)
    assert mock_alert_operator_graph.called


def test_init_not_in_whitelisted_path(tmp_path, mock_alert_operator_graph):
    """Tests that initialization fails if project is not in whitelisted path."""
    mock_alert_operator_graph.reset_mock()

    project_root = str(tmp_path / "project")
    os.makedirs(project_root)
    config = {"whitelisted_paths": ["/safe/path"]}

    with pytest.raises(AnalyzerCriticalError) as excinfo:
        ImportGraphAnalyzer(project_root, config)
    assert "is not within whitelisted paths" in str(excinfo.value)
    assert mock_alert_operator_graph.called


def test_build_graph_no_python_files(tmp_path, mock_audit_logger_graph):
    """Tests that an empty graph is returned for project with no Python files."""
    mock_audit_logger_graph.log_event.reset_mock()

    analyzer = ImportGraphAnalyzer(str(tmp_path))
    graph = analyzer.build_graph()
    assert graph == {}

    # Check for the skip event
    calls = mock_audit_logger_graph.log_event.call_args_list
    assert any(call[0][0] == "graph_build_skipped" for call in calls)


def test_build_graph_with_files(test_project_setup, mock_audit_logger_graph):
    """Tests basic graph building with Python files."""
    mock_audit_logger_graph.log_event.reset_mock()

    analyzer = ImportGraphAnalyzer(test_project_setup)
    graph = analyzer.build_graph()

    # Should have found some modules
    assert len(graph) > 0
    assert "main" in graph

    # Check logging
    calls = mock_audit_logger_graph.log_event.call_args_list
    assert any(call[0][0] == "graph_build_start" for call in calls)
    assert any(call[0][0] == "graph_build_complete" for call in calls)


def test_detect_cycles(test_project_setup):
    """Tests cycle detection in import graph."""
    analyzer = ImportGraphAnalyzer(test_project_setup)

    # Create a simple graph with a cycle
    test_graph = {"a": {"b"}, "b": {"c"}, "c": {"a"}}

    cycles = analyzer.detect_cycles(test_graph)
    assert len(cycles) > 0

    # Verify the cycle contains all three nodes
    cycle = cycles[0]
    assert "a" in cycle
    assert "b" in cycle
    assert "c" in cycle


def test_detect_dead_nodes(test_project_setup):
    """Tests detection of unused modules."""
    analyzer = ImportGraphAnalyzer(test_project_setup)
    graph = analyzer.build_graph()

    dead_nodes = analyzer.detect_dead_nodes(graph)

    # dead_code.py should be detected as dead
    assert "dead_code" in dead_nodes


def test_visualize_graph_success(tmp_path, mock_graphviz, mock_shutil_which):
    """Tests graph visualization when enabled."""
    output_dir = tmp_path / "reports"
    output_dir.mkdir()

    config = {"allow_graphviz_spawn": True, "report_output_dir": str(output_dir)}

    analyzer = ImportGraphAnalyzer(str(tmp_path), config)
    analyzer.graph = {"a": {"b"}, "b": set()}

    analyzer.visualize_graph(output_file="test_graph", format="png")

    # Check that render was called
    assert mock_graphviz.render.called


def test_visualize_graph_disabled_in_production(
    tmp_path, mock_graphviz, mock_alert_operator_graph
):
    """Tests that visualization is skipped in production when not allowed."""
    mock_alert_operator_graph.reset_mock()

    # Set production mode
    core_graph_module.PRODUCTION_MODE = True

    try:
        config = {"allow_graphviz_spawn": False}
        analyzer = ImportGraphAnalyzer(str(tmp_path), config)
        analyzer.graph = {"a": {"b"}}

        analyzer.visualize_graph()

        # Should not render
        assert not mock_graphviz.render.called
        # Should alert operator
        assert mock_alert_operator_graph.called
    finally:
        # Reset production mode
        core_graph_module.PRODUCTION_MODE = False


def test_max_files_limit(tmp_path, mock_alert_operator_graph):
    """Tests that analyzer respects max_python_files limit."""
    mock_alert_operator_graph.reset_mock()

    # Create many Python files
    for i in range(10):
        (tmp_path / f"file_{i}.py").write_text("pass")

    config = {"max_python_files": 5}
    analyzer = ImportGraphAnalyzer(str(tmp_path), config)
    analyzer.build_graph()

    # Should have warned about limit
    assert mock_alert_operator_graph.called
    call_args = mock_alert_operator_graph.call_args[0][0]
    assert "max Python files limit" in call_args


def test_parsing_error_threshold(test_project_setup, mock_alert_operator_graph):
    """Tests handling of parsing error threshold."""
    mock_alert_operator_graph.reset_mock()

    # Set very low threshold to trigger error
    config = {"parsing_error_threshold": 0.0}

    analyzer = ImportGraphAnalyzer(test_project_setup, config)

    # In dev mode, should just warn
    graph = analyzer.build_graph()
    assert isinstance(graph, dict)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
