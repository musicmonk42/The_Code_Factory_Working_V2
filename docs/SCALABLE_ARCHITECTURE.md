<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# Scalable Architecture Guide

## Overview

The Code Factory platform is designed from the ground up for enterprise-scale deployments. This document describes the architectural patterns, technologies, and configurations that enable horizontal and vertical scaling, high availability, and performance optimization.

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Horizontal Scaling](#horizontal-scaling)
3. [Message Queue Architecture](#message-queue-architecture)
4. [Load Balancing & Distribution](#load-balancing--distribution)
5. [Caching Strategies](#caching-strategies)
6. [Database Scaling](#database-scaling)
7. [Auto-Scaling Mechanisms](#auto-scaling-mechanisms)
8. [Backpressure Management](#backpressure-management)
9. [Resilience & Fault Tolerance](#resilience--fault-tolerance)
10. [Distributed Coordination](#distributed-coordination)
11. [Resource Management](#resource-management)
12. [Monitoring & Observability](#monitoring--observability)
13. [Performance Optimizations](#performance-optimizations)
14. [Configuration Guide](#configuration-guide)
15. [Deployment Patterns](#deployment-patterns)
16. [Capacity Planning](#capacity-planning)

---

## Architecture Overview

The Code Factory implements a **multi-tier scalable architecture** with the following key characteristics:

```
┌─────────────────────────────────────────────────────────────┐
│                     Load Balancer / Ingress                 │
└────────────────────┬────────────────────────────────────────┘
                     │
         ┌───────────┴───────────┐
         │                       │
    ┌────▼────┐            ┌────▼────┐           ┌────────┐
    │ Pod 1   │            │ Pod 2   │    ...    │ Pod N  │
    │         │            │         │           │        │
    │ Sharded │            │ Sharded │           │Sharded │
    │ Message │            │ Message │           │Message │
    │  Bus    │            │  Bus    │           │  Bus   │
    └────┬────┘            └────┬────┘           └────┬───┘
         │                      │                     │
         └──────────┬───────────┴─────────────────────┘
                    │
         ┌──────────▼──────────┐
         │  Redis (Locks,      │
         │  Cache, Pub/Sub)    │
         └──────────┬──────────┘
                    │
         ┌──────────▼──────────┐
         │ PostgreSQL + Citus  │
         │ (Distributed SQL)   │
         └──────────┬──────────┘
                    │
         ┌──────────▼──────────┐
         │  Kafka (Optional)   │
         │  Event Streaming    │
         └─────────────────────┘
```

### Key Design Principles

1. **Stateless Application Tier**: All pods are stateless and can be scaled independently
2. **Sharded Internal Queue**: Each pod runs an internal sharded message bus for work distribution
3. **Distributed Coordination**: Redis provides distributed locks and pub/sub for cross-pod coordination
4. **Optional External Queue**: Kafka integration for event streaming and audit trail
5. **Horizontal Database Scaling**: Citus extension for PostgreSQL distribution

---

## Horizontal Scaling

### Kubernetes Horizontal Pod Autoscaler (HPA)

The platform automatically scales based on CPU and memory metrics.

**Configuration**: `helm/codefactory/templates/hpa.yaml`, `k8s/overlays/production/hpa.yaml`

```yaml
spec:
  minReplicas: 3  # Production minimum for HA
  maxReplicas: 10
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70  # Scale at 70% CPU
  - type: Resource
    resource:
      name: memory
      target:
        type: Utilization
        averageUtilization: 80  # Scale at 80% memory
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 30
      policies:
      - type: Percent
        value: 100  # Double pods rapidly under load
        periodSeconds: 30
    scaleDown:
      stabilizationWindowSeconds: 60
      policies:
      - type: Percent
        value: 50  # Scale down conservatively
        periodSeconds: 60
```

**Key Features**:
- **Aggressive Scale Up**: 100% increase every 30 seconds during load spikes
- **Conservative Scale Down**: 50% decrease every 60 seconds to avoid thrashing
- **Dual Metrics**: Both CPU and memory thresholds must be met
- **Minimum HA**: 3 replicas in production ensures availability during pod failures

**File References**:
- Helm HPA: `helm/codefactory/templates/hpa.yaml:1-47`
- K8s HPA: `k8s/overlays/production/hpa.yaml:1-42`

### Pod Disruption Budget (PDB)

Ensures high availability during cluster maintenance and updates.

**Configuration**: `k8s/overlays/production/pdb.yaml`

```yaml
spec:
  minAvailable: 1  # At least 1 pod always running
  selector:
    matchLabels:
      app: codefactory-api
```

**File Reference**: `k8s/overlays/production/pdb.yaml:1-12`

### Rolling Update Strategy

Zero-downtime deployments with controlled rollout.

**Configuration**: `k8s/base/api-deployment.yaml:13-17`

```yaml
strategy:
  type: RollingUpdate
  rollingUpdate:
    maxSurge: 1        # One extra pod during updates
    maxUnavailable: 0  # No downtime
```

---

## Message Queue Architecture

The platform uses a **three-tier message queue architecture**:

1. **Internal Sharded Message Bus** (always present)
2. **Redis Pub/Sub** (for cross-pod coordination)
3. **Kafka** (optional, for event streaming and audit)

### ShardedMessageBus - Core Distributed Queue

The heart of the scalability architecture, running inside each pod.

**File**: `omnicore_engine/message_bus/sharded_message_bus.py` (2240 lines)

#### Architecture

```
┌─────────────────────────────────────────────────┐
│           ShardedMessageBus                     │
│                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐     │
│  │ Shard 0  │  │ Shard 1  │  │ Shard N  │     │
│  │          │  │          │  │          │     │
│  │ Queue    │  │ Queue    │  │ Queue    │     │
│  │ (10k)    │  │ (10k)    │  │ (10k)    │     │
│  │          │  │          │  │          │     │
│  │ Workers  │  │ Workers  │  │ Workers  │     │
│  │ (4x)     │  │ (4x)     │  │ (4x)     │     │
│  └──────────┘  └──────────┘  └──────────┘     │
│         │              │              │        │
│         └──────────────┴──────────────┘        │
│                        │                       │
│              ┌─────────▼─────────┐             │
│              │ Consistent Hash   │             │
│              │      Ring         │             │
│              └───────────────────┘             │
└─────────────────────────────────────────────────┘
```

#### Key Configuration

- **Shard Count**: 4-8 (configurable, default 8)
- **Workers Per Shard**: 2-4 (configurable, default 4)
- **Max Queue Size**: 10,000 messages per shard
- **Total Capacity**: 80,000 messages per pod (8 shards × 10k)

**File References**:
- Initialization: `omnicore_engine/message_bus/sharded_message_bus.py:328-337`
- Thread pools: `omnicore_engine/message_bus/sharded_message_bus.py:343-363`
- Hash ring: `omnicore_engine/message_bus/sharded_message_bus.py:385-392`

#### Consistent Hash Ring

Messages are distributed to shards using a consistent hash ring based on topic name.

**Benefits**:
- Even distribution across shards
- Stable routing (same topic always goes to same shard)
- Efficient re-balancing on shard count changes

**File Reference**: `omnicore_engine/message_bus/sharded_message_bus.py:909-916`

#### Priority Queues

Each shard maintains two queues:
- **Normal Priority Queue**: Standard messages
- **High Priority Queue**: Critical messages (priority ≥ 5)

High-priority messages are processed first within each shard.

**File References**:
- Queue setup: `omnicore_engine/message_bus/sharded_message_bus.py:338-341`
- Priority handling: `omnicore_engine/message_bus/sharded_message_bus.py:942`

### Kafka Bridge Integration

Optional integration for event streaming, audit trail, and inter-service communication.

**File**: `omnicore_engine/message_bus/integrations/kafka_bridge.py` (732 lines)

#### Producer Configuration

Optimized for durability and throughput:

```python
producer_config = {
    "acks": "all",           # Wait for all replicas
    "linger_ms": 5,          # Batch messages for 5ms
    "batch_size": 16384,     # 16KB batches
    "compression_type": "gzip",
    "retries": 5,
    "enable_idempotence": True  # Exactly-once semantics
}
```

**File Reference**: `omnicore_engine/message_bus/integrations/kafka_bridge.py:202-210`

#### Consumer Configuration

Optimized for reliability:

```python
consumer_config = {
    "auto_offset_reset": "latest",
    "enable_auto_commit": False,  # Manual commit after processing
    "max_poll_records": 100,
    "session_timeout_ms": 30000
}
```

**File Reference**: `omnicore_engine/message_bus/integrations/kafka_bridge.py:193-200`

#### Circuit Breaker Integration

Kafka operations are protected by circuit breakers to prevent cascading failures.

**File References**: `omnicore_engine/message_bus/integrations/kafka_bridge.py:335-337, 484-485, 499-500`

### Kafka Sink Adapter

High-performance adapter for sending audit events to Kafka.

**File**: `omnicore_engine/message_bus/kafka_sink_adapter.py` (371 lines)

**Features**:
- **Graceful Degradation**: Works without Kafka (logs locally instead)
- **Bounded Concurrency**: Max 64 concurrent operations (configurable)
- **Circuit Breaker**: Optional protection against Kafka failures
- **Batch Emission**: `emit_many()` for high-throughput scenarios

**File References**:
- Graceful degradation: `omnicore_engine/message_bus/kafka_sink_adapter.py:39-44`
- Circuit breaker: `omnicore_engine/message_bus/kafka_sink_adapter.py:168-173`
- Concurrency: `omnicore_engine/message_bus/kafka_sink_adapter.py:152`

### Redis Integration

Used for cross-pod coordination and caching.

**Docker Configuration**: `docker-compose.production.yml:34-50`

```yaml
redis:
  image: redis:7.4-alpine
  volumes:
    - redis-data:/data
  command: redis-server --appendonly yes  # AOF persistence
  healthcheck:
    test: ["CMD", "redis-cli", "ping"]
    interval: 10s
    retries: 5
```

---

## Load Balancing & Distribution

### Consistent Hash Ring

The ShardedMessageBus uses a consistent hash ring to distribute topics across shards.

**Algorithm**:
1. Each shard is assigned a position on a 0-2^32 ring
2. Topic names are hashed to a ring position
3. Topics are assigned to the next shard clockwise on the ring
4. Re-balancing is minimized when shards are added/removed

**File References**:
- Initialization: `omnicore_engine/message_bus/sharded_message_bus.py:386-388`
- Topic mapping: `omnicore_engine/message_bus/sharded_message_bus.py:909-916`
- Shard addition: `omnicore_engine/message_bus/sharded_message_bus.py:1638-1642`
- Shard removal: `omnicore_engine/message_bus/sharded_message_bus.py:1678-1681`

### Message Priority Handling

Two-tier dispatch system:

1. **High-Priority Workers**: Process messages with priority ≥ 5
2. **Normal Workers**: Process all other messages

**File References**:
- High-priority queues: `omnicore_engine/message_bus/sharded_message_bus.py:338-341`
- Priority threshold: `omnicore_engine/message_bus/sharded_message_bus.py:942`

### Kubernetes Service

ClusterIP service with session affinity (optional).

**Configuration**: `k8s/base/api-deployment.yaml:246`

```yaml
kind: Service
metadata:
  name: codefactory-api
spec:
  type: ClusterIP
  ports:
  - port: 80
    targetPort: 8000
    protocol: TCP
    name: http
  - port: 9090
    targetPort: 9090
    protocol: TCP
    name: metrics
  selector:
    app: codefactory-api
```

---

## Caching Strategies

### Message Cache (Deduplication)

Prevents duplicate processing of messages with the same idempotency key.

**File**: `omnicore_engine/message_bus/sharded_message_bus.py:414-417`

**Configuration**:
- **TTL**: Configurable (default: 3600 seconds)
- **Storage**: In-memory per pod, optionally backed by Redis for cross-pod deduplication
- **Idempotency Keys**: Based on message content hash or explicit key

**File References**:
- MessageCache: `omnicore_engine/message_bus/sharded_message_bus.py:414-417`
- Redis integration: `omnicore_engine/message_bus/sharded_message_bus.py:1279-1282`

### Topic-to-Shard Mapping Cache

Caches topic-to-shard assignments to avoid repeated hash calculations.

**Cache Invalidation**: Triggered on shard count changes or manual re-balance.

**File References**: `omnicore_engine/message_bus/sharded_message_bus.py:389, 1643, 1694`

### Redis Caching Layer

Used for:
- Distributed locks (see [Distributed Coordination](#distributed-coordination))
- Cross-pod message deduplication
- Session state (if enabled)
- Rate limiting counters

**Helm Configuration**: `helm/codefactory/values.yaml:255-260`

```yaml
redis:
  host: codefactory-redis
  port: 6379
  password: changeme
  persistence:
    enabled: true
    size: 10Gi
```

---

## Database Scaling

### Citus Extension (Distributed PostgreSQL)

The platform uses Citus to scale PostgreSQL horizontally across multiple nodes.

**Image**: `citusdata/citus:12.1`

**File**: `docker-compose.production.yml:55`

#### Architecture

```
┌────────────────────────────────────┐
│       Citus Coordinator            │
│  (Query Planning & Distribution)   │
└────────┬────────────────┬──────────┘
         │                │
    ┌────▼────┐      ┌────▼────┐
    │ Worker  │      │ Worker  │
    │ Node 1  │      │ Node 2  │
    │         │      │         │
    │ Shard 1 │      │ Shard 2 │
    │ Shard 3 │      │ Shard 4 │
    └─────────┘      └─────────┘
```

#### Key Features

- **Transparent Sharding**: Application queries unchanged
- **Parallel Queries**: Coordinator distributes queries across workers
- **HA Replication**: Worker nodes can be replicated
- **Reference Tables**: Small tables duplicated to all workers

#### Configuration

**Environment Variable**: `ENABLE_CITUS=1` (default in production)

**Helm Values**: `helm/codefactory/values.yaml:188-191`

```yaml
database:
  poolSize: 50              # Connection pool size
  poolMaxOverflow: 20       # Additional connections under load
  retryAttempts: 3
  retryDelay: 1.0
```

### Connection Pooling

Efficient database connection management:

- **Pool Size**: 50 connections per pod
- **Max Overflow**: 20 additional connections during spikes
- **Timeout**: 30 seconds
- **Pool Recycle**: 3600 seconds (prevents stale connections)

**File Reference**: `helm/codefactory/values.yaml:188-191`

### Read Replicas

For read-heavy workloads, PostgreSQL read replicas can be configured:

```yaml
database:
  primary:
    host: postgres-primary
    port: 5432
  replicas:
    - host: postgres-replica-1
      port: 5432
    - host: postgres-replica-2
      port: 5432
```

*Note: Read replica support is available but not enabled by default.*

---

## Auto-Scaling Mechanisms

### Message Bus Auto-Scaling

The ShardedMessageBus automatically adjusts shard count and worker count based on load.

**File**: `omnicore_engine/message_bus/sharded_message_bus.py:1849-1882`

#### Shard Auto-Scaling Logic

```python
async def auto_scale_shards(self):
    """Automatically adjust shard count based on load metrics."""
    total_queue_size = sum(q.qsize() for q in self.queues)
    avg_queue_size = total_queue_size / len(self.queues)

    # Scale up if average queue size > 80% of max
    if avg_queue_size > (self.max_queue_size * 0.8):
        new_count = min(self.shard_count + 1, 16)  # Max 16 shards
        logger.info(f"Scaling up to {new_count} shards")
        await self._add_shard()

    # Scale down if average queue size < 20% of max
    elif avg_queue_size < (self.max_queue_size * 0.2) and self.shard_count > 2:
        new_count = max(self.shard_count - 1, 2)  # Min 2 shards
        logger.info(f"Scaling down to {new_count} shards")
        await self._remove_shard()
```

**Parameters**:
- **Scale Up Threshold**: 80% queue capacity
- **Scale Down Threshold**: 20% queue capacity
- **Min Shards**: 2
- **Max Shards**: 16
- **Check Interval**: 60 seconds

**File Reference**: `omnicore_engine/message_bus/sharded_message_bus.py:1849-1882`

#### Worker Auto-Scaling Logic

Dynamically adjusts worker count per shard based on message backlog.

**File**: `omnicore_engine/message_bus/sharded_message_bus.py:1804-1847`

```python
async def adjust_workers(self, shard_index: int):
    """Adjust worker count for a specific shard."""
    queue_size = self.queues[shard_index].qsize()
    current_workers = len(self.workers[shard_index])

    # Scale up workers if queue is growing
    if queue_size > 1000 and current_workers < 8:
        # Add 2 workers
        pass

    # Scale down workers if queue is empty
    elif queue_size < 100 and current_workers > 2:
        # Remove 1 worker
        pass
```

**Parameters**:
- **Min Workers Per Shard**: 2
- **Max Workers Per Shard**: 8
- **Scale Up Threshold**: 1000 messages queued
- **Scale Down Threshold**: 100 messages queued

### Kubernetes HPA (Pod-Level Scaling)

See [Horizontal Scaling](#horizontal-scaling) section for Kubernetes HPA configuration.

### Combined Scaling Strategy

The platform uses **three-tier scaling**:

1. **Worker Scaling** (seconds): Adjust thread pool size within each shard
2. **Shard Scaling** (minutes): Add/remove shards within each pod
3. **Pod Scaling** (minutes): Kubernetes HPA adds/removes pods

This approach provides:
- **Fast Response**: Worker scaling handles micro-bursts
- **Medium Response**: Shard scaling handles sustained load increases
- **Macro Response**: Pod scaling handles cluster-wide load

---

## Backpressure Management

### BackpressureManager

Prevents queue overflow by pausing message publishing when queues reach capacity.

**File**: `omnicore_engine/message_bus/backpressure.py` (93 lines)

#### Algorithm

```python
class BackpressureManager:
    def check_queue_load(self, shard_index: int) -> bool:
        """Check if shard queue is over threshold (80%)."""
        queue = self.message_bus.queues[shard_index]
        utilization = queue.qsize() / self.message_bus.max_queue_size
        return utilization > 0.8

    async def should_pause_publishes(self) -> bool:
        """Check if any shard is over capacity."""
        for shard_index in range(self.message_bus.shard_count):
            if self.check_queue_load(shard_index):
                return True
        return False
```

**File Reference**: `omnicore_engine/message_bus/backpressure.py:26-92`

### Shard-Level Flow Control

Individual shards can be paused without affecting other shards.

**File References**:
- Pause check: `omnicore_engine/message_bus/sharded_message_bus.py:932-934`
- Pause/resume: `omnicore_engine/message_bus/sharded_message_bus.py:2018-2032`

### Backpressure Strategy

When backpressure is triggered:

1. **Pause Publishing**: New messages are rejected with HTTP 429 (Too Many Requests)
2. **Drain Queue**: Existing workers continue processing
3. **Resume Publishing**: Once queue drops below 50%, publishing resumes
4. **Scale Up**: Auto-scaler adds shards/workers to prevent future backpressure

---

## Resilience & Fault Tolerance

### Circuit Breaker Pattern

Prevents cascading failures when external dependencies fail.

**File**: `omnicore_engine/message_bus/resilience.py` (100+ lines)

#### States

1. **Closed**: Normal operation, requests pass through
2. **Open**: Dependency failing, requests fail fast
3. **Half-Open**: Testing if dependency recovered

#### Configuration

```python
class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 5,      # Failures before opening
        recovery_timeout: float = 60.0,  # Seconds before half-open
        success_threshold: int = 3       # Successes before closing
    ):
        self.state = "closed"
```

#### Integration

- **Kafka Circuit**: `failure_threshold=3`, `timeout=60s`
- **Redis Circuit**: `failure_threshold=5`, `timeout=60s`

**File Reference**: `omnicore_engine/message_bus/sharded_message_bus.py:420-427`

### Retry Policies

Automatic retry with exponential backoff for transient failures.

**File**: `omnicore_engine/message_bus/resilience.py:28-35`

```python
class RetryPolicy:
    max_retries: int = 3
    backoff_factor: float = 0.01  # Exponential: 0.01, 0.02, 0.04, ...
```

### Dead Letter Queue (DLQ)

Failed messages are captured for manual replay or analysis.

**File**: `omnicore_engine/message_bus/dead_letter_queue.py`

#### DLQ Criteria

Messages sent to DLQ if:
- Priority ≥ 5 (high-priority messages)
- Retry count exceeded
- Permanent failure (e.g., invalid message format)

#### Replay Capability

```python
async def replay_failed_messages(self, max_age_seconds: int = 3600) -> int:
    """Replay messages from the DLQ."""
    # Query DLQ messages younger than max_age
    # Re-publish each message
    # Remove successfully replayed messages
    return replayed_count
```

**File Reference**: `omnicore_engine/message_bus/sharded_message_bus.py:2034-2191`

### Graceful Degradation

The platform continues operating with reduced functionality when dependencies fail:

- **Without Kafka**: Audit logs to file instead of streaming
- **Without Redis**: Single-pod operation (no distributed locks)
- **Without Citus**: Standard PostgreSQL mode

---

## Distributed Coordination

### Distributed Lock Implementation

Redis-based distributed locks for cross-pod coordination.

**File**: `server/distributed_lock.py` (434 lines)

#### Technology

Uses Redis `SET NX EX` (atomic set-if-not-exists with expiration):

```python
acquired = redis.set(
    name="lock:agent_initialization",
    value=uuid4().hex,  # Unique owner ID
    nx=True,            # Only set if doesn't exist
    ex=timeout          # Auto-expire after timeout
)
```

**File Reference**: `server/distributed_lock.py:165-169`

#### Lock Release

Atomic check-and-delete using Lua script:

```lua
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
```

**File Reference**: `server/distributed_lock.py:320-326`

#### Use Cases

1. **Agent Initialization**: Only one pod initializes shared resources
2. **Database Migrations**: Only one pod runs migrations
3. **Job Scheduling**: Prevent duplicate job execution
4. **Resource Allocation**: Coordinate access to shared resources

**File References**:
- Global startup lock: `server/distributed_lock.py:389-411`
- Lock acquisition with retry: `server/distributed_lock.py:185-224`

### Graceful Degradation

When Redis is unavailable:
- Single-pod operation allowed
- Multi-pod deployments require Redis

**File Reference**: `server/distributed_lock.py:185-224`

---

## Resource Management

### Kubernetes Resource Limits

Prevent resource exhaustion and ensure QoS.

**Configuration**: `k8s/base/api-deployment.yaml:198-204`

```yaml
resources:
  requests:
    memory: "1Gi"
    cpu: "500m"
  limits:
    memory: "4Gi"
    cpu: "2000m"
```

**Resource Classes**:
- **Requests**: Guaranteed resources (Kubernetes schedules based on this)
- **Limits**: Maximum resources (pod throttled/killed if exceeded)

### Helm Resource Configuration

Customizable via values.yaml:

**File**: `helm/codefactory/values.yaml:80-86`

```yaml
resources:
  requests:
    memory: "1Gi"
    cpu: "500m"
  limits:
    memory: "4Gi"
    cpu: "2000m"
```

### Docker Compose Resource Limits

For non-Kubernetes deployments:

**File**: `docker-compose.production.yml:237-244`

```yaml
deploy:
  resources:
    limits:
      cpus: '4'
      memory: 8G
    reservations:
      cpus: '2'
      memory: 4G
```

### Message Bus Rate Limiting

Per-client rate limiting to prevent abuse.

**Configuration**: `omnicore_engine/message_bus/sharded_message_bus.py:394-397`

```python
self.rate_limiter = RateLimiter(
    max_requests=1000,  # Max requests per window
    window_seconds=60   # Time window
)
```

### Message Size Validation

Prevents oversized messages from breaking queues.

**Configuration**: `omnicore_engine/message_bus/sharded_message_bus.py:195-203`

```python
MAX_MESSAGE_SIZE = 1 * 1024 * 1024  # 1MB

def validate_message_size(message: Dict[str, Any]) -> bool:
    size = len(json.dumps(message).encode('utf-8'))
    return size <= MAX_MESSAGE_SIZE
```

---

## Monitoring & Observability

### Prometheus Metrics

Comprehensive metrics for monitoring and alerting.

**Configuration**: `monitoring/prometheus.yml` (48 lines)

```yaml
global:
  scrape_interval: 15s      # Scrape every 15 seconds
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'codefactory-platform'
    static_configs:
      - targets: ['codefactory-api:9090']

  - job_name: 'redis'
    static_configs:
      - targets: ['redis:9121']

  - job_name: 'postgres'
    static_configs:
      - targets: ['postgres:9187']
```

**Retention**: 30 days

### Message Bus Metrics

Detailed metrics for the ShardedMessageBus:

**File**: `omnicore_engine/metrics.py`

| Metric | Type | Description |
|--------|------|-------------|
| `message_bus_queue_size` | Gauge | Queue size per shard |
| `message_bus_message_age` | Histogram | Message latency distribution |
| `message_bus_dispatch_duration` | Histogram | Dispatch time per message |
| `message_bus_topic_throughput` | Counter | Messages per topic |
| `message_bus_callback_latency` | Histogram | Callback execution time |
| `message_bus_callback_errors` | Counter | Callback failures per topic |
| `message_bus_publish_retries` | Counter | Retry attempts per shard |
| `kafka_connection_failures` | Counter | Kafka connection failures by reason |
| `kafka_health_check_status` | Gauge | Kafka availability (0/1) |

### Prometheus Pod Annotations

Enable automatic scraping:

**File**: `helm/codefactory/values.yaml:31-34`

```yaml
podAnnotations:
  prometheus.io/scrape: "true"
  prometheus.io/port: "9090"
  prometheus.io/path: "/metrics"
```

### ServiceMonitor Configuration

For Prometheus Operator:

**File**: `helm/codefactory/templates/servicemonitor.yaml`

```yaml
spec:
  selector:
    matchLabels:
      app: codefactory-api
  endpoints:
  - port: metrics
    interval: 30s
    scrapeTimeout: 10s
```

### Grafana Dashboards

Pre-configured dashboards for:
- System health overview
- Message bus performance
- Database connections and query latency
- Kubernetes pod metrics
- Application-specific metrics

**Docker Compose**: `docker-compose.production.yml:264-301`

**Access**: http://localhost:3000 (default credentials in docker-compose)

### Alert Manager

Automated alerting for critical conditions:

**Configuration**: `monitoring/alertmanager.yml`

**Alert Rules**: `monitoring/alerts.yml`

Example alerts:
- High CPU/memory usage
- Queue saturation (>80%)
- Message processing latency >5s
- Circuit breaker opened
- Pod crash loops

---

## Performance Optimizations

### Startup Optimization

Reduce pod startup time for faster scaling.

**Configuration**: `helm/codefactory/values.yaml:180-185`

```yaml
performance:
  parallelAgentLoading: true   # Load agents in parallel
  lazyLoadML: true             # Defer ML model loading
  skipImportValidation: true   # Skip import-time checks
  startupTimeout: 90           # Max startup time (seconds)
```

**File References**:
- Parallel loading: `helm/codefactory/values.yaml:183`
- Lazy ML: `helm/codefactory/values.yaml:184`
- Startup timeout: `helm/codefactory/values.yaml:185`

### Database Connection Pooling

Efficient database connection management:

**Configuration**: `helm/codefactory/values.yaml:188-191`

```yaml
database:
  poolSize: 50           # Normal pool size
  poolMaxOverflow: 20    # Additional connections under load
  retryAttempts: 3
  retryDelay: 1.0
```

**Benefits**:
- Reduced connection overhead
- Better resource utilization
- Graceful handling of connection spikes

### Worker Configuration

Gunicorn worker processes for parallel request handling:

**Configuration**: `docker-compose.production.yml:261`

```bash
gunicorn server.main:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --timeout 300 \
  --graceful-timeout 30 \
  --keep-alive 5 \
  --max-requests 1000 \
  --max-requests-jitter 50
```

**Parameters**:
- **Workers**: 4 (adjust based on CPU cores)
- **Max Requests**: 1000 (prevents memory leaks)
- **Max Requests Jitter**: 50 (prevents thundering herd)
- **Keep-Alive**: 5 seconds

### Async Event Loop

Fully async architecture using uvloop for performance:

- **ASGI Server**: Uvicorn with UvicornWorker
- **Event Loop**: uvloop (if available) or asyncio
- **Database**: asyncpg for PostgreSQL
- **Redis**: aioredis for async operations

### Message Bus Thread Pools

Optimized thread pool configuration:

**File**: `omnicore_engine/message_bus/sharded_message_bus.py:343-363`

```python
# Normal priority executors (one per shard)
self.executors = [
    ThreadPoolExecutor(max_workers=self.workers_per_shard)
    for _ in range(self.shard_count)
]

# High priority executors (one per shard)
self.high_priority_executors = [
    ThreadPoolExecutor(max_workers=self.workers_per_shard // 2)
    for _ in range(self.shard_count)
]

# Callback executors (one per shard)
self.callback_executors = [
    ThreadPoolExecutor(max_workers=8)
    for _ in range(self.shard_count)
]
```

**Total Threads Per Pod**:
- Normal: 4 workers × 8 shards = 32 threads
- High-priority: 2 workers × 8 shards = 16 threads
- Callbacks: 8 workers × 8 shards = 64 threads
- **Grand Total**: ~112 threads per pod

---

## Configuration Guide

### Environment Variables for Scalability

#### Message Bus

```bash
# Sharding
MESSAGE_BUS_SHARD_COUNT=8
MESSAGE_BUS_WORKERS_PER_SHARD=4
MESSAGE_BUS_MAX_QUEUE_SIZE=10000

# Monitoring
ENABLE_MESSAGE_BUS_GUARDIAN=1
MESSAGE_BUS_GUARDIAN_INTERVAL=30

# Auto-scaling
MESSAGE_BUS_AUTO_SCALE=1
MESSAGE_BUS_SCALE_UP_THRESHOLD=0.8
MESSAGE_BUS_SCALE_DOWN_THRESHOLD=0.2
```

**File Reference**: `helm/codefactory/values.yaml:194-198`

#### Database

```bash
# Connection pooling
DB_POOL_SIZE=50
DB_POOL_MAX_OVERFLOW=20
DB_POOL_TIMEOUT=30
DB_POOL_RECYCLE=3600

# Retry logic
DB_RETRY_ATTEMPTS=3
DB_RETRY_DELAY=1.0

# Citus
ENABLE_CITUS=1
```

**File Reference**: `helm/codefactory/values.yaml:188-191`

#### Feature Flags

```bash
# Core features
ENABLE_DATABASE=1
ENABLE_REDIS=1
ENABLE_KAFKA=0  # Optional

# Distributed features
ENABLE_CITUS=1  # Production only
ENABLE_DISTRIBUTED_LOCKS=1

# Performance
PARALLEL_AGENT_LOADING=1
LAZY_LOAD_ML=1
STARTUP_TIMEOUT=90
```

**File Reference**: `helm/codefactory/values.yaml:207-215`

### Helm Values

Complete scalability configuration via `values.yaml`:

```yaml
# Replica configuration
replicaCount: 3

# HPA configuration
autoscaling:
  enabled: true
  minReplicas: 3
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70
  targetMemoryUtilizationPercentage: 80

# Resource limits
resources:
  requests:
    memory: "1Gi"
    cpu: "500m"
  limits:
    memory: "4Gi"
    cpu: "2000m"

# Message bus
messageBus:
  shardCount: 8
  workersPerShard: 4
  maxQueueSize: 10000
  autoScale: true

# Database
database:
  poolSize: 50
  poolMaxOverflow: 20
  citus:
    enabled: true

# Redis
redis:
  enabled: true
  persistence:
    enabled: true
    size: 10Gi

# Kafka (optional)
kafka:
  enabled: false
```

**File**: `helm/codefactory/values.yaml`

---

## Deployment Patterns

### Small Scale (1-10 req/s)

**Configuration**:
- **Pods**: 1-2
- **Shards**: 4
- **Workers**: 2 per shard
- **Resources**: 500m CPU, 1Gi RAM

**Use Cases**: Development, small teams, prototyping

### Medium Scale (10-100 req/s)

**Configuration**:
- **Pods**: 2-5
- **Shards**: 6-8
- **Workers**: 3-4 per shard
- **Resources**: 1 CPU, 2Gi RAM
- **Redis**: Required
- **HPA**: Enabled

**Use Cases**: Small production, mid-sized teams

### Large Scale (100-1000 req/s)

**Configuration**:
- **Pods**: 5-10 (HPA controlled)
- **Shards**: 8
- **Workers**: 4 per shard
- **Resources**: 2 CPU, 4Gi RAM
- **Redis**: Required with persistence
- **Citus**: Enabled
- **Kafka**: Recommended
- **HPA**: Enabled with aggressive scaling
- **Monitoring**: Prometheus + Grafana + AlertManager

**Use Cases**: Production, large teams, enterprise

### Extra Large Scale (1000+ req/s)

**Configuration**:
- **Pods**: 10-20+ (HPA controlled)
- **Shards**: 12-16
- **Workers**: 6-8 per shard
- **Resources**: 4 CPU, 8Gi RAM
- **Redis**: Cluster mode (3+ nodes)
- **Citus**: Multi-node cluster
- **Kafka**: Multi-broker cluster
- **HPA**: Aggressive with custom metrics
- **Monitoring**: Full observability stack
- **CDN**: For static assets
- **Load Balancer**: Multi-region with health checks

**Use Cases**: Enterprise at scale, high-traffic production

---

## Capacity Planning

### Capacity Formulas

#### Message Throughput Per Pod

```
Throughput (msg/s) = (Shards × Workers) / Avg_Processing_Time

Example:
- Shards: 8
- Workers: 4
- Avg Processing Time: 0.1s per message

Throughput = (8 × 4) / 0.1 = 320 msg/s per pod
```

#### Queue Capacity

```
Total_Capacity = Shards × Max_Queue_Size

Example:
- Shards: 8
- Max Queue Size: 10,000

Total Capacity = 8 × 10,000 = 80,000 messages per pod
```

#### Pod Count

```
Required_Pods = (Peak_Throughput / Pod_Throughput) × Safety_Margin

Example:
- Peak Throughput: 1000 msg/s
- Pod Throughput: 320 msg/s
- Safety Margin: 1.5x (50% headroom)

Required Pods = (1000 / 320) × 1.5 = 4.7 ≈ 5 pods
```

### Resource Estimation

#### CPU Requirements

```
CPU_Per_Pod = (Workers × Avg_CPU_Per_Worker) + Overhead

Example:
- Workers: 32 (8 shards × 4 workers)
- Avg CPU per Worker: 0.05 cores
- Overhead: 0.5 cores (OS, logging, metrics)

CPU = (32 × 0.05) + 0.5 = 2.1 cores
```

#### Memory Requirements

```
Memory_Per_Pod = (Queue_Capacity × Avg_Message_Size) + Worker_Memory + Overhead

Example:
- Queue Capacity: 80,000 messages
- Avg Message Size: 1KB
- Worker Memory: 500MB
- Overhead: 500MB

Memory = (80,000 × 1KB) + 500MB + 500MB = 1.08GB ≈ 1.5GB
```

### Scaling Thresholds

| Metric | Scale Up | Scale Down | Duration |
|--------|----------|------------|----------|
| CPU | >70% | <30% | 30s / 60s |
| Memory | >80% | <40% | 30s / 60s |
| Queue Size | >80% | <20% | 60s / 120s |
| Message Age | >5s | <1s | 60s / 120s |

### Growth Planning

#### Vertical Scaling

Increase per-pod resources before horizontal scaling:

1. **Start**: 1 CPU, 2GB RAM
2. **First Increase**: 2 CPU, 4GB RAM (2x)
3. **Second Increase**: 4 CPU, 8GB RAM (2x)
4. **Limit**: 8 CPU, 16GB RAM (beyond this, scale horizontally)

#### Horizontal Scaling

Add pods once vertical scaling limits reached:

1. **Phase 1**: 1-3 pods (development/small production)
2. **Phase 2**: 3-5 pods (medium production)
3. **Phase 3**: 5-10 pods (large production)
4. **Phase 4**: 10+ pods (enterprise scale)

#### Infrastructure Scaling

Scale supporting infrastructure with application:

| Component | Small | Medium | Large | Extra Large |
|-----------|-------|--------|-------|-------------|
| Redis | Single | Single + Replica | Cluster (3 nodes) | Cluster (5+ nodes) |
| PostgreSQL | Single | Single + Replica | Citus (2 workers) | Citus (4+ workers) |
| Kafka | N/A | 1 broker | 3 brokers | 5+ brokers |
| Prometheus | Single | Single | HA (2 nodes) | Federated |

---

## Best Practices

### Do's

✅ **Enable HPA** in production for automatic scaling
✅ **Use Redis** for distributed locks in multi-pod deployments
✅ **Enable Citus** for large databases (>100GB)
✅ **Monitor queue sizes** and set alerts at 60% capacity
✅ **Use circuit breakers** for all external dependencies
✅ **Configure resource limits** to prevent resource exhaustion
✅ **Enable graceful shutdown** (SIGTERM handling)
✅ **Use startup probes** to handle slow agent loading
✅ **Enable Prometheus metrics** for all components
✅ **Test scaling** before production (load tests)

### Don'ts

❌ **Don't disable backpressure** (can cause OOM)
❌ **Don't run without resource limits** (can starve other pods)
❌ **Don't skip health checks** (can cause routing to unhealthy pods)
❌ **Don't use fixed replica counts** in production (use HPA)
❌ **Don't ignore metrics** (set up alerts)
❌ **Don't share Redis** across environments (isolation)
❌ **Don't exceed 80% capacity** for extended periods (add resources)
❌ **Don't deploy without testing** (run integration tests first)
❌ **Don't skip graceful shutdown** (can lose in-flight messages)
❌ **Don't use development configs** in production

### Checklist for Production

- [ ] HPA enabled with appropriate thresholds
- [ ] PDB configured (minAvailable: 1+)
- [ ] Resource requests and limits set
- [ ] Health checks configured (startup, liveness, readiness)
- [ ] Redis enabled with persistence
- [ ] Database connection pooling configured
- [ ] Citus enabled (for large databases)
- [ ] Prometheus metrics enabled
- [ ] Grafana dashboards imported
- [ ] Alert rules configured
- [ ] Circuit breakers enabled
- [ ] Backpressure enabled
- [ ] Graceful shutdown configured
- [ ] Distributed locks enabled
- [ ] Load testing completed
- [ ] Disaster recovery plan documented

---

## Related Documentation

- [Kubernetes Deployment Guide](KUBERNETES_DEPLOYMENT.md) - Detailed K8s deployment instructions
- [Helm Deployment Guide](HELM_DEPLOYMENT.md) - Helm chart configuration
- [Architecture Improvements](ARCHITECTURE_IMPROVEMENTS.md) - Recent architecture enhancements
- [OmniCore Architecture](../omnicore_engine/docs/ARCHITECTURE.md) - Core engine architecture
- [Deployment Guide](DEPLOYMENT.md) - General deployment instructions
- [Monitoring Guide](KAFKA_SETUP.md) - Kafka and monitoring setup

---

## Support

For scalability questions or issues:

- **Architecture Review**: Contact DevOps team
- **Performance Issues**: Check Grafana dashboards first
- **Scaling Issues**: Review HPA status and metrics
- **Resource Issues**: Check pod resource usage and limits

**Email**: support@novatraxlabs.com
**Issues**: <enterprise-repo-url>/issues

---

**Document Version**: 1.0.0
**Last Updated**: 2026-02-11
**Maintained By**: Novatrax Labs DevOps Team
