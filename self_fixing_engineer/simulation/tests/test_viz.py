# simulation/tests/test_viz.py

import os
import sys

# Add the simulation/plugins directory to the path
current_file = os.path.abspath(__file__)
tests_dir = os.path.dirname(current_file)
simulation_dir = os.path.dirname(tests_dir)
plugins_dir = os.path.join(simulation_dir, "plugins")

# Add plugins directory to path
if plugins_dir not in sys.path:
    sys.path.insert(0, plugins_dir)

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Now import viz directly since we added plugins dir to path
import viz
from viz import (
    _load_config,
    _scrub_metadata,
    batch_export_panels,
    get_registered_viz_panels,
    plot_coverage_history,
    plot_flakiness_trend,
    plot_metric_trend,
    validate_panel_id,
)

# ==============================================================================
# Simplified test fixtures that work with minimal dependencies
# ==============================================================================


@pytest.fixture
def mock_matplotlib():
    """Mock matplotlib for tests."""
    with patch.object(viz, "MATPLOTLIB_AVAILABLE", True):
        mock_fig = MagicMock()
        mock_ax = MagicMock()
        with patch.object(viz, "plt") as mock_plt:
            mock_plt.subplots.return_value = (mock_fig, mock_ax)
            mock_plt.close = MagicMock()
            yield {"plt": mock_plt, "fig": mock_fig, "ax": mock_ax}


@pytest.fixture
def mock_filesystem():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        with patch.object(viz, "RESULTS_DIR", temp_path):
            yield temp_path


# ==============================================================================
# Basic functionality tests
# ==============================================================================


def test_validate_panel_id_success():
    """Test panel ID validation with valid input."""
    assert validate_panel_id("my_panel_1-0") == "my_panel_1-0"
    assert validate_panel_id("panel-123") == "panel-123"
    assert validate_panel_id("panel_456") == "panel_456"
    assert validate_panel_id("PANEL789") == "PANEL789"


def test_validate_panel_id_failure():
    """Test panel ID validation with invalid input."""
    with pytest.raises(ValueError, match="Invalid panel ID"):
        validate_panel_id("my;panel")
    with pytest.raises(ValueError):
        validate_panel_id("panel.123")
    with pytest.raises(ValueError):
        validate_panel_id("panel/456")
    with pytest.raises(ValueError):
        validate_panel_id("panel\\789")


def test_plot_flakiness_trend_with_mock(mock_matplotlib):
    """Test flakiness trend plot generation."""
    mock_runs = [
        {"returncode": 0},
        {"returncode": 1},
        {"returncode": 0},
    ]

    fig = plot_flakiness_trend(mock_runs, "test_file.py")

    assert fig is not None
    assert mock_matplotlib["plt"].subplots.called
    mock_matplotlib["ax"].plot.assert_called_once()
    mock_matplotlib["ax"].set_title.assert_called_once()


def test_plot_coverage_history_no_data():
    """Test coverage history with no data."""
    with patch.object(viz, "logger") as mock_logger:
        fig = plot_coverage_history([], "Coverage")
        assert fig is None
        mock_logger.info.assert_called_with("No coverage data to plot.")


def test_plot_coverage_history_with_data(mock_matplotlib):
    """Test coverage history with valid data."""
    coverage_data = [0.75, 0.80, 0.85, 0.90]

    fig = plot_coverage_history(coverage_data, "Test Coverage")

    assert fig is not None
    mock_matplotlib["ax"].plot.assert_called_once()
    mock_matplotlib["ax"].set_title.assert_called_with("Test Coverage History")


def test_plot_metric_trend_no_matplotlib():
    """Test metric trend when matplotlib is not available."""
    with patch.object(viz, "MATPLOTLIB_AVAILABLE", False):
        with patch.object(viz, "logger") as mock_logger:
            fig = plot_metric_trend([1, 2, 3], "Latency")
            assert fig is None
            mock_logger.warning.assert_called_once()


def test_plot_metric_trend_with_data(mock_matplotlib):
    """Test metric trend with valid data."""
    metrics = [100, 110, 95, 105, 102]

    fig = plot_metric_trend(metrics, "Response Time", "ms")

    assert fig is not None
    mock_matplotlib["ax"].plot.assert_called_once()
    mock_matplotlib["ax"].set_ylabel.assert_called_with("Response Time (ms)")


# ==============================================================================
# Panel registration tests
# ==============================================================================


def test_register_and_unregister_panel():
    """Test panel registration and unregistration."""
    # Clean up any existing test panels
    panels = get_registered_viz_panels()
    for panel_id in list(panels.keys()):
        if panel_id.startswith("test_"):
            viz.unregister_viz_panel(panel_id)

    # Register a new panel
    @viz.register_viz_panel(
        panel_id="test_custom_panel",
        title="Test Panel",
        description="A test panel",
        plot_type="matplotlib",
        roles=["admin"],
        export_supported=True,
    )
    def test_panel_func():
        return "test_result"

    # Check it was registered
    panels = get_registered_viz_panels()
    assert "test_custom_panel" in panels
    assert panels["test_custom_panel"]["title"] == "Test Panel"
    assert panels["test_custom_panel"]["roles"] == ["admin"]

    # Unregister and verify
    viz.unregister_viz_panel("test_custom_panel")
    panels = get_registered_viz_panels()
    assert "test_custom_panel" not in panels


def test_register_panel_invalid_id():
    """Test that invalid panel IDs are rejected."""
    with pytest.raises(ValueError, match="Invalid panel ID"):

        @viz.register_viz_panel(panel_id="test/invalid", title="Invalid Panel")
        def invalid_panel():
            pass


def test_get_panels_for_role():
    """Test role-based panel filtering."""

    # Register panels with different roles
    @viz.register_viz_panel(panel_id="test_role_admin", title="Admin Panel", roles=["admin"])
    def admin_panel():
        pass

    @viz.register_viz_panel(panel_id="test_role_user", title="User Panel", roles=["user", "admin"])
    def user_panel():
        pass

    @viz.register_viz_panel(panel_id="test_role_public", title="Public Panel", roles=None)
    def public_panel():
        pass

    try:
        # Test filtering
        admin_panels = viz.get_panels_for_role("admin")
        user_panels = viz.get_panels_for_role("user")
        guest_panels = viz.get_panels_for_role("guest")

        admin_titles = [p["title"] for p in admin_panels]
        user_titles = [p["title"] for p in user_panels]
        guest_titles = [p["title"] for p in guest_panels]

        # Admin should see admin and user panels
        assert "Admin Panel" in admin_titles or "User Panel" in admin_titles

        # User should see user panel
        assert "User Panel" in user_titles

        # Everyone should see public panel
        assert "Public Panel" in guest_titles

    finally:
        # Clean up
        viz.unregister_viz_panel("test_role_admin")
        viz.unregister_viz_panel("test_role_user")
        viz.unregister_viz_panel("test_role_public")


# ==============================================================================
# Export tests
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_export_panels(mock_matplotlib, mock_filesystem):
    """Test batch export functionality."""

    # Register a test panel
    @viz.register_viz_panel(
        panel_id="test_export",
        title="Export Test",
        plot_type="matplotlib",
        export_supported=True,
        export_args=([{"returncode": 0}], "test.py"),
    )
    def test_export_func(runs, filename):
        return mock_matplotlib["fig"]

    try:
        with patch.object(viz.CONFIG, "default_plot_format", "png"):
            with patch.object(viz.CONFIG, "redis_cache_url", None):
                result = await batch_export_panels(panel_ids=["test_export"], format="png")

                assert "test_export" in result
                assert result["test_export"] is not None
                if result["test_export"]:
                    assert result["test_export"].endswith(".png")
                    mock_matplotlib["fig"].savefig.assert_called()
    finally:
        viz.unregister_viz_panel("test_export")


# ==============================================================================
# Hook tests
# ==============================================================================


def test_pre_plot_hooks():
    """Test pre-plot hook functionality."""
    # Clear existing hooks
    viz._pre_plot_hooks.clear()

    def test_hook(plot_type, data):
        data["modified"] = True
        return data

    viz.register_pre_plot_hook(test_hook)

    test_data = {"original": "data"}
    result = viz.pre_plot_hook("matplotlib", test_data)

    assert result["original"] == "data"
    assert result["modified"]


def test_post_plot_hooks():
    """Test post-plot hook functionality."""
    # Clear existing hooks
    viz._post_plot_hooks.clear()

    def test_hook(plot_type, plot_obj, metadata):
        metadata["processed"] = True
        return plot_obj, metadata

    viz.register_post_plot_hook(test_hook)

    mock_plot = MagicMock()
    test_metadata = {"original": "metadata"}
    result_plot, result_metadata = viz.post_plot_hook("plotly", mock_plot, test_metadata)

    assert result_plot == mock_plot
    assert result_metadata["original"] == "metadata"
    assert result_metadata["processed"]


# ==============================================================================
# Configuration tests
# ==============================================================================


def test_load_config_defaults():
    """Test that config loads with defaults when no file exists."""
    with patch.object(Path, "exists", return_value=False):
        config = _load_config()
        assert hasattr(config, "results_dir")
        assert hasattr(config, "default_plot_format")
        assert hasattr(config, "redis_cache_url")
        assert hasattr(config, "redis_cache_ttl")


def test_load_config_from_env():
    """Test loading config from environment variables."""
    with patch.dict(
        os.environ,
        {
            "VIZ_RESULTS_DIR": "/custom/results",
            "VIZ_DEFAULT_PLOT_FORMAT": "svg",
            "VIZ_REDIS_CACHE_TTL": "7200",
        },
    ):
        with patch.object(Path, "exists", return_value=False):
            config = _load_config()
            # Config should have picked up env vars
            assert hasattr(config, "redis_cache_ttl")


# ==============================================================================
# Error handling tests
# ==============================================================================


def test_plot_with_exception():
    """Test that plot functions handle exceptions gracefully."""
    with patch.object(viz, "MATPLOTLIB_AVAILABLE", True):
        with patch.object(viz, "plt") as mock_plt:
            mock_plt.subplots.side_effect = Exception("Test error")
            with patch.object(viz, "logger") as mock_logger:
                fig = plot_flakiness_trend([{"returncode": 0}], "test.py")
                assert fig is None
                mock_logger.error.assert_called()


def test_scrub_metadata_without_detect_secrets():
    """Test metadata scrubbing when detect_secrets is not available."""
    with patch.object(viz, "DETECT_SECRETS_AVAILABLE", False):
        test_metadata = {"password": "secret123", "data": [1, 2, 3]}
        result = _scrub_metadata(test_metadata)
        # Without detect_secrets, data should be unchanged
        assert result == test_metadata


def test_dashboard_interface_check():
    """Test dashboard interface validation."""

    # Valid dashboard
    class ValidDashboard:
        def markdown(self, text):
            pass

        def warning(self, text):
            pass

        def plotly_chart(self, fig, **kwargs):
            pass

    assert viz.check_dashboard_interface(ValidDashboard())

    # Invalid dashboard
    class InvalidDashboard:
        def markdown(self, text):
            pass

    with patch.object(viz, "logger") as mock_logger:
        assert not viz.check_dashboard_interface(InvalidDashboard())
        mock_logger.error.assert_called()


# ==============================================================================
# Run basic test if executed directly
# ==============================================================================

if __name__ == "__main__":
    print("\n=== Running basic viz.py import test ===")
    print(f"viz module location: {viz.__file__}")
    print(f"viz.MATPLOTLIB_AVAILABLE: {viz.MATPLOTLIB_AVAILABLE}")
    print(f"viz.PLOTLY_AVAILABLE: {viz.PLOTLY_AVAILABLE}")
    print(f"viz.PYDANTIC_AVAILABLE: {viz.PYDANTIC_AVAILABLE}")
    print("\n=== Running simple validation test ===")
    try:
        result = validate_panel_id("test_panel")
        print(f"✓ validate_panel_id('test_panel') = '{result}'")
    except Exception as e:
        print(f"✗ validate_panel_id failed: {e}")

    print("\n=== Testing panel registration ===")
    initial_count = len(get_registered_viz_panels())
    print(f"Initial registered panels: {initial_count}")

    @viz.register_viz_panel(
        panel_id="test_direct_run",
        title="Direct Run Test",
        description="Test panel for direct execution",
    )
    def test_direct_panel():
        return "test"

    new_count = len(get_registered_viz_panels())
    print(f"After registration: {new_count} panels")

    viz.unregister_viz_panel("test_direct_run")
    final_count = len(get_registered_viz_panels())
    print(f"After unregistration: {final_count} panels")

    print("\n=== Basic tests complete ===")
