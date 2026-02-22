# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# Sharding Guide — OmniCore Message Bus

This guide explains the consistent-hashing sharding strategy used by the
OmniCore message bus and how to configure it for your deployment.

---

## Overview

The OmniCore message bus partitions topics across multiple **shards** to
allow horizontal scaling.  A *shard* is an independent queue/worker pool that
handles a subset of all topics.

Key-to-shard routing is performed by a **consistent-hash ring**:

1. Each shard is placed at *V* virtual node positions on a 64-bit ring
   (SHA-256 based).
2. When a topic key arrives, it is hashed and the ring is scanned clockwise
   to find the nearest virtual node; the owning shard is returned.

The default number of virtual nodes per shard is **150**, which provides
good uniformity at typical shard counts (3–20).

---

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MESSAGE_BUS_SHARDS` | `3` | Number of shards for the message bus. |
| `MESSAGE_BUS_SHARD_COUNT` | `4` | Legacy alias (lower precedence than `MESSAGE_BUS_SHARDS`). |

Set `MESSAGE_BUS_SHARDS` in your `.env` file or deployment manifest:

```bash
# .env
MESSAGE_BUS_SHARDS=5
```

### Docker Compose

```yaml
services:
  api:
    environment:
      - MESSAGE_BUS_SHARDS=5
```

### Kubernetes

```yaml
env:
  - name: MESSAGE_BUS_SHARDS
    value: "5"
```

---

## How It Works

### Virtual Node Ring

```
Ring (64-bit hash space)
─────────────────────────────────────────────────────────
Position:   0x0000   0x3fff   0x7fff   0xbfff   0xffff
            │        │        │        │        │
Shard:      shard-0  shard-2  shard-1  shard-0  shard-2
            (vnode)  (vnode)  (vnode)  (vnode)  (vnode)
```

A topic key is hashed to a position on the ring.  The **first virtual node
clockwise** from that position determines the responsible shard.

### Consistent Hashing Property

When a shard is added or removed, only the keys that *belonged to its
immediate predecessor on the ring* need to be remapped.  In practice this
means approximately **1/N** of keys are remapped, where N is the current
shard count.

---

## Using the Sharding API

### Python

```python
from omnicore_engine.sharding import ConsistentHashRing, build_ring_from_env

# Pre-populated from MESSAGE_BUS_SHARDS env var
ring = build_ring_from_env()

# Route a topic
shard = ring.get_shard("job.abc123.stage_progress")  # e.g. "shard-1"

# Dynamic shard management
ring.add_shard("shard-extra")
ring.remove_shard("shard-extra")
```

### Thread Safety

`ConsistentHashRing` uses `threading.RLock` internally.  All methods are
safe to call from multiple threads concurrently without external locking.

---

## Performance Characteristics

| Operation | Time Complexity |
|-----------|----------------|
| `get_shard` | O(log V·N) |
| `add_shard` | O(V·log(V·N)) |
| `remove_shard` | O(V·N) |

Where V = virtual nodes per shard (default 150) and N = current shard count.

At 5 shards and 150 virtual nodes: V·N = 750 virtual nodes on the ring.
A `get_shard` lookup performs a binary search over 750 entries — negligible
overhead.

---

## Monitoring

The `ShardedMessageBus` exposes per-shard metrics via Prometheus:

| Metric | Description |
|--------|-------------|
| `message_bus_queue_size` | Current queue depth per shard |
| `message_bus_topic_throughput` | Messages/second per topic |
| `message_bus_dispatch_duration` | Dispatch latency histogram |

---

## Testing the Sharding Module

```bash
# Run only sharding tests
export TESTING=1
pytest omnicore_engine/tests/test_sharding.py -v
```

Tests cover:
- Uniform key distribution (no shard receives > 2× average load).
- Minimal remapping on shard add/remove (consistent-hashing property).
- Thread safety under concurrent access.
- `build_ring_from_env()` helper respects `MESSAGE_BUS_SHARDS`.

---

## Choosing a Shard Count

| Use Case | Recommended `MESSAGE_BUS_SHARDS` |
|----------|----------------------------------|
| Development / CI | 1–2 |
| Small production (< 100 req/s) | 3 (default) |
| Medium production (< 1000 req/s) | 4–8 |
| High-throughput (> 1000 req/s) | 8–16 |

Rule of thumb: use **one shard per 2 CPU cores** available to the message-bus
worker pool.
