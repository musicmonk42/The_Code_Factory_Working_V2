# Arbiter Bug Manager

**Version:** 1.0.0  | **Python:** 3.12+

## What is This?

An intelligent, self-healing error handling system for Python applications that automatically detects, logs, remediates, and notifies about runtime errors. Think of it as an automated Site Reliability Engineer (SRE) that monitors your application and takes action when things go wrong.

**Key Concept:** When an error occurs, the system first tries to fix it automatically using ML-predicted or rule-based remediation playbooks. Only if the fix fails or the issue is critical does it notify your team, reducing alert fatigue while maintaining system reliability.

## Quick Start Example

```python
from arbiter.bug_manager import BugManagerArena

# Initialize with defaults (uses in-memory storage)
bug_manager = BugManagerArena()

# In your application code
try:
    database_connection = connect_to_database()
    process_payment(database_connection)
except ConnectionError as e:
    # System will:
    # 1. Try to reconnect automatically (if playbook exists)
    # 2. Log the incident with full audit trail
    # 3. Notify team only if auto-fix fails
    bug_manager.report(
        e,
        severity="high",
        location="payment_service",
        custom_details={"transaction_id": "TX-12345", "retry_count": 3}
    )
```

## How It Works

```
Error Occurs → Rate Limiting → Generate Signature → Audit Logging
                                        ↓
                              Auto-Remediation Attempt
                            (ML Model or Rule Playbook)
                                        ↓
                        Success? → Log Success → Done
                           ↓ No
                    Send Notifications → Team Investigates
                 (with Circuit Breakers)
```

### Core Flow

1. **Error Detection**: Application catches exception or error condition
2. **Rate Limiting**: Prevents duplicate reports (configurable window)
3. **Signature Generation**: Creates unique hash for deduplication
4. **Audit Logging**: Records encrypted, tamper-proof audit trail
5. **Auto-Remediation**: Attempts ML-predicted or rule-based fixes
6. **Smart Notifications**: Alerts teams only when manual intervention needed

## Key Features

### 🔧 Auto-Remediation System

- **ML-Powered**: Predicts best remediation strategy based on error patterns
- **Rule-Based Playbooks**: Define step-by-step recovery procedures
- **Idempotent Steps**: Safe to retry without side effects
- **Success Tracking**: Feeds outcomes back to ML model for improvement

Example playbook:
```python
restart_service_playbook = RemediationPlaybook(
    name="RestartServicePlaybook",
    steps=[
        RemediationStep(
            name="CheckServiceHealth",
            action_name="check_health",
            on_success="RestartService",
            on_failure="ABORT"
        ),
        RemediationStep(
            name="RestartService",
            action_name="restart_service",
            retries=2,
            timeout_seconds=30
        )
    ]
)
```

### 🔔 Enterprise Notification System

The notification subsystem (750+ lines in `notifications.py`) provides:

#### Circuit Breakers
Prevents cascading failures when channels are down:
- Tracks consecutive failures per channel
- Opens circuit after threshold (default: 5)
- Auto-recovery with half-open state testing
- Redis support for distributed state

#### Rate Limiting
Prevents notification storms:
- Sliding window algorithm
- Per-channel configurable limits
- Redis or in-memory backends
- Automatic fallback on Redis failure

#### Multi-Channel Support
- **Slack**: Webhooks with auth token support
- **Email**: SMTP with TLS/STARTTLS, batch recipients
- **PagerDuty**: Event API v2, severity-based routing

#### Batch Processing
```python
notifications = [
    {"channel": "slack", "message": "Database connection lost"},
    {"channel": "email", "subject": "Critical Alert", "body": "..."},
    {"channel": "pagerduty", "event_type": "trigger", "description": "..."}
]
results = await notification_service.notify_batch(notifications)
```

### 📊 Observability

Comprehensive Prometheus metrics:
- `bug_report_total` - Reports by severity
- `bug_auto_fix_success` - Successful remediations
- `notification_circuit_breaker_open` - Circuit breaker trips
- `notification_send_duration_seconds` - Channel latency
- `remediation_step_execution` - Playbook performance
- `ml_remediation_prediction` - ML model usage

### 🔒 Security Features

- **PII Redaction**: Automatic removal of sensitive data
- **Audit Encryption**: Fernet encryption for logs
- **File Permissions**: Restrictive 0600 on audit files
- **Secret Management**: Integration with Pydantic SecretStr
- **HMAC Chain**: Tamper-evident audit trail

## Installation

### Minimal Setup (Development)

```bash
# Core dependencies only
pip install aiohttp prometheus_client tenacity portalocker cryptography

# Set required environment variables
export ARBITER_AUDIT_LOG_ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
```

### Production Setup

```bash
# Full dependencies
pip install -r requirements.txt

# Start Redis for distributed features
docker run -d -p 6379:6379 redis:alpine

# Configure environment
cp .env.example .env
# Edit .env with your settings
```

## Configuration

### Core Settings

```python
from arbiter.bug_manager import Settings

settings = Settings(
    # Core Behavior
    DEBUG_MODE=False,
    AUTO_FIX_ENABLED=True,
    
    # Rate Limiting
    RATE_LIMIT_ENABLED=True,
    RATE_LIMIT_WINDOW_SECONDS=600,  # 10 minutes
    RATE_LIMIT_MAX_REPORTS=3,        # Max 3 identical errors per window
    RATE_LIMIT_REDIS_URL="redis://localhost:6379",  # Optional distributed state
    
    # Audit Logging
    AUDIT_LOG_ENABLED=True,
    AUDIT_LOG_FILE_PATH="/var/log/arbiter/audit.log",
    AUDIT_LOG_ENCRYPTION_KEY="your-fernet-key-here",
    AUDIT_LOG_MAX_FILE_SIZE_MB=100,
    AUDIT_LOG_ENABLE_COMPRESSION=True,
    
    # Notifications
    ENABLED_NOTIFICATION_CHANNELS=("slack", "email", "pagerduty"),
    NOTIFICATION_FAILURE_THRESHOLD=5,  # Circuit breaker threshold
    NOTIFICATION_RECOVERY_TIMEOUT_SECONDS=300,
    
    # Slack
    SLACK_WEBHOOK_URL="https://hooks.slack.com/services/...",
    SLACK_API_TIMEOUT_SECONDS=5.0,
    
    # Email
    EMAIL_ENABLED=True,
    EMAIL_RECIPIENTS=["sre-team@company.com"],
    EMAIL_SMTP_SERVER="smtp.gmail.com",
    EMAIL_SMTP_PORT=587,
    EMAIL_USE_STARTTLS=True,
    EMAIL_SMTP_USERNAME="alerts@company.com",
    EMAIL_SMTP_PASSWORD="app-specific-password",
    
    # PagerDuty
    PAGERDUTY_ENABLED=True,
    PAGERDUTY_ROUTING_KEY="integration-key-here",
    
    # ML Remediation
    ML_REMEDIATION_ENABLED=True,
    ML_MODEL_ENDPOINT="https://ml-api.company.com/predict",
    ML_AUTH_TOKEN="bearer-token",
    ML_CONFIDENCE_THRESHOLD=0.75,
)
```

### Environment Variables

All settings can be overridden via environment variables with `ARBITER_` prefix:

```bash
export ARBITER_DEBUG_MODE=false
export ARBITER_SLACK_WEBHOOK_URL="https://hooks.slack.com/..."
export ARBITER_RATE_LIMIT_REDIS_URL="redis://redis.internal:6379/0"
export ARBITER_ML_MODEL_ENDPOINT="https://ml.internal/predict"
```

## Usage Examples

### Basic Error Reporting

```python
# Async usage
async def process_order(order_id):
    manager = BugManager(settings)
    try:
        result = await risky_operation(order_id)
    except Exception as e:
        await manager.report(
            e,
            severity="high",
            location="order_processing",
            custom_details={"order_id": order_id}
        )
    finally:
        await manager.shutdown()

# Sync-friendly wrapper
def legacy_function():
    arena = BugManagerArena()
    try:
        dangerous_operation()
    except Exception as e:
        arena.report(e, severity="critical")  # Handles async internally
```

### Custom Remediation Playbooks

```python
from arbiter.bug_manager.remediations import RemediationStep, RemediationPlaybook, BugFixerRegistry

# Define custom action
async def restart_database_pool(bug_details):
    """Custom remediation action"""
    pool_name = bug_details.get("custom_details", {}).get("pool_name")
    # Your restart logic here
    return True  # Return success status

# Register the action
RemediationStep.register_action("restart_db_pool", restart_database_pool)

# Create playbook
db_recovery_playbook = RemediationPlaybook(
    name="DatabaseRecovery",
    description="Recovers from database connection failures",
    steps=[
        RemediationStep(
            name="RestartPool",
            action_name="restart_db_pool",
            retries=2,
            retry_delay_seconds=5.0,
            timeout_seconds=30.0
        )
    ]
)

# Register for specific error patterns
BugFixerRegistry.register_playbook(
    db_recovery_playbook,
    location="database_service",
    bug_signature_prefix="connection_pool_exhausted"
)
```

### Notification Customization

```python
# Register critical notification handler for escalation
async def escalate_to_management(channel: str, failures: int, message: str):
    """Called when notification circuit breaker trips"""
    # Send SMS to on-call manager
    await send_sms(MANAGER_PHONE, f"Alert system down: {channel}")
    
NotificationService.register_critical_notification_handler(escalate_to_management)
```

## Architecture

### Component Overview

```
bug_manager.py          - Core orchestration and API
├── notifications.py    - Multi-channel alerts with resilience patterns
├── remediations.py     - ML and rule-based auto-fix system
├── audit_log.py        - Encrypted, rotatable audit trails
└── utils.py            - PII redaction, validation, error classes
```

### Data Flow

1. **Error Input**: Exception, string, or dict with error details
2. **Rate Limiting**: Redis-backed or in-memory deduplication
3. **Processing Pipeline**:
   - Generate unique signature (SHA256 hash)
   - Create audit log entry (encrypted)
   - Attempt remediation (ML or rules)
   - Send notifications if needed
4. **Metrics Export**: Prometheus `/metrics` endpoint

## Monitoring & Operations

### Health Checks

```python
# Kubernetes liveness probe
@app.route("/health/live")
async def liveness():
    return {"status": "alive"}

# Readiness probe (checks dependencies)
@app.route("/health/ready")
async def readiness():
    checks = {
        "redis": await check_redis(),
        "ml_model": await check_ml_endpoint(),
        "smtp": await check_smtp()
    }
    is_ready = all(checks.values())
    return {"ready": is_ready, "checks": checks}, 200 if is_ready else 503
```

### Prometheus Metrics

```yaml
# Example Prometheus alerts
groups:
  - name: bug_manager
    rules:
      - alert: HighErrorRate
        expr: rate(bug_report_total[5m]) > 10
        annotations:
          summary: "High error rate detected"
      
      - alert: CircuitBreakerOpen
        expr: notification_circuit_breaker_open > 0
        annotations:
          summary: "Notification channel {{ $labels.channel }} is down"
      
      - alert: LowAutoFixRate
        expr: rate(bug_auto_fix_success[1h]) / rate(bug_auto_fix_attempt[1h]) < 0.5
        annotations:
          summary: "Auto-remediation success rate below 50%"
```

## Troubleshooting

### Common Issues

**Q: Notifications not being sent**
- Check circuit breaker status in metrics
- Verify credentials in environment variables
- Check `notification_send_failed` metric for specific channels
- Review audit logs for error details

**Q: Auto-remediation not working**
- Ensure ML endpoint is accessible
- Check `ml_remediation_prediction_failed` metric
- Verify playbooks are registered for error signatures
- Review ML confidence threshold setting

**Q: High memory usage**
- Reduce `AUDIT_LOG_BUFFER_SIZE`
- Enable Redis for distributed rate limiting
- Check for memory leaks in custom remediation actions

**Q: Audit logs missing**
- Verify encryption key is set correctly
- Check file permissions (should be 0600)
- Ensure sufficient disk space
- Review `audit_log_write_failed` metric

### Debug Mode

Enable debug logging:
```python
settings.DEBUG_MODE = True
logging.basicConfig(level=logging.DEBUG)
```

## Performance Tuning

### For High-Volume Systems

```python
settings = Settings(
    # Increase concurrency
    BUG_MAX_CONCURRENT_REPORTS=100,
    NOTIFICATION_BATCH_CONCURRENCY=20,
    
    # Use Redis for distributed state
    RATE_LIMIT_REDIS_URL="redis://redis-cluster:6379",
    NOTIFICATION_REDIS_URL="redis://redis-cluster:6379",
    
    # Optimize buffering
    AUDIT_LOG_BUFFER_SIZE=500,
    AUDIT_LOG_FLUSH_INTERVAL_SECONDS=2.0,
    
    # Adjust circuit breakers
    NOTIFICATION_FAILURE_THRESHOLD=10,
    NOTIFICATION_HALF_OPEN_ATTEMPTS=3,
)
```

### Resource Requirements

- **Memory**: ~100MB base + 1MB per 1000 buffered events
- **CPU**: Minimal, scales with remediation complexity
- **Network**: Depends on notification volume
- **Storage**: Audit logs grow ~1GB per million events (uncompressed)

## Security Considerations

1. **Never log credentials** - Use SecretStr for sensitive config
2. **Enable audit encryption** in production
3. **Restrict file permissions** to 0600
4. **Use TLS** for all external communications
5. **Implement PII redaction** for compliance (GDPR, CCPA)
6. **Rotate encryption keys** periodically
7. **Use separate Redis** instances for different environments

## Contributing

1. Fork the repository
2. Create feature branch (`git checkout -b feature/amazing-feature`)
3. Run tests (`pytest tests/`)
4. Check linting (`flake8`, `mypy`)
5. Submit PR with clear description

## License

MIT License - See [LICENSE](LICENSE) for details

## Support

- **Documentation**: [docs/](docs/)
- **Issues**: [GitHub Issues](https://github.com/company/arbiter-bug-manager/issues)
- **Contact**: sre-team@company.com

---

*Built with ❤️ for reliable, self-healing systems*