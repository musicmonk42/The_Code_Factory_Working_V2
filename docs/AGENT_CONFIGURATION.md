# Generator Agent Integration Configuration Guide

## Overview

The Code Factory platform  includes robust integration with generator agents that use LLM providers (OpenAI, Grok, Anthropic, Google) to perform code generation, test generation, deployment configuration, documentation generation, and security scanning.

## Quick Start

### 1. Configure at Least One LLM Provider

Copy `.env.example` to `.env` and add your API key:

```bash
cp .env.example .env
```

Then edit `.env` and set at least one API key:

```bash
# For OpenAI (GPT-4)
OPENAI_API_KEY=sk-your-actual-api-key-here

# OR for xAI Grok
GROK_API_KEY=your-grok-api-key-here

# OR for Anthropic Claude
ANTHROPIC_API_KEY=your-anthropic-api-key-here

# OR for Google Gemini
GOOGLE_API_KEY=your-google-api-key-here
```

### 2. Choose Your Default Provider

Set which LLM provider to use by default:

```bash
DEFAULT_LLM_PROVIDER=openai  # Options: openai, grok, anthropic, google
```

### 3. Start the Server

```bash
# Using Docker Compose
docker-compose up

# Or directly with Python
python -m uvicorn server.main:app --host 0.0.0.0 --port 8000
```

## Configuration Reference

### LLM Provider Settings

#### OpenAI Configuration

```bash
OPENAI_API_KEY=sk-your-key-here
OPENAI_MODEL=gpt-4  # Options: gpt-4, gpt-4-turbo, gpt-3.5-turbo
OPENAI_BASE_URL=https://api.openai.com/v1  # Optional: for Azure OpenAI
```

#### xAI Grok Configuration

```bash
GROK_API_KEY=your-key-here
GROK_MODEL=grok-beta
```

#### Anthropic Claude Configuration

```bash
ANTHROPIC_API_KEY=your-key-here
ANTHROPIC_MODEL=claude-3-sonnet-20240229  # Options: claude-3-opus, claude-3-sonnet, claude-3-haiku
```

#### Google Gemini Configuration

```bash
GOOGLE_API_KEY=your-key-here
GOOGLE_MODEL=gemini-pro
```

### LLM Behavior Settings

```bash
# Default provider to use
DEFAULT_LLM_PROVIDER=openai

# Timeout for LLM API requests (seconds)
LLM_TIMEOUT=300

# Maximum retry attempts for failed requests
LLM_MAX_RETRIES=3

# Temperature for generation (0.0 = deterministic, 2.0 = very random)
LLM_TEMPERATURE=0.7

# Enable ensemble mode (query multiple LLMs and combine results)
ENABLE_ENSEMBLE_MODE=false

# Enable response caching to reduce costs
ENABLE_LLM_CACHING=true
```

### Agent Configuration

Enable or disable specific agents:

```bash
ENABLE_CODEGEN_AGENT=true      # Code generation
ENABLE_TESTGEN_AGENT=true      # Test generation
ENABLE_DEPLOY_AGENT=true       # Deployment configuration
ENABLE_DOCGEN_AGENT=true       # Documentation generation
ENABLE_CRITIQUE_AGENT=true     # Security scanning
ENABLE_CLARIFIER=true          # Requirements clarification
```

### Agent Behavior

```bash
# Fail fast if agents cannot be imported (recommended for production)
STRICT_MODE=false

# Use LLM-based clarification instead of rule-based
USE_LLM_CLARIFIER=true

# Directory for storing uploads and generated code
UPLOAD_DIR=./uploads
```

## Deployment-Specific Configuration

### Docker Compose

The `docker-compose.yml` automatically passes environment variables:

```yaml
environment:
  - GROK_API_KEY=${GROK_API_KEY:-}
  - OPENAI_API_KEY=${OPENAI_API_KEY:-}
  - DEFAULT_LLM_PROVIDER=${DEFAULT_LLM_PROVIDER:-openai}
```

Make sure to set these in your host environment or in a `.env` file.

### Railway

Railway automatically loads environment variables from your `.env` file or from the Railway dashboard. The `railway.toml` and `Procfile` are already configured correctly.

Add environment variables in the Railway dashboard:
1. Go to your project settings
2. Navigate to "Variables"
3. Add `OPENAI_API_KEY`, `DEFAULT_LLM_PROVIDER`, etc.

### Heroku

Set environment variables using the Heroku CLI:

```bash
heroku config:set OPENAI_API_KEY=sk-your-key-here
heroku config:set DEFAULT_LLM_PROVIDER=openai
```

## Graceful Degradation

The system is designed to handle missing configuration gracefully:

### Without LLM Configuration

- **Agents will be marked as unavailable**
- **API endpoints will return clear error messages**
- **System will still start and serve non-agent endpoints**
- **Warnings will be logged at startup**

Example log output:

```
WARNING - No LLM providers configured. Agents will use fallback/mock behavior.
         Set API keys in .env file (OPENAI_API_KEY, GROK_API_KEY, etc.)
WARNING - Some agents unavailable: codegen, testgen, deploy, docgen, critique
```

### With Partial Configuration

If some dependencies are missing:

- **Available agents will work normally**
- **Unavailable agents will return descriptive errors**
- **System remains operational for working components**

## Validation

### Check Configuration Status

The configuration is validated at startup. Check logs for:

```
INFO - Configuration validation passed
INFO - Available LLM providers: openai, grok
INFO - OmniCoreService initialized. Available agents: codegen, testgen, clarifier
```

### Validate Programmatically

```python
from server.config import validate_configuration

results = validate_configuration()
print(f"Valid: {results['valid']}")
print(f"Available providers: {results['available_providers']}")
print(f"Warnings: {results['warnings']}")
print(f"Errors: {results['errors']}")
```

### Test Agent Availability

```python
from server.services.omnicore_service import OmniCoreService

service = OmniCoreService()
print(f"Agent availability: {service.agents_available}")
```

## Production Best Practices

### 1. Use Environment Variables

Never commit API keys to version control. Always use environment variables:

```bash
# Good - environment variable
OPENAI_API_KEY=sk-actual-key

# Bad - hardcoded in code
api_key = "sk-actual-key"  # NEVER DO THIS
```

### 2. Enable Strict Mode

In production, enable strict mode to fail fast:

```bash
STRICT_MODE=true
```

This ensures the application won't start if agents are misconfigured.

### 3. Set Appropriate Timeouts

For production workloads, adjust timeouts based on your needs:

```bash
LLM_TIMEOUT=600  # 10 minutes for complex generation tasks
LLM_MAX_RETRIES=5  # More retries for reliability
```

### 4. Monitor LLM Usage

Enable logging and monitoring:

```bash
LOG_LEVEL=INFO
LOG_FORMAT=json
ENABLE_METRICS=true
```

### 5. Use Caching

Enable LLM response caching to reduce costs:

```bash
ENABLE_LLM_CACHING=true
REDIS_URL=redis://redis:6379
```

## Troubleshooting

### "No LLM providers configured"

**Solution**: Set at least one API key in `.env`:

```bash
OPENAI_API_KEY=sk-your-key-here
```

### "Codegen agent is not available"

**Possible causes**:
1. No LLM provider configured
2. Missing dependencies
3. Import errors

**Solutions**:
1. Verify API key is set: `echo $OPENAI_API_KEY`
2. Check logs for specific import errors
3. Reinstall dependencies: `pip install -r requirements.txt`

### "ImportError: No module named X"

**Solution**: Install missing dependencies:

```bash
pip install -r requirements.txt
```

### Agent works locally but not in Docker

**Solution**: Ensure environment variables are passed to Docker:

```bash
docker run -e OPENAI_API_KEY=$OPENAI_API_KEY ...
```

Or use `docker-compose.yml` which handles this automatically.

### "Connection timeout" errors

**Solution**: Increase timeout values:

```bash
LLM_TIMEOUT=600
REQUEST_TIMEOUT=600
```

## Security Considerations

### API Key Management

1. **Never log API keys**: The configuration module uses `SecretStr` to mask keys
2. **Use secrets management**: Consider AWS Secrets Manager or HashiCorp Vault
3. **Rotate keys regularly**: Update API keys periodically
4. **Limit key permissions**: Use provider-specific permission controls

### Rate Limiting

Configure rate limiting to prevent abuse:

```bash
# In your .env or environment
RATE_LIMIT_ENABLED=true
RATE_LIMIT_REQUESTS_PER_MINUTE=60
```

### Network Security

For production:

```bash
# Use HTTPS for LLM API calls
OPENAI_BASE_URL=https://api.openai.com/v1

# Enable SSL verification
SSL_VERIFY=true
```

## Testing

### Unit Tests

Run configuration tests:

```bash
pytest server/tests/test_agent_integration.py::TestConfigurationManagement -v
```

### Integration Tests

Test agent integration:

```bash
pytest server/tests/test_agent_integration.py -v
```

### Manual Testing

Test configuration validation:

```bash
python -c "
from server.config import validate_configuration
import json
print(json.dumps(validate_configuration(), indent=2))
"
```

## Support

For issues or questions:

1. Check logs: `docker-compose logs codefactory`
2. Review configuration: Ensure `.env` matches `.env.example`
3. Validate setup: Run the validation script above
4. Check agent status: Review startup logs for availability

## Migration Guide

### From Previous Versions

If you're upgrading from a version without the new configuration system:

1. **Copy new environment variables** from `.env.example`
2. **Set your LLM provider**: Add `DEFAULT_LLM_PROVIDER=openai`
3. **Keep existing variables**: Don't remove existing configuration
4. **Restart services**: `docker-compose restart`

### Environment Variable Mapping

Old → New:

```bash
# Old (if you had custom setup)
API_KEY → OPENAI_API_KEY
LLM_BACKEND → DEFAULT_LLM_PROVIDER
MODEL_NAME → OPENAI_MODEL

# New standardized format
OPENAI_API_KEY=sk-...
DEFAULT_LLM_PROVIDER=openai
OPENAI_MODEL=gpt-4
```
