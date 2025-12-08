# generator/conftest.py
"""
Root conftest.py for generator tests.
Adds the generator directory to sys.path to allow imports like 'from main.api import ...'
"""
import sys
from pathlib import Path

# Add the generator directory to sys.path
generator_root = Path(__file__).parent.resolve()
generator_root_str = str(generator_root)

# Always insert at the beginning to ensure imports work correctly
if generator_root_str in sys.path:
    sys.path.remove(generator_root_str)
sys.path.insert(0, generator_root_str)
