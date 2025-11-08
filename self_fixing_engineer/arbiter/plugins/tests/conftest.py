# diagnose_imports.py
"""Diagnose import issues."""
import sys
from pathlib import Path

print("Python Path:")
for p in sys.path:
    print(f"  {p}")

print("\nTrying imports:")
imports = [
    "arbiter",
    "arbiter.plugins",
    "arbiter.plugins.multi_modal_plugin",
    "arbiter.plugins.multi_modal_config",
    "arbiter.plugins.multimodal.interface",
]

for imp in imports:
    try:
        exec(f"import {imp}")
        print(f"✓ {imp}")
    except ImportError as e:
        print(f"✗ {imp}: {e}")

print("\nChecking files:")
files = [
    "arbiter/plugins/multi_modal_plugin.py",
    "arbiter/plugins/multi_modal_config.py",
    "arbiter/plugins/multimodal/interface.py",
]

for f in files:
    path = Path(f)
    if path.exists():
        print(f"✓ {f} exists")
    else:
        print(f"✗ {f} missing")