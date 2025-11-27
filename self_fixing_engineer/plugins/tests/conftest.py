"""
Configuration for plugins tests.
Adds the plugins directory to sys.path so that core_audit and other plugins can be imported.
"""
import sys
from pathlib import Path

# Add plugins directory to sys.path for direct imports
plugins_dir = Path(__file__).parent.parent
if str(plugins_dir) not in sys.path:
    sys.path.insert(0, str(plugins_dir))
