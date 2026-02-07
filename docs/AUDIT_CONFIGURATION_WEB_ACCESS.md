<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# Accessing Audit Configuration Through the Web UI

## Overview

The audit logging configuration is now fully accessible through the Code Factory web server via REST API endpoints. This allows operators and administrators to:

- View current audit configuration status
- Check security settings and validation status
- Access comprehensive configuration documentation
- Get quick start guides and examples
- Verify configuration without file system access

## API Endpoints

### 1. Configuration Status Endpoint

**Endpoint**: `GET /audit/config/status`

**Description**: Returns detailed information about the current audit logging configuration.

**Returns**:
- Configuration source (YAML file or environment variables)
- Backend configuration (type, compression, batching)
- Security status (encryption, RBAC, immutability, tamper detection)
- Compliance mode and settings
- Performance settings (retry, health checks)
- Configuration validation results (warnings and errors)
- Feature status (tracing, metrics, API, gRPC)
- Available configuration templates
- Documentation links

**Example Request**:
```bash
curl http://localhost:8000/audit/config/status
```

**Example Response**:
```json
{
  "config_source": "yaml_file",
  "config_file": "generator/audit_config.yaml",
  "backend": {
    "type": "file",
    "compression_enabled": true,
    "compression_algorithm": "zstd",
    "batch_flush_interval": 10,
    "batch_max_size": 100
  },
  "security": {
    "encryption_enabled": true,
    "encryption_key_configured": true,
    "immutable": true,
    "tamper_detection_enabled": true,
    "rbac_enabled": true,
    "dev_mode": false,
    "crypto_provider": "software",
    "signing_algorithm": "ed25519",
    "crypto_mode": "full"
  },
  "compliance": {
    "mode": "soc2",
    "data_retention_days": 365,
    "pii_redaction_enabled": true
  },
  "performance": {
    "retry_max_attempts": 3,
    "retry_backoff_factor": 0.5,
    "health_check_interval": 30
  },
  "features": {
    "tracing_enabled": true,
    "metrics_enabled": true,
    "api_enabled": true,
    "grpc_enabled": true
  },
  "validation": {
    "status": "ok",
    "warnings": [
      "Using software crypto provider - consider HSM for production",
      "Using file backend - consider cloud storage for production"
    ],
    "errors": [],
    "warnings_count": 2,
    "errors_count": 0
  },
  "documentation": {
    "configuration_guide": "/docs/AUDIT_CONFIGURATION.md",
    "quick_start": "/generator/AUDIT_CONFIG_README.md",
    "validation_script": "python generator/audit_log/validate_config.py"
  },
  "available_templates": {
    "production": "generator/audit_config.production.yaml",
    "development": "generator/audit_config.development.yaml",
    "enhanced": "generator/audit_config.enhanced.yaml"
  }
}
```

### 2. Configuration Documentation Endpoint

**Endpoint**: `GET /audit/config/documentation`

**Description**: Returns comprehensive configuration documentation, help guides, and quick start instructions.

**Returns**:
- Available configuration options by category
- Environment variable reference (critical, core, performance)
- Configuration templates with descriptions
- Validation commands (validate, strict mode, environment)
- Documentation links
- Quick start guides for development and production

**Example Request**:
```bash
curl http://localhost:8000/audit/config/documentation
```

**Example Response**:
```json
{
  "configuration_options": {
    "cryptographic_provider": {
      "description": "Configure cryptographic signing and key management",
      "key_settings": [
        "PROVIDER_TYPE (software/hsm)",
        "DEFAULT_ALGO (rsa/ecdsa/ed25519/hmac)",
        "KEY_ROTATION_INTERVAL_SECONDS"
      ]
    },
    "backend_storage": {
      "description": "Configure where audit logs are stored",
      "key_settings": [
        "BACKEND_TYPE (file/sqlite/s3/gcs/azure/kafka/splunk)",
        "BACKEND_PARAMS (JSON configuration)",
        "COMPRESSION_ALGO (none/gzip/zstd)"
      ]
    },
    ...
  },
  "environment_variables": {
    "critical": {
      "AUDIT_LOG_ENCRYPTION_KEY": "Base64-encoded Fernet key (REQUIRED in production)",
      "AUDIT_CRYPTO_MODE": "Crypto mode: full/dev/disabled",
      "AUDIT_LOG_DEV_MODE": "Enable development mode (NEVER in production)"
    },
    ...
  },
  "templates": {
    "production": {
      "file": "generator/audit_config.production.yaml",
      "description": "Production-hardened configuration with security-first defaults",
      "command": "make audit-config-setup-prod"
    },
    ...
  },
  "validation": {
    "validate_current": {
      "command": "make audit-config-validate",
      "description": "Validate current audit_config.yaml"
    },
    ...
  },
  "documentation_links": {
    "complete_reference": "docs/AUDIT_CONFIGURATION.md",
    "quick_start": "generator/AUDIT_CONFIG_README.md",
    "module_readme": "generator/audit_log/README.md",
    "implementation_summary": "AUDIT_CONFIG_IMPLEMENTATION_SUMMARY.md"
  },
  "quick_start": {
    "development": [
      "1. Run: make audit-config-setup-dev",
      "2. Set: export AUDIT_LOG_DEV_MODE=true",
      "3. Validate: make audit-config-validate",
      "4. Start: python server/main.py"
    ],
    "production": [
      "1. Run: make audit-config-setup-prod",
      "2. Edit: vim generator/audit_config.yaml",
      "3. Generate key: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"",
      "4. Set: export AUDIT_LOG_ENCRYPTION_KEY=<generated-key>",
      "5. Validate: make audit-config-validate-strict",
      "6. Deploy your application"
    ]
  }
}
```

## Accessing via Web UI

### Method 1: Interactive API Documentation (Swagger UI)

1. **Start the server**:
   ```bash
   python server/main.py
   # or
   make run-server
   ```

2. **Open Swagger UI**:
   - Navigate to: http://localhost:8000/docs
   - Scroll to the **"Audit Logs"** section
   - Expand the audit configuration endpoints

3. **Try it out**:
   - Click on `/audit/config/status` or `/audit/config/documentation`
   - Click **"Try it out"** button
   - Click **"Execute"** button
   - View the response in the **"Responses"** section

### Method 2: ReDoc API Documentation

1. **Open ReDoc**:
   - Navigate to: http://localhost:8000/redoc
   - Search for "audit config" in the search box
   - View detailed endpoint documentation

### Method 3: Direct Browser Access

1. **Configuration Status**:
   - Open: http://localhost:8000/audit/config/status
   - Browser will display JSON response

2. **Configuration Documentation**:
   - Open: http://localhost:8000/audit/config/documentation
   - Browser will display JSON response

### Method 4: Command Line (curl)

```bash
# Get configuration status
curl http://localhost:8000/audit/config/status | jq '.'

# Get configuration documentation
curl http://localhost:8000/audit/config/documentation | jq '.'

# Check specific configuration values
curl -s http://localhost:8000/audit/config/status | jq '.security'
curl -s http://localhost:8000/audit/config/status | jq '.validation'
```

### Method 5: Python Client

```python
import requests

# Get configuration status
response = requests.get('http://localhost:8000/audit/config/status')
config = response.json()

print(f"Config Source: {config['config_source']}")
print(f"Backend Type: {config['backend']['type']}")
print(f"Security Status: {config['security']}")
print(f"Validation Status: {config['validation']['status']}")

# Get configuration documentation
response = requests.get('http://localhost:8000/audit/config/documentation')
docs = response.json()

print(f"Quick Start: {docs['quick_start']['production']}")
```

## Use Cases

### 1. Health Check / Monitoring

Monitor audit configuration status programmatically:

```bash
#!/bin/bash
# Check if encryption is enabled
STATUS=$(curl -s http://localhost:8000/audit/config/status)
ENCRYPTION_ENABLED=$(echo $STATUS | jq -r '.security.encryption_enabled')

if [ "$ENCRYPTION_ENABLED" != "true" ]; then
    echo "ALERT: Audit log encryption is disabled!"
    # Send alert
fi
```

### 2. Configuration Validation Dashboard

Create a dashboard that displays:
- Current configuration source
- Security feature status (encryption, RBAC, immutability)
- Validation warnings and errors
- Backend configuration
- Compliance mode

### 3. Deployment Verification

After deployment, verify configuration:

```bash
# Verify production configuration
curl -s http://localhost:8000/audit/config/status | jq '{
  dev_mode: .security.dev_mode,
  encryption: .security.encryption_key_configured,
  immutable: .security.immutable,
  tamper_detection: .security.tamper_detection_enabled,
  validation_status: .validation.status,
  errors: .validation.errors_count
}'
```

### 4. Automated Testing

Include in CI/CD pipeline:

```python
def test_audit_config():
    """Verify audit configuration in deployment"""
    response = requests.get('http://server/audit/config/status')
    config = response.json()
    
    # Check security requirements
    assert config['security']['encryption_enabled'] == True
    assert config['security']['immutable'] == True
    assert config['security']['dev_mode'] == False
    assert config['validation']['errors_count'] == 0
```

### 5. Documentation Access

Provide configuration help to operators:

```bash
# Get quick start guide
curl -s http://localhost:8000/audit/config/documentation | \
  jq '.quick_start.production[]'

# Get environment variable reference
curl -s http://localhost:8000/audit/config/documentation | \
  jq '.environment_variables.critical'
```

## Integration with Existing UI

The audit configuration endpoints are integrated into the existing audit log UI:

1. **Main Audit Page**: Existing audit log viewer at `/audit/logs/all`
2. **Configuration Tab** (recommended): Add a "Configuration" tab that calls `/audit/config/status`
3. **Help/Documentation**: Link to `/audit/config/documentation` for inline help

## Security Considerations

**Public Access**: These endpoints expose configuration information but:
- ✅ Do NOT expose sensitive values (keys, credentials)
- ✅ Only show configuration metadata and status
- ✅ Useful for monitoring and troubleshooting
- ⚠️ Consider adding authentication in production

**Sensitive Information Handling**:
- Encryption key is reported as "configured" or "not configured" (boolean)
- Actual key value is never exposed
- Backend parameters are not shown (may contain credentials)
- Configuration structure is shown, not sensitive data

**Recommendations**:
1. Add authentication/authorization to these endpoints in production
2. Restrict access to admin users or monitoring systems
3. Log access to these endpoints for audit purposes
4. Rate limit to prevent information disclosure attacks

## Makefile Commands

```bash
# Show API endpoint documentation
make audit-config-api-docs

# Start server with audit config access
make run-server
# Then open http://localhost:8000/docs

# Validate configuration before checking via API
make audit-config-validate
```

## Troubleshooting

### Issue: Endpoints return 404

**Solution**: Ensure server is running and audit router is registered
```bash
# Check if server is running
curl http://localhost:8000/health

# Check if audit router is loaded
curl http://localhost:8000/openapi.json | grep -A2 "/audit/config"
```

### Issue: Configuration shows as "environment_variables" but YAML file exists

**Solution**: Environment variables take precedence. Check:
```bash
# List audit environment variables
env | grep AUDIT_

# Unset to use YAML config
unset AUDIT_LOG_BACKEND_TYPE
# etc.
```

### Issue: Validation shows errors

**Solution**: Fix configuration issues identified in the response
```bash
# Get validation errors
curl -s http://localhost:8000/audit/config/status | jq '.validation.errors'

# Run validation script for detailed information
make audit-config-validate
```

## Additional Resources

- **Complete Configuration Guide**: `docs/AUDIT_CONFIGURATION.md`
- **Quick Start**: `generator/AUDIT_CONFIG_README.md`
- **Validation Script**: `python generator/audit_log/validate_config.py --help`
- **Server Documentation**: `server/README.md`

## Example: Building a Configuration Dashboard

```python
import requests
from rich.console import Console
from rich.table import Table

def display_audit_config_status():
    """Display audit configuration status in a nice table"""
    console = Console()
    
    response = requests.get('http://localhost:8000/audit/config/status')
    config = response.json()
    
    # Security status table
    table = Table(title="Audit Log Security Status")
    table.add_column("Feature", style="cyan")
    table.add_column("Status", style="green")
    
    security = config['security']
    table.add_row("Encryption", "✓" if security['encryption_enabled'] else "✗")
    table.add_row("Encryption Key", "✓" if security['encryption_key_configured'] else "✗")
    table.add_row("Immutable Logs", "✓" if security['immutable'] else "✗")
    table.add_row("Tamper Detection", "✓" if security['tamper_detection_enabled'] else "✗")
    table.add_row("RBAC", "✓" if security['rbac_enabled'] else "✗")
    table.add_row("Dev Mode", "✗" if not security['dev_mode'] else "⚠")
    
    console.print(table)
    
    # Validation warnings
    if config['validation']['warnings']:
        console.print("\n[yellow]Configuration Warnings:[/yellow]")
        for warning in config['validation']['warnings']:
            console.print(f"  ⚠ {warning}")
    
    # Validation errors
    if config['validation']['errors']:
        console.print("\n[red]Configuration Errors:[/red]")
        for error in config['validation']['errors']:
            console.print(f"  ✗ {error}")

if __name__ == "__main__":
    display_audit_config_status()
```

---

**Last Updated**: February 2026  
**Version**: 1.0  
**Status**: Production Ready
