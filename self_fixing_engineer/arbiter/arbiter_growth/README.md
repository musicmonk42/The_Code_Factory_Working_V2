# Arbiter Growth Module

**Version:** 1.0.0 | **License:** Proprietary © 2025 Unexpected Innovations

## TL;DR

A Python system that tracks and manages the evolution of AI decision-making entities ("arbiters") through skill improvements and level progression. Think of it as an XP/leveling system for AI agents with enterprise-grade reliability, audit trails, and distributed storage support.

## What is This? (5-Minute Overview)

### The Problem
AI systems need to track their capabilities as they improve over time. When an AI agent learns a new skill or improves existing ones, you need:
- A reliable way to record these improvements
- The ability to restore previous states if something goes wrong
- Audit trails for compliance and debugging
- Distributed storage for scalability

### The Solution
The Arbiter Growth module provides event-sourced state management for AI agents. Here's a simple example:

```python
# Your AI agent processes some data successfully
await manager.improve_skill("data_analysis", improvement=0.1)

# The system automatically:
# - Records this event with exactly-once guarantees
# - Updates the agent's state
# - Creates an audit trail
# - Persists to your chosen storage backend
# - Notifies any connected systems

# Later, you can query the agent's current capabilities
state = await manager.get_current_state()
print(f"Data analysis skill: {state.skills['data_analysis']}")  # 0.1
```

### Core Concepts

- **Arbiter**: An AI agent or decision-making entity whose capabilities evolve over time
- **Growth Event**: A recorded change in an arbiter's capabilities (skill improvement, level-up, etc.)
- **Snapshot**: A point-in-time backup of an arbiter's complete state
- **Event Sourcing**: Every change is recorded as an immutable event, allowing full history replay

## Simple Architecture Overview

The system follows a straightforward event-driven pattern:

```
Your Code → Growth Event → Manager → Storage
                ↓
           State Update
                ↓
         Plugins/Hooks → External Systems (Knowledge Graph, etc.)
```

**Why this architecture?**
- **Events** ensure you never lose data (append-only log)
- **Snapshots** provide fast state recovery without replaying thousands of events
- **Plugins** let you extend functionality without modifying core code
- **Multiple storage backends** let you start simple (SQLite) and scale up (Kafka)

## Quick Start (Minimal Example)

Get running in under 5 minutes with just Python and SQLite:

```bash
# 1. Install minimal dependencies
pip install pydantic sqlalchemy aiosqlite

# 2. Set encryption key (required for security)
export ARBITER_ENCRYPTION_KEY="your-32-byte-key-here-change-this!"

# 3. Create and run the example
```

```python
# example.py
import asyncio
from arbiter_growth_manager import ArbiterGrowthManager
from config_store import ConfigStore
from storage_backends import SQLiteStorageBackend
from idempotency import IdempotencyStore

async def main():
    # Basic setup with SQLite (no external services needed)
    config = ConfigStore()
    storage = SQLiteStorageBackend(config)
    idempotency = IdempotencyStore(redis_url="redis://localhost:6379")
    
    # Create manager for an AI agent named "assistant_v1"
    manager = ArbiterGrowthManager(
        arbiter_name="assistant_v1",
        storage_backend=storage,
        config_store=config,
        idempotency_store=idempotency
    )
    
    # Start the manager
    await manager.start()
    
    # Record some growth events
    await manager.improve_skill("text_analysis", 0.15)
    await manager.improve_skill("code_generation", 0.20)
    await manager.level_up()
    
    # Check current state
    state = await manager.get_current_state()
    print(f"Level: {state.level}")
    print(f"Skills: {state.skills}")
    
    # Gracefully shutdown
    await manager.stop()

asyncio.run(main())
```

## When Should You Use This?

✅ **Good fit if you need:**
- Track AI agent capabilities over time
- Audit trail for compliance/debugging
- Ability to rollback agent states
- Distributed system support
- Exactly-once event processing

❌ **Not needed if:**
- Your AI agents are stateless
- You only need simple key-value storage
- You don't need audit trails or history

## Installation

### Minimal Setup (Development)

```bash
pip install pydantic sqlalchemy aiosqlite tenacity prometheus_client
```

### Full Setup (Production)

```bash
# Install all dependencies
pip install -r requirements.txt

# Start required services (choose based on your needs)
docker-compose up -d redis    # For idempotency (required)
docker-compose up -d kafka     # For high-scale event streaming (optional)
docker-compose up -d neo4j     # For knowledge graph integration (optional)
```

### Dependencies Explained

- **Core** (Required):
  - `pydantic`: Data validation
  - `sqlalchemy`: Database ORM
  - `tenacity`: Retry logic
  - `prometheus_client`: Metrics

- **Storage Backends** (Choose one):
  - SQLite: Built-in, good for development
  - Redis Streams: Medium scale, real-time processing
  - Kafka: High scale, enterprise deployments

- **Optional Integrations**:
  - Neo4j: Knowledge graph storage
  - etcd: Distributed configuration

## Key Features

### For Developers
- **Clean API**: Simple methods like `improve_skill()` and `level_up()`
- **Async-first**: Built on asyncio for high performance
- **Type hints**: Full typing support for IDE autocomplete
- **Extensible**: Plugin system for custom logic

### For Operations
- **Observable**: Prometheus metrics, OpenTelemetry tracing
- **Resilient**: Circuit breakers, retries, rate limiting
- **Secure**: Encryption at rest, HMAC audit chains
- **Scalable**: Multiple storage backend options

### For Compliance
- **Audit trails**: Immutable, tamper-evident logs
- **Event sourcing**: Complete history of all changes
- **Idempotency**: Exactly-once processing guarantees

## Configuration

The system uses hierarchical configuration (etcd → JSON file → defaults):

```json
{
  "storage.backend": "sqlite",
  "snapshot_interval": 100,
  "rate_limit_tokens": 10,
  "redis.url": "redis://localhost:6379"
}
```

See [Configuration Guide](docs/configuration.md) for all options.

## Advanced Usage

### Custom Plugins

```python
from plugins import PluginHook

class MyPlugin(PluginHook):
    async def on_growth_event(self, event, state):
        print(f"Arbiter {state.arbiter_id} processed {event.type}")

manager.register_hook(MyPlugin())
```

### Production Deployment

See [Deployment Guide](docs/deployment.md) for:
- Docker/Kubernetes setup
- High availability configuration
- Monitoring and alerting
- Performance tuning

## API Reference

### Core Methods

- `start()` - Initialize the manager
- `stop()` - Graceful shutdown
- `improve_skill(name, amount)` - Record skill improvement
- `level_up()` - Increase arbiter level
- `get_current_state()` - Get current arbiter state
- `get_health_status()` - Health check for monitoring

See [API Documentation](docs/api.md) for complete reference.

## Architecture Details

For those interested in the implementation:

```
[Growth Event] → [Idempotency Check] → [Rate Limiter] → [Queue]
                         ↓
                  [Process Event] → [Update State]
                         ↓
                  [Plugins/Hooks]
                         ↓
            [Snapshot/Audit to Storage Backend]
```

Components:
- **Event Queue**: Bounded queue for backpressure
- **Circuit Breakers**: Prevent cascading failures
- **Storage Abstraction**: Swap backends without code changes
- **Audit Chain**: HMAC-linked entries for tamper detection

## Troubleshooting

### Common Issues

**Q: "Redis connection failed"**
- Ensure Redis is running: `redis-cli ping`
- Check connection URL format

**Q: "Circuit breaker open"**
- Check storage backend health
- Review logs for repeated failures
- Wait for automatic reset (60s default)

**Q: "Rate limit exceeded"**
- Reduce request frequency
- Increase `rate_limit_tokens` in config

See [Troubleshooting Guide](docs/troubleshooting.md) for more.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for:
- Code style guidelines
- Testing requirements
- Pull request process

## Support

- **Documentation**: [docs/](docs/)
- **Issues**: [GitHub Issues](https://github.com/company/repo/issues)
- **Email**: support@unexpectedinnovations.com

## License

Proprietary © 2025 Unexpected Innovations. See [LICENSE](LICENSE) for details.