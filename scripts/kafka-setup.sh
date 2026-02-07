#!/bin/bash
# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# Kafka Setup and Management Script for Code Factory Platform
# This script manages Kafka and Zookeeper services, resolves DUPLICATE_BROKER_REGISTRATION errors,
# and creates required topics for the Code Factory Platform.

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
COMPOSE_FILE="docker-compose.kafka.yml"
KAFKA_CONTAINER="codefactory-kafka"
ZOOKEEPER_CONTAINER="codefactory-zookeeper"
KAFKA_BOOTSTRAP="localhost:9093"

# Determine which Docker Compose command to use
if docker compose version &>/dev/null 2>&1; then
    DOCKER_COMPOSE="docker compose"
else
    DOCKER_COMPOSE="docker-compose"
fi

# Topics to create
TOPICS=(
    "audit-events:3:1"           # audit-events with 3 partitions, replication factor 1
    "audit-events-dlq:1:1"       # dead letter queue with 1 partition, replication factor 1
    "job-completed:3:1"          # job completion events with 3 partitions, replication factor 1
)

# Helper functions
print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

print_info() {
    echo -e "${BLUE}ℹ $1${NC}"
}

print_header() {
    echo -e "\n${BLUE}═══════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════${NC}\n"
}

# Check if Docker is installed
check_docker() {
    if ! command -v docker &> /dev/null; then
        print_error "Docker is not installed. Please install Docker first."
        exit 1
    fi
    
    if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
        print_error "Docker Compose is not installed. Please install Docker Compose first."
        exit 1
    fi
    
    print_success "Docker and Docker Compose are installed"
}

# Check if compose file exists
check_compose_file() {
    if [ ! -f "$COMPOSE_FILE" ]; then
        print_error "Compose file $COMPOSE_FILE not found in current directory"
        print_info "Please run this script from the repository root"
        exit 1
    fi
    print_success "Found $COMPOSE_FILE"
}

# Stop and remove existing Kafka containers
cleanup_kafka() {
    print_header "Cleaning Up Existing Kafka Instances"
    
    # Stop and remove containers
    if docker ps -a | grep -q "$KAFKA_CONTAINER\|$ZOOKEEPER_CONTAINER"; then
        print_info "Stopping existing Kafka and Zookeeper containers..."
        $DOCKER_COMPOSE -f "$COMPOSE_FILE" down 2>/dev/null || true
        print_success "Stopped containers"
    else
        print_info "No existing containers found"
    fi
    
    # Remove volumes (this clears stale metadata)
    print_warning "Removing Kafka volumes to clear stale metadata..."
    $DOCKER_COMPOSE -f "$COMPOSE_FILE" down -v 2>/dev/null || true
    print_success "Removed volumes"
    
    # Additional cleanup for any orphaned containers
    for container in "$KAFKA_CONTAINER" "$ZOOKEEPER_CONTAINER"; do
        if docker ps -a -q -f name="$container" | grep -q .; then
            print_info "Removing orphaned container: $container"
            docker rm -f "$container" 2>/dev/null || true
        fi
    done
    
    print_success "Cleanup complete"
}

# Start Kafka services
start_kafka() {
    print_header "Starting Kafka Services"
    
    print_info "Starting Zookeeper and Kafka..."
    $DOCKER_COMPOSE -f "$COMPOSE_FILE" up -d
    
    print_success "Services started"
}

# Wait for Kafka to be ready
wait_for_kafka() {
    print_header "Waiting for Kafka to be Ready"
    
    local max_attempts=30
    local attempt=0
    
    print_info "Waiting for Kafka broker to be ready (max ${max_attempts}s)..."
    
    while [ $attempt -lt $max_attempts ]; do
        if docker exec "$KAFKA_CONTAINER" kafka-broker-api-versions --bootstrap-server localhost:9092 &>/dev/null; then
            print_success "Kafka is ready!"
            return 0
        fi
        
        attempt=$((attempt + 1))
        echo -n "."
        sleep 1
    done
    
    echo ""
    print_error "Kafka failed to become ready after ${max_attempts}s"
    print_info "Check logs with: $DOCKER_COMPOSE -f $COMPOSE_FILE logs kafka"
    return 1
}

# Create required topics
create_topics() {
    print_header "Creating Required Topics"
    
    for topic_config in "${TOPICS[@]}"; do
        IFS=':' read -r topic_name partitions replication_factor <<< "$topic_config"
        
        print_info "Creating topic: $topic_name (partitions=$partitions, replication=$replication_factor)"
        
        if docker exec "$KAFKA_CONTAINER" kafka-topics \
            --create \
            --if-not-exists \
            --topic "$topic_name" \
            --bootstrap-server localhost:9092 \
            --partitions "$partitions" \
            --replication-factor "$replication_factor" &>/dev/null; then
            print_success "Topic '$topic_name' created"
        else
            print_warning "Topic '$topic_name' may already exist or failed to create"
        fi
    done
}

# Verify setup
verify_setup() {
    print_header "Verifying Setup"
    
    # Check container status
    print_info "Checking container status..."
    if docker ps | grep -q "$KAFKA_CONTAINER"; then
        print_success "Kafka container is running"
    else
        print_error "Kafka container is not running"
        return 1
    fi
    
    if docker ps | grep -q "$ZOOKEEPER_CONTAINER"; then
        print_success "Zookeeper container is running"
    else
        print_error "Zookeeper container is not running"
        return 1
    fi
    
    # List topics
    print_info "Listing created topics..."
    docker exec "$KAFKA_CONTAINER" kafka-topics \
        --list \
        --bootstrap-server localhost:9092
    
    print_success "Setup verification complete"
}

# Display troubleshooting commands
show_troubleshooting() {
    print_header "Troubleshooting Commands"
    
    echo "View Kafka logs:"
    echo "  $DOCKER_COMPOSE -f $COMPOSE_FILE logs -f kafka"
    echo ""
    echo "View Zookeeper logs:"
    echo "  $DOCKER_COMPOSE -f $COMPOSE_FILE logs -f zookeeper"
    echo ""
    echo "List topics:"
    echo "  docker exec $KAFKA_CONTAINER kafka-topics --list --bootstrap-server localhost:9092"
    echo ""
    echo "Describe a topic:"
    echo "  docker exec $KAFKA_CONTAINER kafka-topics --describe --topic audit-events --bootstrap-server localhost:9092"
    echo ""
    echo "Test producer (send a test message):"
    echo "  echo 'test message' | docker exec -i $KAFKA_CONTAINER kafka-console-producer --broker-list localhost:9092 --topic audit-events"
    echo ""
    echo "Test consumer (read messages):"
    echo "  docker exec $KAFKA_CONTAINER kafka-console-consumer --bootstrap-server localhost:9092 --topic audit-events --from-beginning --max-messages 10"
    echo ""
    echo "Check broker API versions:"
    echo "  docker exec $KAFKA_CONTAINER kafka-broker-api-versions --bootstrap-server localhost:9092"
    echo ""
    echo "Stop services:"
    echo "  $DOCKER_COMPOSE -f $COMPOSE_FILE down"
    echo ""
    echo "Stop and remove volumes (complete cleanup):"
    echo "  $DOCKER_COMPOSE -f $COMPOSE_FILE down -v"
    echo ""
}

# Display status
show_status() {
    print_header "Kafka Service Status"
    
    $DOCKER_COMPOSE -f "$COMPOSE_FILE" ps
    
    echo ""
    print_info "Container health status:"
    docker ps --filter "name=$KAFKA_CONTAINER" --format "table {{.Names}}\t{{.Status}}"
    docker ps --filter "name=$ZOOKEEPER_CONTAINER" --format "table {{.Names}}\t{{.Status}}"
}

# Main setup function
full_setup() {
    print_header "Kafka Full Setup for Code Factory Platform"
    
    check_docker
    check_compose_file
    cleanup_kafka
    start_kafka
    
    if wait_for_kafka; then
        create_topics
        verify_setup
        
        print_header "Setup Complete!"
        print_success "Kafka is ready to use"
        print_info "Connection strings:"
        print_info "  - From Docker containers: kafka:9092"
        print_info "  - From host machine: localhost:9093"
        print_info "  - Zookeeper: localhost:2181"
        echo ""
        show_troubleshooting
    else
        print_error "Setup failed - Kafka is not ready"
        print_info "Run '$0 logs' to see error details"
        exit 1
    fi
}

# Show logs
show_logs() {
    print_info "Showing Kafka logs (Ctrl+C to exit)..."
    $DOCKER_COMPOSE -f "$COMPOSE_FILE" logs -f kafka
}

# Show usage
show_usage() {
    cat << EOF
Usage: $0 [COMMAND]

Kafka Setup and Management Script for Code Factory Platform

Commands:
  setup       Full setup: cleanup, start, create topics, verify (default)
  cleanup     Stop and remove Kafka containers and volumes
  start       Start Kafka services
  stop        Stop Kafka services
  restart     Restart Kafka services
  status      Show service status
  logs        Show Kafka logs (follow mode)
  topics      List all topics
  verify      Verify Kafka setup
  troubleshoot Show troubleshooting commands
  help        Show this help message

Examples:
  $0              # Run full setup (cleanup + start + create topics)
  $0 setup        # Same as above
  $0 logs         # View Kafka logs
  $0 status       # Check service status
  $0 cleanup      # Clean up and remove all data

For more information, see docs/KAFKA_SETUP.md
EOF
}

# Main command dispatcher
main() {
    local command="${1:-setup}"
    
    case "$command" in
        setup)
            full_setup
            ;;
        cleanup)
            check_docker
            check_compose_file
            cleanup_kafka
            ;;
        start)
            check_docker
            check_compose_file
            start_kafka
            wait_for_kafka
            ;;
        stop)
            check_docker
            check_compose_file
            print_info "Stopping Kafka services..."
            $DOCKER_COMPOSE -f "$COMPOSE_FILE" down
            print_success "Services stopped"
            ;;
        restart)
            check_docker
            check_compose_file
            print_info "Restarting Kafka services..."
            $DOCKER_COMPOSE -f "$COMPOSE_FILE" restart
            wait_for_kafka
            print_success "Services restarted"
            ;;
        status)
            check_docker
            check_compose_file
            show_status
            ;;
        logs)
            check_docker
            check_compose_file
            show_logs
            ;;
        topics)
            check_docker
            check_compose_file
            print_info "Listing topics..."
            docker exec "$KAFKA_CONTAINER" kafka-topics --list --bootstrap-server localhost:9092
            ;;
        verify)
            check_docker
            check_compose_file
            verify_setup
            ;;
        troubleshoot)
            show_troubleshooting
            ;;
        help|--help|-h)
            show_usage
            ;;
        *)
            print_error "Unknown command: $command"
            echo ""
            show_usage
            exit 1
            ;;
    esac
}

# Run main function
main "$@"
