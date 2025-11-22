import pytest
import os
import json
from unittest.mock import patch, MagicMock, AsyncMock
import tempfile
import shutil

# Mock Streamlit to prevent it from running the app during tests
@pytest.fixture(autouse=True)
def mock_streamlit():
    """Mocks Streamlit functions for unit testing."""
    mock_st = MagicMock()
    # Mock session_state dictionary-like behavior
    mock_st.session_state = {}
    mock_st.checkbox = MagicMock()
    mock_st.selectbox = MagicMock()
    mock_st.expander = MagicMock()
    mock_st.button = MagicMock()
    mock_st.sidebar.button = MagicMock()
    mock_st.tabs = MagicMock()
    mock_st.rerun = MagicMock()
    mock_st.stop = MagicMock()
    with patch('simulation.dashboard.st_dash', new=mock_st):
        yield mock_st

# Setup a mock plugin directory
@pytest.fixture
def mock_plugin_and_result_dirs():
    """Creates a temporary directory structure for plugins and results."""
    temp_dir = tempfile.mkdtemp()
    plugins_dir = os.path.join(temp_dir, 'plugins')
    results_dir = os.path.join(temp_dir, 'simulation_results')
    configs_dir = os.path.join(temp_dir, 'configs')
    os.makedirs(plugins_dir, exist_ok=True)
    os.makedirs(results_dir, exist_ok=True)
    os.makedirs(configs_dir, exist_ok=True)
    
    # Write a dummy plugin file
    with open(os.path.join(plugins_dir, 'my_dummy_plugin.py'), 'w') as f:
        f.write("""
import streamlit as st
def register_my_dashboard_panels(register_func):
    register_func('my_panel', 'My Panel', lambda s, d: s.write('Panel content'), live_data_supported=True)
def render_sidebar_component(sidebar):
    sidebar.button('Dummy Sidebar Button')
def render_main_component(main):
    main.write('Dummy Main Component')
""")
    # Write a dummy result file
    with open(os.path.join(results_dir, 'session_1__test_code__sim_20240101_120000.json'), 'w') as f:
        json.dump({"test_file": "session_1_tests", "status": "COMPLETED"}, f)

    # Patch Config class to use temporary directories
    with patch('simulation.dashboard.Config') as mock_config:
        mock_config.PLUGINS_DIR = plugins_dir
        mock_config.RESULTS_DIR = results_dir
        mock_config.CONFIG_DIR = configs_dir
        yield mock_config
    
    # Cleanup
    shutil.rmtree(temp_dir)

@pytest.fixture
def mock_onboarding_backends():
    """Mocks backend-related imports for the onboarding wizard."""
    with patch('simulation.dashboard.ONBOARDING_BACKENDS_AVAILABLE', True), \
         patch('simulation.dashboard.MeshPubSub') as MockMeshPubSub, \
         patch('simulation.dashboard.CheckpointManager') as MockCheckpointManager:
        
        MockMeshPubSub.supported_backends.return_value = ["redis", "local"]
        MockMeshPubSub.return_value.healthcheck = AsyncMock(return_value={"status": "ok", "message": "Mocked health"})
        MockMeshPubSub.return_value.connect = AsyncMock()
        MockMeshPubSub.return_value.close = AsyncMock()

        MockCheckpointManager._BACKENDS = {"fs": None}
        MockCheckpointManager.return_value.load = AsyncMock(return_value={"status": "healthy"})
        MockCheckpointManager.return_value.save = AsyncMock()
        MockCheckpointManager.return_value.delete = AsyncMock()
        
        yield MockMeshPubSub, MockCheckpointManager

# ==============================================================================
# Tests for Plugin Loading and Management
# ==============================================================================

def test_load_plugin_dashboard_panels_cached(mock_plugin_and_result_dirs):
    """
    Test that the caching function correctly discovers and loads plugins
    from the mock directory.
    """
    from simulation import dashboard
    dashboard.load_plugin_dashboard_panels_cached()

    panels = dashboard.get_registered_dashboard_panels()
    sidebar_components = dashboard.get_registered_sidebar_components()
    main_components = dashboard.get_registered_main_components()

    assert len(panels) == 1
    assert panels[0]['id'] == 'my_panel'
    assert len(sidebar_components) == 1
    assert len(main_components) == 1

def test_load_plugin_dashboard_panels_cached_with_dangerous_name(mock_plugin_and_result_dirs):
    """
    Test that a plugin with a dangerous name is skipped for security reasons.
    """
    # Create a dummy plugin file with a dangerous name
    dangerous_plugin_path = os.path.join(mock_plugin_and_result_dirs.PLUGINS_DIR, 'os.py')
    with open(dangerous_plugin_path, 'w') as f:
        f.write("def register_my_dashboard_panels(register_func): pass")
    
    from simulation import dashboard
    dashboard.load_plugin_dashboard_panels_cached()
    
    panels = dashboard.get_registered_dashboard_panels()
    assert len(panels) == 1 # The original plugin should still load, but 'os.py' should not
    
def test_is_version_compatible():
    """Test the version compatibility check helper function."""
    from simulation import dashboard
    assert dashboard.is_version_compatible("1.1.0", "1.0.0", "2.0.0") is True
    assert dashboard.is_version_compatible("0.9.0", "1.0.0", "2.0.0") is False
    assert dashboard.is_version_compatible("2.1.0", "1.0.0", "2.0.0") is False

# ==============================================================================
# Tests for Onboarding Wizard
# ==============================================================================

def test_display_onboarding_wizard_config_generation(mock_streamlit, mock_plugin_and_result_dirs):
    """Test that the onboarding wizard correctly generates config and plugins."""
    from simulation import dashboard
    
    # Mock user input
    mock_streamlit.selectbox.side_effect = ["agentic_swarm", "redis", "fs"]
    mock_streamlit.multiselect.return_value = ["python"]
    mock_streamlit.text_input.side_effect = ["redis://localhost:6379", "./checkpoints"]
    
    # Mock `form_submit_button` to be True
    with patch.object(mock_streamlit, 'form_submit_button', return_value=True):
        dashboard.display_onboarding_wizard()
    
    # Check if config.json was created
    config_path = os.path.join(mock_plugin_and_result_dirs.CONFIG_DIR, 'config.json')
    assert os.path.exists(config_path)
    
    # Check if demo plugin was created
    plugin_dir = os.path.join(mock_plugin_and_result_dirs.PLUGINS_DIR, 'demo_python_plugin')
    assert os.path.exists(plugin_dir)
    assert os.path.exists(os.path.join(plugin_dir, 'manifest.json'))

@pytest.mark.asyncio
async def test_run_health_checks_gui_success(mock_onboarding_backends):
    """Test that health checks pass successfully."""
    from simulation import dashboard
    mock_config = {
        "notification_backend": {"type": "redis", "url": "redis://localhost:6379/0"},
        "checkpoint_backend": {"type": "fs", "dir": "./checkpoints"}
    }
    
    # The async function is called via run_async_streamlit
    with patch('simulation.dashboard.st_dash') as mock_st_dash:
        mock_st_dash.session_state.plugin_manager_instance = MagicMock()
        await dashboard._run_health_checks_gui(mock_config)
        
        assert mock_st_dash.success.call_count == 2
        
def test_sanitize_plugin_name():
    """Test the sanitize_plugin_name function for security."""
    from simulation import dashboard
    with pytest.raises(ValueError, match="Path traversal"):
        dashboard.sanitize_plugin_name("../etc/passwd")
    
    sanitized = dashboard.sanitize_plugin_name("my-plugin_1.0")
    assert sanitized == "my-plugin_10"
    
    with pytest.raises(ValueError, match="Dangerous plugin name detected"):
        dashboard.sanitize_plugin_name("sys")

# ==============================================================================
# Tests for Data Loading and Filtering
# ==============================================================================

def test_load_all_simulation_results(mock_plugin_and_result_dirs):
    """Test that results are loaded and sorted correctly."""
    from simulation import dashboard
    results = dashboard.load_all_simulation_results(mock_plugin_and_result_dirs.RESULTS_DIR)
    
    assert len(results) == 1
    assert "status" in results[0]
    assert results[0]['test_file'] == "session_1_tests"

def test_load_all_simulation_results_with_invalid_json(mock_plugin_and_result_dirs):
    """Test that invalid JSON files are skipped without crashing."""
    invalid_json_path = os.path.join(mock_plugin_and_result_dirs.RESULTS_DIR, 'corrupted.json')
    with open(invalid_json_path, 'w') as f:
        f.write("{'key': 'invalid_json'")
    
    from simulation import dashboard
    results = dashboard.load_all_simulation_results(mock_plugin_and_result_dirs.RESULTS_DIR)
    assert len(results) == 1 # Only the valid one should be loaded

# ==============================================================================
# Tests for Translation (`t` function)
# ==============================================================================

def test_translation_function(mock_streamlit):
    """Test the localization function `t` with different languages."""
    from simulation import dashboard
    
    # Mock session state for language
    mock_streamlit.session_state["lang"] = "en"
    assert dashboard.t("welcome_message") == "Welcome"
    
    mock_streamlit.session_state["lang"] = "es"
    assert dashboard.t("welcome_message") == "Bienvenido"
    
    # Test fallback to default language
    assert dashboard.t("non_existent_key") == "non_existent_key"
    
    # Test fallback to key if not found in default language
    mock_streamlit.session_state["lang"] = "en"
    assert dashboard.t("language_selector_label") == "Language"