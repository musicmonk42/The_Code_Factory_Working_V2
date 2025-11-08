# OmniCore Omega Pro Engine Troubleshooting Guide

## Common Issues

### Dependency Installation
- Ensure Python 3.8+
- Use virtualenv
- Install with `pip install -r requirements.txt`

### Database Connection
- Check `DATABASE_URL`
- Ensure DB server is running
- Run `alembic upgrade head`
- Check `ENCRYPTION_KEYS`

### Plugin Loading
- Verify `PLUGIN_DIR`
- Use correct `@plugin` decorator
- Check logs for PluginEventHandler/RollbackHandler

### Message Bus Issues
- Backpressure: Increase `MESSAGE_BUS_MAX_QUEUE_SIZE`, shards
- DLQ: Tune `DLQ_MAX_RETRIES`, `DLQ_BACKOFF_FACTOR`
- Kafka/Redis: Validate URLs, ensure services running

### API/CLI Auth
- Set `JWT_SECRET` for API
- Set `USER_ID` for CLI
- Use valid JWT token

### Security Errors
- Check Fernet keys for encryption errors
- Rebuild Merkle tree if audit fails

### Performance
- Monitor metrics: Prometheus, logs
- Tune worker/shard counts, cache size

### Debugging
- Set `LOG_LEVEL=DEBUG`
- Use `metrics-status`, `audit-query` CLI commands
- Tail logs: `tail -f omnicore.log`

## References

- [ARCHITECTURE.md](ARCHITECTURE.md)
- [CONFIGURATION.md](CONFIGURATION.md)
- [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md)
- [DEPLOYMENT.md](DEPLOYMENT.md)
- [API_REFERENCE.md](API_REFERENCE.md)
- [PLUGINS.md](PLUGINS.md)
- [TESTING.md](TESTING.md)