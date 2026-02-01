#!/bin/bash
# Docker Security Scanning Script
# Scans Docker images for security vulnerabilities using multiple tools
#
# Usage: ./scripts/docker-security-scan.sh [IMAGE_NAME]
# Example: ./scripts/docker-security-scan.sh code-factory:latest

set -e

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

IMAGE_NAME="${1:-code-factory:latest}"

echo -e "${BLUE}============================================================${NC}"
echo -e "${BLUE}Docker Security Scan - ${IMAGE_NAME}${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""

# Check if image exists
if ! docker images | grep -q "$(echo $IMAGE_NAME | cut -d: -f1)"; then
    echo -e "${RED}✗ Image not found: ${IMAGE_NAME}${NC}"
    echo -e "${YELLOW}Build the image first with: docker build -t ${IMAGE_NAME} .${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Image found: ${IMAGE_NAME}${NC}"
echo ""

# Create results directory
RESULTS_DIR="./docker-scan-results"
mkdir -p "${RESULTS_DIR}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# Function to check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Scan with Trivy if available
if command_exists trivy; then
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}1. Running Trivy Vulnerability Scan...${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    
    # Run Trivy scan
    trivy image --severity HIGH,CRITICAL "${IMAGE_NAME}" | tee "${RESULTS_DIR}/trivy_${TIMESTAMP}.txt"
    
    # Generate JSON report
    trivy image --format json --output "${RESULTS_DIR}/trivy_${TIMESTAMP}.json" "${IMAGE_NAME}"
    
    echo ""
    echo -e "${GREEN}✓ Trivy scan complete${NC}"
    echo -e "  Results: ${RESULTS_DIR}/trivy_${TIMESTAMP}.txt"
    echo ""
else
    echo -e "${YELLOW}⚠ Trivy not installed. Install with:${NC}"
    echo -e "  curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin"
    echo ""
fi

# Scan with Docker Scan if available
if command_exists docker && docker scan --accept-license >/dev/null 2>&1; then
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}2. Running Docker Scan...${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    
    docker scan "${IMAGE_NAME}" | tee "${RESULTS_DIR}/docker_scan_${TIMESTAMP}.txt"
    
    echo ""
    echo -e "${GREEN}✓ Docker scan complete${NC}"
    echo -e "  Results: ${RESULTS_DIR}/docker_scan_${TIMESTAMP}.txt"
    echo ""
else
    echo -e "${YELLOW}⚠ Docker Scan not available. Enable with:${NC}"
    echo -e "  docker scan --accept-license"
    echo ""
fi

# Scan with Grype if available
if command_exists grype; then
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}3. Running Grype Vulnerability Scan...${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    
    grype "${IMAGE_NAME}" | tee "${RESULTS_DIR}/grype_${TIMESTAMP}.txt"
    
    # Generate JSON report
    grype "${IMAGE_NAME}" -o json > "${RESULTS_DIR}/grype_${TIMESTAMP}.json"
    
    echo ""
    echo -e "${GREEN}✓ Grype scan complete${NC}"
    echo -e "  Results: ${RESULTS_DIR}/grype_${TIMESTAMP}.txt"
    echo ""
else
    echo -e "${YELLOW}⚠ Grype not installed. Install with:${NC}"
    echo -e "  curl -sSfL https://raw.githubusercontent.com/anchore/grype/main/install.sh | sh -s -- -b /usr/local/bin"
    echo ""
fi

# Basic Docker image inspection
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${YELLOW}4. Docker Image Inspection${NC}"
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

# Check image size
IMAGE_SIZE=$(docker images "${IMAGE_NAME}" --format "{{.Size}}")
echo -e "Image Size: ${BLUE}${IMAGE_SIZE}${NC}"

# Check layers
LAYER_COUNT=$(docker history "${IMAGE_NAME}" --no-trunc | wc -l)
echo -e "Layer Count: ${BLUE}${LAYER_COUNT}${NC}"

# Check user
USER_INFO=$(docker inspect "${IMAGE_NAME}" --format '{{.Config.User}}')
if [ -z "$USER_INFO" ] || [ "$USER_INFO" == "root" ] || [ "$USER_INFO" == "0" ]; then
    echo -e "Running as: ${RED}root ✗${NC}"
    echo -e "  ${YELLOW}WARNING: Image runs as root user. This is a security risk.${NC}"
else
    echo -e "Running as: ${GREEN}${USER_INFO} ✓${NC}"
fi

# Check health check
HEALTHCHECK=$(docker inspect "${IMAGE_NAME}" --format '{{.Config.Healthcheck}}')
if [ "$HEALTHCHECK" == "<nil>" ] || [ -z "$HEALTHCHECK" ]; then
    echo -e "Health Check: ${YELLOW}Not configured ⚠${NC}"
else
    echo -e "Health Check: ${GREEN}Configured ✓${NC}"
fi

# Check exposed ports
PORTS=$(docker inspect "${IMAGE_NAME}" --format '{{range $key, $value := .Config.ExposedPorts}}{{$key}} {{end}}')
if [ -z "$PORTS" ]; then
    echo -e "Exposed Ports: ${YELLOW}None${NC}"
else
    echo -e "Exposed Ports: ${BLUE}${PORTS}${NC}"
fi

echo ""

# Security recommendations
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${YELLOW}5. Security Recommendations${NC}"
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

echo "✓ Use multi-stage builds to minimize image size"
echo "✓ Run as non-root user"
echo "✓ Keep base images updated"
echo "✓ Minimize installed packages"
echo "✓ Scan regularly for vulnerabilities"
echo "✓ Use specific version tags, not 'latest'"
echo "✓ Implement health checks"
echo "✓ Sign and verify images"
echo "✓ Use secrets management (not environment variables)"
echo "✓ Apply security updates promptly"

echo ""
echo -e "${BLUE}============================================================${NC}"
echo -e "${GREEN}✓ Security scan complete!${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""
echo -e "${YELLOW}Results saved to: ${RESULTS_DIR}/${NC}"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo "1. Review scan results for HIGH and CRITICAL vulnerabilities"
echo "2. Update dependencies if vulnerabilities found"
echo "3. Rebuild image: docker build --no-cache -t ${IMAGE_NAME} ."
echo "4. Rescan to verify fixes"
echo "5. See DOCKER_SECURITY.md for detailed guidance"
echo ""
