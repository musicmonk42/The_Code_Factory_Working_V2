"""Pytest configuration for generator tests."""
import os

def pytest_configure(config):
    """Skip expensive initialization during collection phase."""
    if config.option.collectonly:
        os.environ['SKIP_EXPENSIVE_INIT'] = '1'
        os.environ['PYTEST_COLLECTING_ONLY'] = '1'
