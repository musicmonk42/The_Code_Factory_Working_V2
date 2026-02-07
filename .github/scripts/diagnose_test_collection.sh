#!/bin/bash
# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

set -e

echo "=== Diagnosing test collection issues ==="
echo "Testing each test file individually to identify CPU-heavy files..."
echo ""

PROBLEM_FILES=()

# Function to test a single file
test_single_file() {
    local test_file="$1"
    local filename=$(basename "$test_file")
    
    printf "Testing %-50s ... " "$test_file"
    
    if timeout 10s pytest --collect-only --quiet --import-mode=importlib "$test_file" >/dev/null 2>&1; then
        echo "✓ OK"
        return 0
    else
        EXIT_CODE=$?
        if [ $EXIT_CODE -eq 124 ]; then
            echo "⚠️  TIMEOUT (>10s)"
        elif [ $EXIT_CODE -eq 152 ]; then
            echo "❌ CPU LIMIT EXCEEDED"
        else
            echo "⚠️  FAILED (exit $EXIT_CODE)"
        fi
        return 1
    fi
}

# Test all test files in common directories
for test_dir in "tests" "self_fixing_engineer/tests" "omnicore_engine/tests" "omnicore_engine/database/tests" "omnicore_engine/message_bus/tests" "generator/tests"; do
    if [ ! -d "$test_dir" ]; then
        continue
    fi
    
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "Directory: $test_dir"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    # Use process substitution to avoid subshell
    while IFS= read -r test_file; do
        if ! test_single_file "$test_file"; then
            PROBLEM_FILES+=("$test_file")
        fi
    done < <(find "$test_dir" -name "test_*.py" -type f 2>/dev/null)
    
    echo ""
done

# Summary
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "SUMMARY"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ ${#PROBLEM_FILES[@]} -eq 0 ]; then
    echo "✓ All test files collected successfully"
    exit 0
else
    echo "❌ Found ${#PROBLEM_FILES[@]} problematic test file(s):"
    printf '%s\n' "${PROBLEM_FILES[@]}"
    exit 1
fi
