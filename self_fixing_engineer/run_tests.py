#!/usr/bin/env python3
"""
Safe test runner that handles common issues
"""
import os
import sys

# Set environment variable to use Python protobuf implementation
os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"

# Run pytest with the arguments
import pytest

sys.exit(pytest.main(sys.argv[1:]))
