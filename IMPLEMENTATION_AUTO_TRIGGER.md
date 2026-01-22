# Auto-Trigger Pipeline After Upload with Automatic LLM Detection

## Overview

This implementation adds two key features to the Code Factory platform:

1. **Auto-Trigger Pipeline After Upload**: Automatically starts the full generation pipeline after README files are uploaded, eliminating the need for manual API calls to trigger the pipeline.

2. **Automatic LLM Provider Detection**: Automatically detects which LLM provider is configured via environment variables, removing the need for manual configuration.

## Implementation Details

### 1. LLM Provider Auto-Detection

**File: `server/config.py`**

Added two new helper functions:

#### `detect_available_llm_provider()`
- Checks environment variables in priority order:
  1. `OPENAI_API_KEY` → OpenAI
  2. `ANTHROPIC_API_KEY` → Anthropic/Claude
  3. `XAI_API_KEY` or `GROK_API_KEY` → xAI/Grok
  4. `GOOGLE_API_KEY` → Google/Gemini
  5. `OLLAMA_HOST` → Ollama (local)
- Returns the first available provider or `None` if none found
- Logs which provider was detected

#### `get_default_model_for_provider(provider: str)`
- Returns appropriate default model for each provider:
  - OpenAI: `gpt-4-turbo`
  - Anthropic: `claude-3-sonnet-20240229`
  - xAI/Grok: `grok-beta`
  - Google: `gemini-pro`
  - Ollama: `codellama`

#### Enhanced `LLMProviderConfig`
- Added `xai_api_key` field to support `XAI_API_KEY` environment variable
- Added `ollama_host` and `ollama_model` fields for Ollama support
- Updated `get_provider_api_key()` to handle both `XAI_API_KEY` and `GROK_API_KEY`
- Updated `is_provider_configured()` to check `OLLAMA_HOST` for Ollama
- Updated `get_available_providers()` to include Ollama

### 2. OmniCore Service Integration

**File: `server/services/omnicore_service.py`**

#### Enhanced `_build_llm_config()`
- Calls `detect_available_llm_provider()` when default provider is not configured
- Logs auto-detected provider at initialization
- Provides clear error messages if no provider is found
- Sets both `XAI_API_KEY` and `GROK_API_KEY` environment variables for xAI
- Sets `OLLAMA_HOST` environment variable for Ollama

**Key Features:**
- Graceful fallback if config module is not available
- Clear logging for debugging
- Comprehensive error messages listing all required environment variables

### 3. Auto-Trigger Pipeline After Upload

**File: `server/routers/generator.py`**

#### New Background Task Function
```python
async def _trigger_pipeline_background(
    job_id: str,
    readme_content: str,
    generator_service: GeneratorService,
)
```

**Features:**
- Auto-detects programming language from README content:
  - Checks for keywords: JavaScript, TypeScript, Java, Go, Rust
  - Defaults to Python if no match found
- Calls `generator_service.run_full_pipeline()` with sensible defaults:
  - `include_tests=True`
  - `include_deployment=True`
  - `include_docs=True`
  - `run_critique=True`
- Logs success and errors appropriately

#### Modified `upload_files()` Endpoint
- Added `BackgroundTasks` parameter from FastAPI
- Extracts README content during file upload
- Improved README detection to match any filename containing 'readme'
- Triggers background task if README content is found
- Returns `pipeline_triggered` flag in response
- Updates success message to indicate pipeline was triggered

**Response Format:**
```json
{
  "success": true,
  "message": "Uploaded 1 files successfully. Pipeline auto-triggered.",
  "data": {
    "uploaded_files": [...],
    "categorization": {...},
    "pipeline_triggered": true
  }
}
```

### 4. Environment Variables

**Updated Files:**
- `.env.example`: Added XAI_API_KEY, OLLAMA_HOST, OLLAMA_MODEL
- `docker-compose.yml`: Added XAI_API_KEY and OLLAMA_HOST environment variables

**New Environment Variables:**
```bash
# xAI/Grok (supports both variables)
XAI_API_KEY=your-xai-api-key-here
GROK_API_KEY=your-grok-api-key-here
GROK_MODEL=grok-beta

# Ollama (local LLM)
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=codellama
```

### 5. Tests

**File: `server/tests/test_auto_trigger.py`**

Created comprehensive test suite covering:

#### LLM Auto-Detection Tests
- Test detection of each provider (OpenAI, Anthropic, xAI, Google, Ollama)
- Test priority order (OpenAI > Anthropic > xAI > Google > Ollama)
- Test handling of both `XAI_API_KEY` and `GROK_API_KEY`
- Test when no provider is configured
- Test default model retrieval for each provider

#### Auto-Trigger Pipeline Tests
- Test pipeline triggers when README.md is uploaded
- Test pipeline doesn't trigger without README
- Test response includes `pipeline_triggered` flag
- Test proper message in response

#### Language Detection Tests
- Validates detection logic for Python, JavaScript, TypeScript, Java, Go, Rust

## Usage

### Basic Flow

1. **Create a job:**
   ```bash
   POST /api/jobs
   ```

2. **Upload README.md:**
   ```bash
   POST /api/generator/{job_id}/upload
   Files: README.md
   ```

3. **Pipeline automatically starts!**
   - No manual trigger needed
   - Job progresses through: clarify → codegen → testgen → deploy → docgen → critique

### Monitoring Progress

```bash
GET /api/generator/{job_id}/status
```

Response will show current stage and progress percentage.

## Configuration Examples

### Using OpenAI (Priority 1)
```bash
OPENAI_API_KEY=sk-...
```

### Using Anthropic (Priority 2)
```bash
ANTHROPIC_API_KEY=sk-ant-...
```

### Using xAI/Grok (Priority 3)
```bash
XAI_API_KEY=xai-...
# OR
GROK_API_KEY=xai-...
```

### Using Google Gemini (Priority 4)
```bash
GOOGLE_API_KEY=AIza...
```

### Using Ollama (Priority 5)
```bash
OLLAMA_HOST=http://localhost:11434
```

### Explicit Provider Selection
```bash
DEFAULT_LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
```

## Language Detection Logic

The system auto-detects the programming language from README content:

- **Python**: Default (no specific keywords needed)
- **JavaScript**: Keywords: "javascript", "node.js", "npm"
- **TypeScript**: Keyword: "typescript"
- **Java**: Keyword: "java" (excluding "javascript")
- **Go**: Keywords: "go", "golang"
- **Rust**: Keyword: "rust"

## Error Handling

### No LLM Provider Configured
```
ERROR: No LLM provider configured. Please set API keys in environment variables:
  - OPENAI_API_KEY for OpenAI
  - ANTHROPIC_API_KEY for Anthropic/Claude
  - XAI_API_KEY or GROK_API_KEY for xAI/Grok
  - GOOGLE_API_KEY for Google/Gemini
  - OLLAMA_HOST for Ollama (local)
```

### No README Uploaded
```
WARNING: No README.md found in uploaded files for job {job_id}.
Pipeline will not be auto-triggered.
```

## Benefits

1. **Improved User Experience**
   - Single API call to start entire generation flow
   - No need to manually trigger pipeline

2. **Reduced Configuration**
   - Automatic LLM provider detection
   - Smart defaults for all providers

3. **Better Debugging**
   - Clear logging of detected provider
   - Comprehensive error messages

4. **Flexible Deployment**
   - Works in Docker, Kubernetes, cloud platforms
   - Supports multiple LLM providers
   - Graceful fallbacks

## Docker Deployment

The Docker build was validated and completes successfully:

```bash
docker build --build-arg SKIP_HEAVY_DEPS=1 -t codefactory-test:latest .
```

All environment variables are properly configured in `docker-compose.yml`:

```yaml
environment:
  - XAI_API_KEY=${XAI_API_KEY:-}
  - GROK_API_KEY=${GROK_API_KEY:-}
  - OPENAI_API_KEY=${OPENAI_API_KEY:-}
  - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}
  - GOOGLE_API_KEY=${GOOGLE_API_KEY:-}
  - OLLAMA_HOST=${OLLAMA_HOST:-}
  - DEFAULT_LLM_PROVIDER=${DEFAULT_LLM_PROVIDER:-openai}
```

## Testing

Run the test suite:

```bash
pytest server/tests/test_auto_trigger.py -v
```

Test coverage includes:
- LLM provider detection
- Auto-trigger functionality
- Language detection
- Error handling
- Edge cases

## Industry Standards Compliance

This implementation follows industry best practices:

1. **12-Factor App**: Environment-based configuration
2. **Fail-Fast**: Clear errors at configuration time
3. **Graceful Degradation**: Fallbacks when providers unavailable
4. **Type Safety**: Pydantic validation for all configs
5. **Security**: SecretStr for API keys, proper masking
6. **Logging**: Comprehensive logging for debugging
7. **Testing**: Full test coverage for all features
8. **Documentation**: Clear docstrings and user documentation
9. **Docker**: Validated containerization
10. **Backwards Compatibility**: All existing functionality preserved

## Future Enhancements

Potential improvements for future iterations:

1. **Advanced Language Detection**: Use file extensions and content analysis
2. **Configurable Pipeline Steps**: Allow users to specify which steps to run
3. **Pipeline Templates**: Pre-configured pipelines for common use cases
4. **Real-time Progress**: WebSocket notifications for pipeline progress
5. **Multi-LLM Support**: Use different providers for different stages

## Troubleshooting

### Pipeline Not Triggering
- Ensure README file is named with 'readme' in the filename (case-insensitive)
- Check logs for README detection messages
- Verify job status after upload

### LLM Provider Not Detected
- Check environment variables are set correctly
- Review logs for auto-detection messages
- Verify API key format is correct

### Language Detection Issues
- Add explicit language keywords to README
- Consider using explicit language parameter in API calls

## Conclusion

This implementation significantly improves the user experience by:
- Eliminating manual pipeline triggering
- Automatically detecting LLM providers
- Providing clear feedback and error messages
- Following industry best practices

The changes are minimal, focused, and maintain backwards compatibility while adding powerful new functionality.
