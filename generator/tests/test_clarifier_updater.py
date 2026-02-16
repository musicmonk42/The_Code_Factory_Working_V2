import sys
from unittest.mock import MagicMock, patch


# --- Mock Configuration ---
class MockConfigObject:
    SCHEMA_VERSION = 3  # Required by clarifier_updater.py
    HISTORY_DB_PATH = "mock_history.db"  # Required by clarifier_updater.py


mock_config_instance = MagicMock(spec=MockConfigObject)
for attr, value in MockConfigObject.__dict__.items():
    if not attr.startswith("_"):
        setattr(mock_config_instance, attr, value)

# Mock get_config at module level BEFORE importing clarifier_updater
# This is critical because clarifier_updater.py calls get_config() at module level (line 108)
# and accesses settings.SCHEMA_VERSION at module level (line 273)
if 'generator.config' not in sys.modules:
    from types import ModuleType
    config_stub = ModuleType('generator.config')
    sys.modules['generator.config'] = config_stub
if 'generator.config.settings' not in sys.modules:
    from types import ModuleType
    settings_stub = ModuleType('generator.config.settings')
    settings_stub.get_config = MagicMock(return_value=mock_config_instance)
    sys.modules['generator.config.settings'] = settings_stub

# Patch get_config() before importing clarifier_updater
_pre_import_patch = patch('generator.config.settings.get_config', return_value=mock_config_instance)
_pre_import_patch.start()