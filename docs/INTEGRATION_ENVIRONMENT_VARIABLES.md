<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# Environment Variables for Stub Integrations

This document describes the environment variables needed for the newly integrated real services.

## Alert and Notification Systems

### PagerDuty Integration

Configure PagerDuty Events API v2 integration:

```bash
# PagerDuty routing key for sending alerts
PAGERDUTY_ROUTING_KEY=your_routing_key_here
```

**Usage:**
- When set, alerts will be sent to PagerDuty using the Events API v2
- When not set, alerts will only be logged locally
- Supports automatic retry with exponential backoff
- Compatible with both `quantum.py` and `file_watcher.py` modules

**Alert Levels:**
- `CRITICAL` → PagerDuty severity: critical
- `ERROR` → PagerDuty severity: error
- `WARNING` → PagerDuty severity: warning
- `INFO` → PagerDuty severity: info

### Slack Integration

Configure Slack webhook integration:

```bash
# Slack incoming webhook URL
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL
```

**Usage:**
- When set, alerts will be sent to Slack via webhook
- When not set, alerts will only be logged locally
- Supports automatic retry with exponential backoff
- Color-coded messages based on alert level
- Compatible with both `quantum.py` and `file_watcher.py` modules

## HashiCorp Vault Integration

### Vault Configuration

The Vault credential provider supports multiple authentication methods:

#### Token Authentication (Simplest)

```bash
VAULT_ADDR=https://vault.example.com:8200
VAULT_TOKEN=your_vault_token
```

#### AppRole Authentication (Recommended for Production)

```bash
VAULT_ADDR=https://vault.example.com:8200
VAULT_ROLE_ID=your_role_id
VAULT_SECRET_ID=your_secret_id
```

#### Kubernetes Authentication (For K8s Deployments)

```bash
VAULT_ADDR=https://vault.example.com:8200
VAULT_K8S_ROLE=your_k8s_role
```

#### Optional Vault Settings

```bash
# Secret mount point (default: secret)
VAULT_MOUNT_POINT=secret

# Vault namespace (for Vault Enterprise)
VAULT_NAMESPACE=your_namespace

# Path to CA certificate for TLS verification
VAULT_CACERT=/path/to/ca-cert.pem
```

**Features:**
- Automatic caching with TTL (Time To Live)
- Support for both KV v1 and KV v2 secret engines
- Automatic fallback to expired cache on connection failure
- Thread-safe credential retrieval

**Usage in quantum.py:**
```python
from self_fixing_engineer.simulation.quantum import VaultCredentialProvider

provider = VaultCredentialProvider()
credentials = await provider.get_credentials("path/to/secret")
```

## LLM Provider Integration

### OpenAI Configuration

```bash
OPENAI_API_KEY=sk-your-openai-api-key
```

### Anthropic Claude Configuration

```bash
ANTHROPIC_API_KEY=sk-ant-your-anthropic-api-key
```

### Google Gemini Configuration

```bash
GEMINI_API_KEY=your-gemini-api-key
```

### General LLM Settings

```bash
# Force use of mock LLM regardless of API keys (useful for testing)
LLM_USE_MOCK=false
```

**Usage in agent_core.py:**
```python
from self_fixing_engineer.simulation.agent_core import init_llm

# Initialize with specific provider
llm = init_llm("openai", model="gpt-4")
response = llm.generate("Your prompt here")

# Automatic fallback to MockLLM if credentials not available
llm = init_llm("anthropic")  # Will use MockLLM if ANTHROPIC_API_KEY not set
```

**Supported Providers:**
- `openai` - OpenAI GPT models (default: gpt-3.5-turbo)
- `anthropic` - Anthropic Claude models (default: claude-3-haiku-20240307)
- `gemini` - Google Gemini models (default: gemini-pro)
- `mock` - Mock LLM for testing

## SIEM Integration

SIEM client factory supports multiple platforms. Configuration is passed as dictionaries.

### Splunk Configuration

```python
config = {
    "host": "splunk.example.com",
    "port": 8088,
    "token": "your-hec-token",
    "index": "main",
    "source": "siem_client",
    "sourcetype": "json"
}

from self_fixing_engineer.simulation.plugins.siem_clients import get_siem_client
client = get_siem_client("splunk", config)
await client.connect()
await client.send_event({"message": "test event"})
```

### AWS CloudWatch Configuration

```python
config = {
    "region": "us-east-1",
    "log_group": "application-logs",
    "log_stream": "production",
    "access_key_id": "...",  # Optional, uses boto3 credential chain
    "secret_access_key": "..."  # Optional
}

client = get_siem_client("cloudwatch", config)
```

### Azure Sentinel Configuration

```python
config = {
    "workspace_id": "your-workspace-id",
    "shared_key": "your-shared-key",
    "log_type": "CustomLog"
}

client = get_siem_client("azure_sentinel", config)
```

## Cloud Logging Integration

Cloud logger implementations are already fully integrated with proper credential handling.

### AWS CloudWatch Logs

```bash
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key
```

### Google Cloud Logging

```bash
GCP_PROJECT_ID=your-project-id
# Set GOOGLE_APPLICATION_CREDENTIALS to service account JSON path
```

### Azure Monitor

```bash
AZURE_DCE=https://your-dce.monitor.azure.com
AZURE_DCR_ID=your-dcr-immutable-id
AZURE_STREAM_NAME=your-stream-name
```

## WebSocket Manager

No specific environment variables required. Configuration is done programmatically:

```python
from self_fixing_engineer.arbiter.human_loop import WebSocketManager

ws_manager = WebSocketManager(max_connections=100)
await ws_manager.start()

# Register a WebSocket connection
await ws_manager.register_connection("conn_id", websocket, metadata={...})

# Send to specific connection
await ws_manager.send_json({"type": "update", "data": {...}}, connection_id="conn_id")

# Broadcast to all connections
await ws_manager.send_json({"type": "broadcast", "message": "..."})

await ws_manager.stop()
```

## Fallback Behavior

All integrations are designed with graceful fallback:

1. **Missing Credentials:** Services will log warnings and either:
   - Use mock implementations (LLM)
   - Log locally instead of sending to external service (alerts)
   - Raise clear errors on first use (Vault, SIEM)

2. **Connection Failures:** Built-in retry logic with exponential backoff

3. **Service Unavailable:** Circuit breaker patterns prevent repeated failures

4. **Test/Development Mode:** Set `LLM_USE_MOCK=true` to force mock behavior regardless of credentials

## Security Best Practices

1. **Never commit credentials:** Use environment variables or secrets managers
2. **Rotate credentials regularly:** Especially API keys and tokens
3. **Use least privilege:** Grant only necessary permissions
4. **Enable TLS:** Always use HTTPS endpoints
5. **Audit access:** Enable logging for all credential retrievals
6. **Use Vault in production:** Centralize secret management with HashiCorp Vault

## Testing

To test integrations without real credentials:

```bash
# Run with mock implementations
export LLM_USE_MOCK=true

# Don't set alert credentials to test logging fallback
unset PAGERDUTY_ROUTING_KEY
unset SLACK_WEBHOOK_URL

# Run your application
python your_application.py
```

## Troubleshooting

### PagerDuty alerts not sending

1. Verify `PAGERDUTY_ROUTING_KEY` is set correctly
2. Check logs for connection errors
3. Verify Events API v2 is enabled for your service
4. Test routing key with `curl`:
   ```bash
   curl -X POST https://events.pagerduty.com/v2/enqueue \
     -H 'Content-Type: application/json' \
     -d "{\"routing_key\":\"$PAGERDUTY_ROUTING_KEY\",\"event_action\":\"trigger\",\"payload\":{\"summary\":\"test\",\"severity\":\"info\",\"source\":\"test\"}}"
   ```

### Slack webhooks not working

1. Verify `SLACK_WEBHOOK_URL` is a valid incoming webhook URL
2. Check that the webhook hasn't been revoked
3. Test webhook with `curl`:
   ```bash
   curl -X POST $SLACK_WEBHOOK_URL \
     -H 'Content-Type: application/json' \
     -d '{"text":"Test message"}'
   ```

### Vault authentication failing

1. Verify `VAULT_ADDR` is accessible
2. Check authentication credentials are valid
3. For AppRole: verify role_id and secret_id are correct
4. For Kubernetes: verify service account token exists
5. Test connection with `vault` CLI:
   ```bash
   export VAULT_ADDR=https://vault.example.com:8200
   vault login -method=token token=$VAULT_TOKEN
   vault kv get secret/path/to/secret
   ```

### LLM provider errors

1. Verify API key is valid and has credits/quota
2. Check API key permissions
3. Ensure required packages are installed:
   - OpenAI: `pip install openai`
   - Anthropic: `pip install anthropic`
   - Gemini: `pip install google-generativeai`
4. Test API key with provider's CLI or web interface

## Dependencies

Make sure these packages are installed for full functionality:

```bash
pip install aiohttp      # For HTTP clients (alerts, webhooks)
pip install hvac         # For HashiCorp Vault
pip install openai       # For OpenAI LLM
pip install anthropic    # For Anthropic Claude
pip install google-generativeai  # For Google Gemini
pip install tenacity     # For retry logic
pip install boto3        # For AWS services
```
