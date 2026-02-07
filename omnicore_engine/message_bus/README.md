<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

\# Message Bus Module



A high-performance, distributed message bus system with sharding, priority queues, and resilience features for the Omnicore Engine.



\## Overview



The Message Bus module provides asynchronous, reliable message passing between components in a distributed system. It features automatic sharding for scalability, priority-based message processing, dead letter queue handling, and integration with external message brokers like Kafka and Redis.



\## Features



\### Core Capabilities

\- \*\*Sharded Architecture\*\*: Distributes messages across multiple shards for parallel processing

\- \*\*Priority Queues\*\*: Dual-queue system for normal and high-priority messages

\- \*\*Dynamic Sharding\*\*: Add/remove shards at runtime based on load

\- \*\*Message Deduplication\*\*: Idempotency support with configurable cache

\- \*\*Encryption\*\*: Built-in message encryption with key rotation support

\- \*\*Rate Limiting\*\*: Per-client rate limiting to prevent abuse



\### Resilience Features

\- \*\*Circuit Breakers\*\*: Protects against cascading failures with external services

\- \*\*Retry Policies\*\*: Configurable exponential backoff for failed operations

\- \*\*Dead Letter Queue\*\*: Captures and persists failed messages for investigation

\- \*\*Backpressure Management\*\*: Prevents queue overflow with dynamic flow control

\- \*\*Graceful Shutdown\*\*: Ensures message processing completes before termination



\### Integration

\- \*\*Kafka Bridge\*\*: Optional integration with Apache Kafka for distributed messaging

\- \*\*Redis Bridge\*\*: Optional Redis pub/sub integration

\- \*\*Database Persistence\*\*: High-priority message persistence for durability

\- \*\*Context Propagation\*\*: Automatic execution context propagation across async boundaries



\## Architecture



```

┌─────────────────────────────────────────────────────────────┐

│                     ShardedMessageBus                        │

├─────────────────────────────────────────────────────────────┤

│                                                              │

│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │

│  │   Shard 0    │  │   Shard 1    │  │   Shard 2    │     │

│  ├──────────────┤  ├──────────────┤  ├──────────────┤     │

│  │ Normal Queue │  │ Normal Queue │  │ Normal Queue │     │

│  │ Priority Q   │  │ Priority Q   │  │ Priority Q   │     │

│  │ Workers      │  │ Workers      │  │ Workers      │     │

│  └──────────────┘  └──────────────┘  └──────────────┘     │

│                                                              │

│  ┌─────────────────────────────────────────────────┐       │

│  │            Consistent Hash Ring                  │       │

│  └─────────────────────────────────────────────────┘       │

│                                                              │

│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │

│  │   DLQ        │  │ Rate Limiter │  │ Dedup Cache  │     │

│  └──────────────┘  └──────────────┘  └──────────────┘     │

│                                                              │

│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │

│  │ Kafka Bridge │  │ Redis Bridge │  │  Guardian    │     │

│  └──────────────┘  └──────────────┘  └──────────────┘     │

└─────────────────────────────────────────────────────────────┘

```



\## Installation



```bash

\# Install required dependencies

pip install cryptography pydantic structlog aiohttp



\# Optional dependencies for integrations

pip install kafka-python redis

```



\## Quick Start



\### Basic Usage



```python

from message\_bus.sharded\_message\_bus import ShardedMessageBus

from arbiter.config import ArbiterConfig



\# Initialize message bus

config = ArbiterConfig()

bus = ShardedMessageBus(config=config)



\# Subscribe to a topic

async def message\_handler(message):

&nbsp;   print(f"Received: {message.payload}")



await bus.subscribe("my.topic", message\_handler)



\# Publish a message

await bus.publish(

&nbsp;   topic="my.topic",

&nbsp;   payload={"data": "Hello, World!"},

&nbsp;   priority=5

)



\# Shutdown gracefully

await bus.shutdown()

```



\### Priority Messages



```python

\# High priority message (processed first)

await bus.publish(

&nbsp;   topic="critical.event",

&nbsp;   payload={"alert": "System critical"},

&nbsp;   priority=1  # Lower number = higher priority

)



\# Normal priority

await bus.publish(

&nbsp;   topic="normal.event",

&nbsp;   payload={"info": "Regular update"},

&nbsp;   priority=5

)

```



\### Request-Response Pattern



```python

\# Make a request and wait for response

response = await bus.request(

&nbsp;   topic="calc.service",

&nbsp;   payload={"operation": "add", "a": 5, "b": 3},

&nbsp;   timeout=5.0

)

print(f"Result: {response}")

```



\### Idempotent Messages



```python

\# Prevent duplicate processing

await bus.publish(

&nbsp;   topic="payment.process",

&nbsp;   payload={"amount": 100, "user": "123"},

&nbsp;   idempotency\_key="payment\_123\_unique",

&nbsp;   priority=1

)

```



\### Encrypted Messages



```python

\# Send encrypted message

await bus.publish(

&nbsp;   topic="secure.data",

&nbsp;   payload={"sensitive": "information"},

&nbsp;   encrypt=True

)

```



\## Configuration



\### Environment Variables



```python

\# Core settings

MESSAGE\_BUS\_SHARD\_COUNT = 4  # Number of shards

MESSAGE\_BUS\_MAX\_QUEUE\_SIZE = 10000  # Max messages per queue

MESSAGE\_BUS\_WORKERS\_PER\_SHARD = 2  # Worker threads per shard



\# Priority thresholds

PRIORITY\_THRESHOLD = 5  # Messages with priority < 5 use high-priority queue

DLQ\_PRIORITY\_THRESHOLD = 7  # Failed messages with priority >= 7 go to DLQ



\# Rate limiting

MESSAGE\_BUS\_RATE\_LIMIT\_MAX = 1000  # Max requests per window

MESSAGE\_BUS\_RATE\_LIMIT\_WINDOW = 60  # Window size in seconds



\# Resilience

DLQ\_MAX\_RETRIES = 3

DLQ\_BACKOFF\_FACTOR = 1.5

BACKPRESSURE\_THRESHOLD = 0.8  # Trigger backpressure at 80% capacity



\# External integrations

USE\_KAFKA = false

USE\_REDIS = false

ENABLE\_MESSAGE\_BUS\_GUARDIAN = false

```



\### Retry Policies



```python

config.RETRY\_POLICIES = {

&nbsp;   "critical": {

&nbsp;       "max\_retries": 5,

&nbsp;       "backoff\_factor": 0.1

&nbsp;   },

&nbsp;   "default": {

&nbsp;       "max\_retries": 3,

&nbsp;       "backoff\_factor": 0.5

&nbsp;   }

}

```



\## Advanced Features



\### Dynamic Sharding



```python

\# Enable dynamic sharding

config.dynamic\_shards\_enabled = True



\# Add shard when load increases

await bus.add\_shard()



\# Remove shard when load decreases

await bus.remove\_shard(shard\_id=3)

```



\### Custom Hooks



```python

\# Pre-publish hook

def add\_timestamp(message):

&nbsp;   message.context\["timestamp"] = time.time()

&nbsp;   return message



bus.add\_pre\_publish\_hook(add\_timestamp)



\# Post-publish hook

def log\_published(message):

&nbsp;   logger.info(f"Published: {message.topic}")



bus.add\_post\_publish\_hook(log\_published)

```



\### Pattern Subscriptions



```python

import re



\# Subscribe to pattern

pattern = re.compile(r"events\\..\*\\.created")

await bus.subscribe(pattern, handler)



\# Will match: events.user.created, events.order.created, etc.

```



\## Monitoring



\### Metrics



The message bus exposes Prometheus metrics:



\- `message\_bus\_queue\_size` - Current queue sizes per shard

\- `message\_bus\_dispatch\_duration` - Message dispatch latency

\- `message\_bus\_topic\_throughput` - Messages per second by topic

\- `message\_bus\_callback\_errors` - Callback error counts

\- `message\_bus\_publish\_retries` - Publish retry attempts

\- `message\_bus\_consumer\_lag` - Consumer lag metrics

\- `message\_bus\_callback\_latency` - Callback execution time

\- `message\_bus\_message\_age` - Age of messages when processed



\### Health Checks



```python

\# Check bus health

health = bus.get\_health\_status()

print(f"Running: {health\['running']}")

print(f"Queue sizes: {health\['queue\_sizes']}")

print(f"Error rate: {health\['error\_rate']}")

```



\## Testing



\### Unit Tests



```bash

\# Run all tests

python -m pytest tests/



\# Run specific test file

python -m pytest tests/test\_sharded\_message\_bus.py



\# With coverage

python -m pytest tests/ --cov=message\_bus

```



\### End-to-End Tests



```bash

\# Run e2e tests

python tests/test\_message\_bus\_e2e.py



\# With performance benchmarks

PERF\_TEST=true python tests/test\_message\_bus\_e2e.py

```



\## Performance



\### Benchmarks



\- \*\*Throughput\*\*: 10,000+ messages/second (single instance)

\- \*\*Latency\*\*: < 10ms p99 for normal priority

\- \*\*Scalability\*\*: Linear scaling with shard count

\- \*\*Memory\*\*: ~100MB base + 1KB per queued message



\### Optimization Tips



1\. \*\*Shard Count\*\*: Set to number of CPU cores for CPU-bound workloads

2\. \*\*Worker Threads\*\*: 2-4 workers per shard for I/O-bound tasks

3\. \*\*Queue Size\*\*: Balance memory usage vs. burst capacity

4\. \*\*Priority Threshold\*\*: Adjust based on message distribution



\## Troubleshooting



\### Common Issues



\*\*Queue Full Errors\*\*

```python

\# Increase queue size

config.message\_bus\_max\_queue\_size = 50000



\# Or enable backpressure

config.BACKPRESSURE\_THRESHOLD = 0.7

```



\*\*High Latency\*\*

```python

\# Add more shards

await bus.adjust\_shards(target\_shard\_count=8)



\# Or increase workers

await bus.adjust\_workers(shard\_id=0, target\_workers=4)

```



\*\*Message Loss\*\*

```python

\# Enable persistence for critical messages

await bus.publish(

&nbsp;   topic="critical",

&nbsp;   payload=data,

&nbsp;   priority=1,  # High priority ensures persistence

&nbsp;   idempotency\_key="unique\_id"

)

```



\### Debug Logging



```python

import logging

logging.basicConfig(level=logging.DEBUG)

logger = logging.getLogger("message\_bus")

```



\## API Reference



\### ShardedMessageBus



\#### Methods



\- `publish(topic, payload, priority=0, retries=3, trace\_id=None, idempotency\_key=None, encrypt=False, context=None, client\_id='default', signature=None) -> bool`

\- `batch\_publish(messages: List\[Dict]) -> List\[bool]`

\- `subscribe(topic: Union\[str, Pattern], handler: Callable, filter=None)`

\- `unsubscribe(topic: Union\[str, Pattern], handler: Callable)`

\- `request(topic: str, payload: Any, timeout: float = 5.0, priority: int = 5) -> Any`

\- `add\_pre\_publish\_hook(hook: Callable\[\[Message], Message])`

\- `add\_post\_publish\_hook(hook: Callable\[\[Message], None])`

\- `adjust\_shards(target\_shard\_count: int)`

\- `adjust\_workers(shard\_id: int, target\_workers: int)`

\- `shutdown()`



\### Message



\#### Fields



\- `topic: str` - Message topic/channel

\- `payload: Any` - Message data

\- `priority: int = 0` - Priority (lower = higher priority)

\- `timestamp: float` - Creation timestamp

\- `trace\_id: str` - Unique trace identifier

\- `encrypted: bool = False` - Encryption flag

\- `idempotency\_key: Optional\[str] = None` - Deduplication key

\- `context: Dict\[str, Any]` - Execution context

\- `processing\_start: Optional\[int] = None` - Processing start time



\## Contributing



1\. Fork the repository

2\. Create a feature branch

3\. Add tests for new functionality

4\. Ensure all tests pass

5\. Submit a pull request



\## License



\[Specify your license here]



\## Support



For issues, questions, or contributions, please \[open an issue](link-to-issues) or contact the maintainers.

