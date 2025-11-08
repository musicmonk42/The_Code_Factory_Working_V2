# OmniCore Omega Pro Engine Architecture

```
+-------------------+       +-------------------+       +-------------------+
| FastAPI / CLI     |<----->| PluginRegistry    |<----->| ShardedMessageBus |
| - /fix-imports    |       | - PluginService   |       | - KafkaBridge     |
| - /admin/plugins  |       | - PluginMarketplace|      | - RedisBridge     |
| - /audit/export   |       +-------------------+       | - MessageCache    |
+-------------------+               |                   +-------------------+
                                    |
                                    v
+-------------------+       +-------------------+       +-------------------+
| Database          |<----->| ExplainAudit      |<----->| MetaSupervisor    |
| - AgentState      |       | - Merkle Trees    |       | - CodeHealthEnv   |
| - ExplainAuditRecord|     +-------------------+       +-------------------+
+-------------------+
```

**Key Components:**
- **FastAPI / CLI:** Entry points for API and command-line interface.
- **PluginRegistry:** Registers and manages all plugins; supports dynamic loading, hot-reload, rollback.
- **ShardedMessageBus:** Async, sharded, and backpressure-aware message bus with integrations (Kafka, Redis).
- **Database:** Stores agent state, audit logs; supports encryption and Citus sharding.
- **ExplainAudit:** Auditing with Merkle tree integrity proofs.
- **MetaSupervisor:** Health monitoring, RL-based code health, plugin/test orchestration.

**See**:  
- [API_REFERENCE.md](API_REFERENCE.md)  
- [CONFIGURATION.md](CONFIGURATION.md)  
- [DEPLOYMENT.md](DEPLOYMENT.md)  
- [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md)  
- [PLUGINS.md](PLUGINS.md)  
- [TESTING.md](TESTING.md)  
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md)