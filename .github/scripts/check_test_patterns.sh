#!/bin/bash
# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.


echo "=== Checking for problematic patterns in test files ==="

# Pattern 1: Module-level client/connection creation
echo "Checking for module-level client/connection instantiation..."
grep -rn "^[a-zA-Z_][a-zA-Z0-9_]* = .*Client()" tests/ self_fixing_engineer/tests/ omnicore_engine/tests/ generator/tests/ 2>/dev/null || echo "  None found"

# Pattern 2: Module-level imports from main code that might trigger initialization
echo ""
echo "Checking for direct imports of heavy modules..."
grep -rn "^from omnicore_engine.meta_supervisor import" tests/ self_fixing_engineer/tests/ omnicore_engine/tests/ generator/tests/ 2>/dev/null || echo "  None found"
grep -rn "^from self_fixing_engineer.arbiter import" tests/ self_fixing_engineer/tests/ omnicore_engine/tests/ generator/tests/ 2>/dev/null || echo "  None found"

# Pattern 3: Imports from conftest in test files (circular import risk)
echo ""
echo "Checking for imports from conftest..."
grep -rn "^from conftest import\|^import conftest" tests/ self_fixing_engineer/tests/ omnicore_engine/tests/ generator/tests/ 2>/dev/null || echo "  None found"

# Pattern 4: Module-level function calls
echo ""
echo "Checking for module-level function calls..."
grep -rn "^[a-zA-Z_][a-zA-Z0-9_]* = .*load_.*(" tests/ self_fixing_engineer/tests/ omnicore_engine/tests/ generator/tests/ 2>/dev/null || echo "  None found"

echo ""
echo "=== Pattern check complete ==="
