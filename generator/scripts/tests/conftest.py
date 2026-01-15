"""Pytest configuration for scripts tests"""
import sys
from pathlib import Path

# Add the scripts directory to the path so tests can import the modules
scripts_dir = Path(__file__).parent.parent
sys.path.insert(0, str(scripts_dir))
