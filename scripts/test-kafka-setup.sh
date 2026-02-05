#!/bin/bash
# Integration test for Kafka setup
# This script tests the kafka-setup.sh functionality without actually running Docker

set -e

echo "Testing kafka-setup.sh script..."

# Test 1: Script exists and is executable
if [ -x "./scripts/kafka-setup.sh" ]; then
    echo "✓ Script is executable"
else
    echo "✗ Script is not executable"
    exit 1
fi

# Test 2: Syntax validation
if bash -n scripts/kafka-setup.sh; then
    echo "✓ Script syntax is valid"
else
    echo "✗ Script has syntax errors"
    exit 1
fi

# Test 3: Help command works
if ./scripts/kafka-setup.sh help > /dev/null 2>&1; then
    echo "✓ Help command works"
else
    echo "✗ Help command failed"
    exit 1
fi

# Test 4: Troubleshoot command works
if ./scripts/kafka-setup.sh troubleshoot > /dev/null 2>&1; then
    echo "✓ Troubleshoot command works"
else
    echo "✗ Troubleshoot command failed"
    exit 1
fi

# Test 5: Docker Compose file validation
if docker compose -f docker-compose.kafka.yml config > /dev/null 2>&1; then
    echo "✓ Docker Compose file is valid"
else
    echo "✗ Docker Compose file has errors"
    exit 1
fi

# Test 6: Check required topics are defined in script
if grep -q "audit-events" scripts/kafka-setup.sh && \
   grep -q "audit-events-dlq" scripts/kafka-setup.sh && \
   grep -q "job-completed" scripts/kafka-setup.sh; then
    echo "✓ All required topics are defined"
else
    echo "✗ Missing required topic definitions"
    exit 1
fi

# Test 7: Check documentation exists
if [ -f "docs/KAFKA_SETUP.md" ]; then
    echo "✓ Kafka setup documentation exists"
else
    echo "✗ Kafka setup documentation missing"
    exit 1
fi

# Test 8: Check Quick Fix section in documentation
if grep -q "Quick Fix for DUPLICATE_BROKER_REGISTRATION" docs/KAFKA_SETUP.md; then
    echo "✓ Quick Fix section exists in documentation"
else
    echo "✗ Quick Fix section missing from documentation"
    exit 1
fi

# Test 9: Check README has Kafka section
if grep -q "Kafka Infrastructure" README.md; then
    echo "✓ README has Kafka Infrastructure section"
else
    echo "✗ README missing Kafka Infrastructure section"
    exit 1
fi

# Test 10: Verify script supports both docker-compose versions
if grep -q 'DOCKER_COMPOSE=' scripts/kafka-setup.sh; then
    echo "✓ Script detects docker-compose version"
else
    echo "✗ Script doesn't handle docker-compose version detection"
    exit 1
fi

echo ""
echo "All tests passed! ✓"
echo ""
echo "Note: This test validates the configuration files and script structure."
echo "To test actual Kafka functionality, run: ./scripts/kafka-setup.sh setup"
