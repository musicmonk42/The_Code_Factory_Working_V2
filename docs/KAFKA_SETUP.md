# Kafka Setup and Configuration Guide

## Quick Fix for DUPLICATE_BROKER_REGISTRATION Error

If you're experiencing the `DUPLICATE_BROKER_REGISTRATION` error, follow these steps for a quick resolution:

### Automated Fix (Recommended)

```bash
# Run the automated setup script (cleans up and restarts Kafka)
./scripts/kafka-setup.sh setup

# Or simply run without arguments (default is setup)
./scripts/kafka-setup.sh
```

This script will:
1. Stop and remove existing Kafka/Zookeeper containers
2. Remove volumes to clear stale metadata
3. Start fresh Kafka and Zookeeper instances
4. Wait for Kafka to be ready
5. Create required topics (audit-events, audit-events-dlq, job-completed)
6. Verify the setup

### Manual Fix

If you prefer to fix manually:

```bash
# 1. Stop and remove existing containers and volumes
docker-compose -f docker-compose.kafka.yml down -v

# 2. Start services fresh
docker-compose -f docker-compose.kafka.yml up -d

# 3. Wait for Kafka to be ready (30 seconds)
sleep 30

# 4. Create required topics
docker exec codefactory-kafka kafka-topics \
  --create --if-not-exists \
  --topic audit-events \
  --bootstrap-server localhost:9092 \
  --partitions 3 --replication-factor 1

docker exec codefactory-kafka kafka-topics \
  --create --if-not-exists \
  --topic audit-events-dlq \
  --bootstrap-server localhost:9092 \
  --partitions 1 --replication-factor 1

docker exec codefactory-kafka kafka-topics \
  --create --if-not-exists \
  --topic job-completed \
  --bootstrap-server localhost:9092 \
  --partitions 3 --replication-factor 1

# 5. Verify setup
docker exec codefactory-kafka kafka-topics --list --bootstrap-server localhost:9092
```

### What Causes DUPLICATE_BROKER_REGISTRATION?

This error occurs when:
- A previous Kafka instance registered with the same broker ID but didn't cleanly unregister
- Stale metadata exists in Zookeeper
- Multiple Kafka instances try to use the same broker ID

The fix involves **completely removing the Kafka and Zookeeper volumes** to clear all stale metadata.

---

## Overview

The Code Factory Platform uses Apache Kafka for audit event streaming and message bus functionality. Kafka is **optional** and the platform can operate without it using graceful degradation.

## Quick Start

### Option 1: Run Without Kafka (Development)

For development environments where Kafka is not needed:

```bash
# Enable dry-run mode (no actual Kafka sends)
export KAFKA_DEV_DRY_RUN=true

# Or simply don't set Kafka bootstrap servers
# unset KAFKA_BOOTSTRAP_SERVERS
```

The platform will log Kafka events instead of sending them to Kafka brokers.

### Option 2: Local Kafka with Docker

For local development with Kafka:

```bash
# Start Kafka using Docker Compose
docker-compose -f docker-compose.kafka.yml up -d

# Verify Kafka is running
docker-compose -f docker-compose.kafka.yml ps

# View Kafka logs
docker-compose -f docker-compose.kafka.yml logs -f kafka
```

### Option 3: Production Kafka Cluster

For production environments with an existing Kafka cluster:

```bash
# Set Kafka bootstrap servers
export KAFKA_BOOTSTRAP_SERVERS=kafka-broker1:9092,kafka-broker2:9092,kafka-broker3:9092
export KAFKA_TOPIC=audit-events
export KAFKA_DLQ_TOPIC=audit-events-dlq

# Enable security (if required)
export KAFKA_SECURITY_PROTOCOL=SASL_SSL
export KAFKA_SASL_MECHANISM=SCRAM-SHA-256
export KAFKA_SASL_USERNAME=your-username
export KAFKA_SASL_PASSWORD=your-password

# Optional: SSL certificates
export KAFKA_SSL_CAFILE=/path/to/ca-cert.pem
export KAFKA_SSL_CERTFILE=/path/to/client-cert.pem
export KAFKA_SSL_KEYFILE=/path/to/client-key.pem
```

## Configuration

### Essential Settings

```bash
# Kafka broker addresses (comma-separated)
KAFKA_BOOTSTRAP_SERVERS=localhost:9092

# Topic for audit events
KAFKA_TOPIC=audit-events

# Dead letter queue for failed messages
KAFKA_DLQ_TOPIC=audit-events-dlq
```

### Graceful Degradation

The platform includes built-in mechanisms to handle Kafka unavailability:

1. **Dry-run mode**: Set `KAFKA_DEV_DRY_RUN=true` to disable actual sends
2. **Automatic retry**: Failed sends are retried with exponential backoff
3. **Circuit breaker**: Prevents retry storms when Kafka is down
4. **Dead letter queue**: Permanently failed messages are sent to DLQ topic

```bash
# Enable dry-run mode (development only)
KAFKA_DEV_DRY_RUN=true
```

### Retry and Backoff Configuration

Control how the platform handles Kafka connection failures:

```bash
# Maximum retry attempts per message
KAFKA_MAX_RETRIES=6

# Initial backoff time in milliseconds
KAFKA_BASE_BACKOFF_MS=100

# Maximum backoff time (30 seconds)
KAFKA_MAX_BACKOFF_MS=30000

# Total retry window (2 minutes)
KAFKA_MAX_RETRY_TOTAL_MS=120000
```

**How it works:**
- First retry: 100ms delay
- Second retry: ~200ms delay (exponential backoff)
- Third retry: ~400ms delay
- Continues with jitter up to max backoff (30s)
- Stops after 2 minutes total or 6 attempts

### Performance Tuning

Optimize Kafka producer performance:

```bash
# Acknowledgment level
KAFKA_ACKS=all  # Options: 0, 1, all

# Enable idempotence (prevent duplicates)
KAFKA_ENABLE_IDEMPOTENCE=true

# Batching configuration
KAFKA_LINGER_MS=25  # Wait up to 25ms to batch messages
KAFKA_BATCH_BYTES=16384  # 16KB batch size
KAFKA_BATCH_MAX=100  # Max messages per batch

# Compression
KAFKA_COMPRESSION_TYPE=snappy  # Options: none, gzip, snappy, lz4, zstd

# Concurrent sends
KAFKA_SEND_CONCURRENCY=8  # Number of parallel send operations

# In-flight requests
KAFKA_MAX_IN_FLIGHT=5  # Max concurrent requests to broker
```

### Queue Settings

Control internal message queue behavior:

```bash
# Internal queue size
KAFKA_QUEUE_MAXSIZE=5000

# Queue overflow policy
KAFKA_QUEUE_DROP_POLICY=block  # Options: block, drop_oldest, drop_newest

# Flush interval
KAFKA_FLUSH_INTERVAL_MS=200  # Flush batch every 200ms

# Request timeout
KAFKA_REQUEST_TIMEOUT_MS=30000  # 30 second timeout
```

### Security Configuration

For secure Kafka clusters:

```bash
# Security protocol
KAFKA_SECURITY_PROTOCOL=SASL_SSL  # Options: PLAINTEXT, SSL, SASL_PLAINTEXT, SASL_SSL

# SASL authentication
KAFKA_SASL_MECHANISM=SCRAM-SHA-256  # Options: PLAIN, SCRAM-SHA-256, SCRAM-SHA-512
KAFKA_SASL_USERNAME=your-username
KAFKA_SASL_PASSWORD=your-password

# SSL/TLS certificates
KAFKA_SSL_CAFILE=/path/to/ca-cert.pem
KAFKA_SSL_CERTFILE=/path/to/client-cert.pem
KAFKA_SSL_KEYFILE=/path/to/client-key.pem

# Allow plaintext in dev (NOT recommended for production)
KAFKA_ALLOW_PLAINTEXT=false
```

## Docker Compose Setup

### Using the Provided Kafka Configuration

The repository includes a complete Kafka setup in `docker-compose.kafka.yml`. This is the **recommended** way to run Kafka for development and testing.

**Quick Start:**

```bash
# Automated setup (recommended)
./scripts/kafka-setup.sh

# Or manually start services
docker-compose -f docker-compose.kafka.yml up -d

# View logs
docker-compose -f docker-compose.kafka.yml logs -f kafka

# Stop services
docker-compose -f docker-compose.kafka.yml down
```

The `docker-compose.kafka.yml` file includes:
- **Zookeeper**: Coordination service for Kafka
- **Kafka Broker**: Single broker with proper listener configuration
- **Health Checks**: Ensures services are ready before accepting connections
- **Data Persistence**: Named volumes for Zookeeper and Kafka data
- **Network**: Connects to `codefactory-network` (same as main services)

**Configuration Details:**
- Kafka is accessible at `kafka:9092` from Docker containers
- Kafka is accessible at `localhost:9093` from the host machine
- Zookeeper is accessible at `localhost:2181`
- Auto-create topics is enabled for convenience

### Basic Kafka Setup

If you need a custom configuration, here's a minimal example:

```yaml
version: '3.8'

services:
  zookeeper:
    image: confluentinc/cp-zookeeper:7.5.0
    environment:
      ZOOKEEPER_CLIENT_PORT: 2181
      ZOOKEEPER_TICK_TIME: 2000
    ports:
      - "2181:2181"

  kafka:
    image: confluentinc/cp-kafka:7.5.0
    depends_on:
      - zookeeper
    ports:
      - "9092:9092"
    environment:
      KAFKA_BROKER_ID: 1
      KAFKA_ZOOKEEPER_CONNECT: zookeeper:2181
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://localhost:9092
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
      KAFKA_TRANSACTION_STATE_LOG_MIN_ISR: 1
      KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR: 1
```

Start the services:

```bash
docker-compose -f docker-compose.kafka.yml up -d
```

### Create Required Topics

**Automated (Recommended):**

The `kafka-setup.sh` script automatically creates all required topics:

```bash
./scripts/kafka-setup.sh setup
```

**Manual Creation:**

If you need to manually create topics:

```bash
# Create audit events topic
docker exec codefactory-kafka kafka-topics \
  --create --if-not-exists \
  --topic audit-events \
  --bootstrap-server localhost:9092 \
  --partitions 3 \
  --replication-factor 1

# Create dead letter queue topic
docker exec codefactory-kafka kafka-topics \
  --create --if-not-exists \
  --topic audit-events-dlq \
  --bootstrap-server localhost:9092 \
  --partitions 1 \
  --replication-factor 1

# Create job completion topic
docker exec codefactory-kafka kafka-topics \
  --create --if-not-exists \
  --topic job-completed \
  --bootstrap-server localhost:9092 \
  --partitions 3 \
  --replication-factor 1

# List topics
docker exec codefactory-kafka kafka-topics \
  --list \
  --bootstrap-server localhost:9092
```

## Troubleshooting

### DUPLICATE_BROKER_REGISTRATION Error

**Symptom:**
```
[BrokerLifecycleManager id=1] Unable to register broker 1 because the controller returned error DUPLICATE_BROKER_REGISTRATION
ERROR Encountered fatal fault: Error starting LogManager
```

**Root Cause:**
- Stale Kafka broker registration in Zookeeper
- Previous instance didn't cleanly shut down
- Multiple instances trying to use the same broker ID

**Solution:**

1. **Use the automated fix script:**
   ```bash
   ./scripts/kafka-setup.sh setup
   ```

2. **Or manually clean up:**
   ```bash
   # Stop everything
   docker-compose -f docker-compose.kafka.yml down -v
   
   # Remove any orphaned containers
   docker rm -f codefactory-kafka codefactory-zookeeper 2>/dev/null || true
   
   # Start fresh
   docker-compose -f docker-compose.kafka.yml up -d
   ```

3. **Verify the fix:**
   ```bash
   # Check container status
   docker-compose -f docker-compose.kafka.yml ps
   
   # View Kafka logs (should show successful startup)
   docker-compose -f docker-compose.kafka.yml logs kafka | grep -i "started"
   
   # Test broker API
   docker exec codefactory-kafka kafka-broker-api-versions --bootstrap-server localhost:9092
   ```

### Connection Refused Errors

**Symptom:**
```
Failed to connect to kafka:9092: Connection refused
```

**Solutions:**

1. **Check if Kafka is running:**
   ```bash
   docker-compose -f docker-compose.kafka.yml ps
   # Or use the script
   ./scripts/kafka-setup.sh status
   ```

2. **Enable dry-run mode temporarily:**
   ```bash
   export KAFKA_DEV_DRY_RUN=true
   ```

3. **Check Kafka logs:**
   ```bash
   docker-compose -f docker-compose.kafka.yml logs kafka
   # Or use the script
   ./scripts/kafka-setup.sh logs
   ```

4. **Verify network connectivity:**
   ```bash
   # From host machine
   telnet localhost 9093
   # or
   nc -zv localhost 9093
   
   # From Docker container (should use kafka:9092)
   docker exec codefactory-platform nc -zv kafka 9092
   ```

5. **Restart Kafka:**
   ```bash
   ./scripts/kafka-setup.sh restart
   ```

### Retry Storms

**Symptom:**
- High CPU usage
- Thousands of retry log messages
- System performance degradation

**Solutions:**

1. **Check current backoff settings:**
   ```bash
   echo $KAFKA_MAX_BACKOFF_MS
   echo $KAFKA_MAX_RETRY_TOTAL_MS
   ```

2. **Increase backoff intervals:**
   ```bash
   export KAFKA_MAX_BACKOFF_MS=60000  # 60 seconds
   export KAFKA_MAX_RETRY_TOTAL_MS=300000  # 5 minutes
   ```

3. **Enable dry-run mode:**
   ```bash
   export KAFKA_DEV_DRY_RUN=true
   ```

4. **Reduce retry attempts:**
   ```bash
   export KAFKA_MAX_RETRIES=3
   ```

### Authentication Failures

**Symptom:**
```
Authentication failed: Invalid credentials
```

**Solutions:**

1. **Verify credentials:**
   ```bash
   echo $KAFKA_SASL_USERNAME
   # Don't echo password in production!
   ```

2. **Check SASL mechanism:**
   ```bash
   echo $KAFKA_SASL_MECHANISM  # Should match server configuration
   ```

3. **Test with kafka-console-producer:**
   ```bash
   kafka-console-producer --bootstrap-server localhost:9092 \
     --topic test \
     --producer-property security.protocol=SASL_SSL \
     --producer-property sasl.mechanism=SCRAM-SHA-256 \
     --producer-property sasl.jaas.config='...'
   ```

### Message Delivery Issues

**Symptom:**
- Messages not appearing in Kafka topics
- High DLQ message count

**Solutions:**

1. **Check producer logs:**
   ```bash
   # Enable debug logging
   export LOG_LEVEL=DEBUG
   ```

2. **Verify topic exists:**
   ```bash
   docker-compose -f docker-compose.kafka.yml exec kafka \
     kafka-topics --list --bootstrap-server localhost:9092
   ```

3. **Check message size:**
   ```bash
   # Increase max message size if needed
   export KAFKA_MAX_RECORD_BYTES=1000000  # 1MB
   ```

4. **Monitor DLQ topic:**
   ```bash
   docker-compose -f docker-compose.kafka.yml exec kafka \
     kafka-console-consumer \
     --bootstrap-server localhost:9092 \
     --topic audit-events-dlq \
     --from-beginning
   ```

## Monitoring

### Check Kafka Health

```bash
# Use the setup script for status
./scripts/kafka-setup.sh status

# Test connection
docker exec codefactory-kafka kafka-broker-api-versions --bootstrap-server localhost:9092

# Monitor consumer lag
docker exec codefactory-kafka kafka-consumer-groups --bootstrap-server localhost:9092 --describe --all-groups

# View topic data
docker exec codefactory-kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic audit-events \
  --from-beginning \
  --max-messages 10

# List all topics
./scripts/kafka-setup.sh topics
```

### Prometheus Metrics

The platform exposes Kafka-related Prometheus metrics:

- `omnicore_kafka_events_total` - Total events sent to Kafka
- `kafka_sent` - Successfully sent messages (by topic)
- `kafka_dropped` - Dropped messages (by topic and reason)
- `kafka_latency_seconds` - Message latency histogram
- `kafka_queue_depth` - Current queue depth

Access metrics at: `http://localhost:8001/metrics`

## Best Practices

### Development

1. **Use dry-run mode** when Kafka is not needed:
   ```bash
   export KAFKA_DEV_DRY_RUN=true
   ```

2. **Start with local Docker Kafka** for integration testing

3. **Enable debug logging** to troubleshoot issues:
   ```bash
   export LOG_LEVEL=DEBUG
   ```

### Production

1. **Use a dedicated Kafka cluster** with proper replication

2. **Enable authentication and encryption:**
   ```bash
   export KAFKA_SECURITY_PROTOCOL=SASL_SSL
   export KAFKA_SASL_MECHANISM=SCRAM-SHA-256
   ```

3. **Configure appropriate retry settings** to prevent retry storms:
   ```bash
   export KAFKA_MAX_RETRIES=6
   export KAFKA_MAX_BACKOFF_MS=30000
   export KAFKA_MAX_RETRY_TOTAL_MS=120000
   ```

4. **Set up monitoring and alerting** for:
   - Connection failures
   - High DLQ message count
   - Producer lag
   - Queue depth

5. **Use idempotence** to prevent duplicate messages:
   ```bash
   export KAFKA_ENABLE_IDEMPOTENCE=true
   export KAFKA_ACKS=all
   ```

6. **Configure dead letter queue** for failed messages:
   ```bash
   export KAFKA_DLQ_TOPIC=audit-events-dlq
   ```

7. **Never use dry-run mode in production:**
   ```bash
   # Verify this is false or unset
   echo $KAFKA_DEV_DRY_RUN
   ```

## Migration Checklist

When deploying Kafka support:

- [ ] Install and configure Kafka cluster
- [ ] Create required topics (audit-events, audit-events-dlq)
- [ ] Configure authentication (SASL/SSL)
- [ ] Set environment variables
- [ ] Test connection with kafka-console-producer
- [ ] Enable monitoring and alerting
- [ ] Run load tests to verify performance
- [ ] Document runbook for operations team
- [ ] Set up backup/archival for audit logs
- [ ] Configure retention policies

## References

- [Apache Kafka Documentation](https://kafka.apache.org/documentation/)
- [Confluent Platform Documentation](https://docs.confluent.io/)
- [Kafka Producer Configuration](https://kafka.apache.org/documentation/#producerconfigs)
- [Circuit Breaker Pattern](https://martinfowler.com/bliki/CircuitBreaker.html)
- [Kafka Best Practices](https://docs.confluent.io/platform/current/kafka/deployment.html)
