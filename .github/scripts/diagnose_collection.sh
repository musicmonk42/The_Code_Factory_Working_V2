#!/bin/bash
# Diagnose which conftest/test file causes collection slowdown
# This script helps identify CPU-intensive operations during pytest collection

set -x

echo "=== Pytest Collection Diagnostics ==="
echo "Running pytest collection with detailed output..."
echo ""

# Run pytest collection with minimal terminal processing to reduce overhead
pytest --collect-only -p no:terminal -q 2>&1 | head -100

echo ""
echo "=== Collection completed ==="
