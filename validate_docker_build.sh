#!/bin/bash
# Docker Build Validation Script for Code Factory Platform
# This script validates that the unified Docker build works correctly

set -e  # Exit on error

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}==================================================${NC}"
echo -e "${BLUE}Code Factory Platform - Docker Build Validation${NC}"
echo -e "${BLUE}==================================================${NC}"
echo ""

# Check if Docker is installed
echo -e "${YELLOW}1. Checking Docker installation...${NC}"
if ! command -v docker &> /dev/null; then
    echo -e "${RED}✗ Docker is not installed${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Docker is installed: $(docker --version)${NC}"
echo ""

# Check if Docker daemon is running
echo -e "${YELLOW}2. Checking Docker daemon...${NC}"
if ! docker info &> /dev/null; then
    echo -e "${RED}✗ Docker daemon is not running${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Docker daemon is running${NC}"
echo ""

# Check if docker-compose is available
echo -e "${YELLOW}3. Checking Docker Compose...${NC}"
if docker compose version &> /dev/null; then
    echo -e "${GREEN}✓ Docker Compose is available: $(docker compose version)${NC}"
elif command -v docker-compose &> /dev/null; then
    echo -e "${GREEN}✓ Docker Compose is available: $(docker-compose --version)${NC}"
else
    echo -e "${RED}✗ Docker Compose is not installed${NC}"
    exit 1
fi
echo ""

# Build the unified platform image
echo -e "${YELLOW}4. Building unified platform image...${NC}"
echo -e "${BLUE}   Using: docker build -t code-factory:validate -f Dockerfile .${NC}"
if docker build --build-arg SKIP_HEAVY_DEPS=1 -t code-factory:validate -f Dockerfile . > /tmp/docker_build.log 2>&1; then
    echo -e "${GREEN}✓ Build successful${NC}"
    
    # Get image size
    IMAGE_SIZE=$(docker images code-factory:validate --format "{{.Size}}")
    echo -e "${GREEN}  Image size: ${IMAGE_SIZE}${NC}"
else
    echo -e "${RED}✗ Build failed. Check /tmp/docker_build.log for details${NC}"
    tail -20 /tmp/docker_build.log
    exit 1
fi
echo ""

# Verify image structure
echo -e "${YELLOW}5. Verifying image structure...${NC}"
if docker run --rm code-factory:validate ls -la /app/generator /app/omnicore_engine /app/self_fixing_engineer > /dev/null 2>&1; then
    echo -e "${GREEN}✓ All modules present in image${NC}"
else
    echo -e "${RED}✗ Image structure validation failed${NC}"
    exit 1
fi
echo ""

# Verify Python environment
echo -e "${YELLOW}6. Verifying Python environment...${NC}"
PYTHON_VERSION=$(docker run --rm code-factory:validate python --version 2>&1)
echo -e "${GREEN}✓ ${PYTHON_VERSION}${NC}"
echo ""

# Test docker-compose config
echo -e "${YELLOW}7. Validating docker-compose.yml...${NC}"
if docker compose config > /dev/null 2>&1; then
    echo -e "${GREEN}✓ docker-compose.yml is valid${NC}"
else
    echo -e "${RED}✗ docker-compose.yml validation failed${NC}"
    exit 1
fi
echo ""

# Summary
echo -e "${BLUE}==================================================${NC}"
echo -e "${GREEN}✓ All validation checks passed!${NC}"
echo -e "${BLUE}==================================================${NC}"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo -e "  1. Start services: ${BLUE}make docker-up${NC} or ${BLUE}docker compose up -d${NC}"
echo -e "  2. View logs: ${BLUE}make docker-logs${NC} or ${BLUE}docker compose logs -f${NC}"
echo -e "  3. Check health: ${BLUE}make health-check${NC}"
echo ""
echo -e "${YELLOW}Cleanup:${NC}"
echo -e "  Remove validation image: ${BLUE}docker rmi code-factory:validate${NC}"
echo ""
