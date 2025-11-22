#!/usr/bin/env python3
"""Auto-fix common test issues"""

import re
from pathlib import Path

def fix_async_tests():
    """Add asyncio markers to async tests"""
    test_files = Path(".").glob("**/test_*.py")
    for file in test_files:
        with open(file, 'r') as f:
            content = f.read()
        
        # Add import if needed
        if "async def test_" in content and "pytest.mark.asyncio" not in content:
            if "import pytest" not in content:
                content = "import pytest\n" + content
            
            # Add decorator to async tests
            content = re.sub(
                r'^(async def test_)',
                r'@pytest.mark.asyncio\n\1',
                content,
                flags=re.MULTILINE
            )
            
            with open(file, 'w') as f:
                f.write(content)
            print(f"Fixed {file}")

if __name__ == "__main__":
    fix_async_tests()
