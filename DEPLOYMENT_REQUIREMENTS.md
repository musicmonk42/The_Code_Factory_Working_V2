# Deployment Requirements Documentation

## Overview

The Code Factory deployment pipeline now ensures that **all generated projects** include complete and valid deployment artifacts for Docker, Kubernetes, and Helm. **CRITICAL**: All deployment files MUST accurately reflect the actual generated code, not generic templates.

## Key Principle: Deployment Files Must Match Generated Code

**Every deployment configuration must be based on analysis of the actual generated project files.**

This means:
- **Dockerfile** copies the actual dependencies file (requirements.txt, package.json, go.mod)
- **Ports** exposed match what the application actually listens on
- **Entry points** (CMD/ENTRYPOINT) use the actual main file
- **Environment variables** match what the code references
- **Health checks** point to actual endpoints the app exposes
- **Kubernetes/Helm configs** use actual application ports and service names

## Deployment Pipeline Changes

### 1. Placeholder Substitution Fix

**Issue**: The deploy stage was failing with `"Deploy config contains unsubstituted placeholders: {'<PORT_NUMBER>'}"` error.

**Solution**: Added missing placeholders to the `common_env_placeholders` dictionary in `generator/agents/deploy_agent/deploy_response_handler.py`:

```python
common_env_placeholders = {
    '{BUILD_ENV}': 'production',
    '{ENVIRONMENT}': 'production',
    '{NODE_ENV}': 'production',
    '{PORT}': '8000',
    '{HOST}': '0.0.0.0',
    '<PORT_NUMBER>': '8000',  # NEW
    '<PORT>': '8000',         # NEW
    '<HOST>': '0.0.0.0',      # NEW
    '<SERVICE_NAME>': 'app',  # NEW
}
```

### 2. Multi-Target Deployment (`deploy_all`)

**Issue**: Only one deploy target (docker) was running per job, not all three (docker, kubernetes, helm).

**Solution**: Added new `_run_deploy_all()` method in `server/services/omnicore_service.py` that:
- Executes all three deployment targets sequentially (docker, kubernetes, helm)
- Aggregates results from all targets
- Fails the pipeline if any required target fails
- Returns comprehensive status for each target

**Usage**:
```python
deploy_result = await self._run_deploy_all(job_id, deploy_payload)
# Returns:
# {
#   "status": "completed" or "error",
#   "results": {...},  # Results for each target
#   "generated_files": [...],  # All files across targets
#   "failed_targets": [...],  # List of failed targets
#   "completed_targets": [...],  # List of successful targets
# }
```

### 3. Deployment Completeness Validator

**Issue**: No validator ensured all required deployment artifacts exist and are valid, or that they match the generated code.

**Solution**: Created `DeploymentCompletenessValidator` class in `generator/agents/deploy_agent/deploy_validator.py` that validates:

#### Required Files by Deployment Type

**Docker**:
- `Dockerfile` - Must contain `FROM` instruction
- `docker-compose.yml` - Valid YAML
- `.dockerignore` - Excludes build artifacts

**Kubernetes**:
- `k8s/deployment.yaml` - Valid YAML with deployment configuration
- `k8s/service.yaml` - Valid YAML with service configuration
- `k8s/configmap.yaml` - Valid YAML with config map

**Helm**:
- `helm/Chart.yaml` - Valid YAML with chart metadata
- `helm/values.yaml` - Valid YAML with default values
- `helm/templates/` - Directory containing chart templates

#### Validation Checks

1. **File Existence**: All required files exist
2. **YAML Validation**: All YAML files have valid syntax
3. **Dockerfile Validation**: Contains required instructions (FROM, etc.)
4. **Placeholder Validation**: No unsubstituted placeholders like `<PORT_NUMBER>`, `{VARIABLE}`, etc.
5. **Code Matching Validation** (NEW): 
   - Dockerfile references actual dependency files (requirements.txt, package.json, go.mod)
   - Dockerfile uses actual entry points found in the project
   - Deployment configs don't use generic values when actual values are available

**Exceptions**: Helm template placeholders like `{{ .Values.x }}` are allowed as they are valid Go template syntax.

### 4. Pipeline Integration

**Changes to Pipeline** (`server/services/omnicore_service.py`):

1. **Required Deployment Stage**: Deployment is now a **required** stage (not optional)
   - Pipeline fails if deployment fails
   - Use `include_deployment=False` to explicitly skip

2. **Validation After Deployment**: 
   - Runs `_validate_deployment_completeness()` after all targets complete
   - Pipeline fails if validation fails
   - Provides detailed error messages for missing or invalid files

3. **Error Handling**:
   - Clear error messages for failed targets
   - Lists which targets succeeded/failed
   - Reports validation errors with file-level detail

## Usage Examples

### Basic Pipeline Run

```python
# Deployment enabled by default
payload = {
    "requirements": "Build a REST API",
    "include_deployment": True,  # Default
}
result = await omnicore_service.run_pipeline(job_id, payload)
```

### Skip Deployment (Opt-Out)

```python
# Explicitly skip deployment
payload = {
    "requirements": "Build a REST API",
    "include_deployment": False,
}
result = await omnicore_service.run_pipeline(job_id, payload)
```

### Checking Deployment Results

```python
result = await omnicore_service.run_pipeline(job_id, payload)

if result["status"] == "failed":
    # Check if deployment failed
    if "failed_targets" in result:
        print(f"Failed targets: {result['failed_targets']}")
    
    # Check validation errors
    if "validation_errors" in result:
        print(f"Validation errors: {result['validation_errors']}")
```

## How Deploy Agent Analyzes Generated Code

The deploy agent uses a multi-step process to ensure deployment files match the actual generated code:

### 1. Context Gathering (`gather_context()`)

The deploy agent automatically reads and analyzes:

- **requirements.txt** - Python dependencies and versions
- **package.json** - JavaScript/TypeScript dependencies and scripts
- **go.mod** - Go module requirements
- **Main application files** - Entry points, ports, configuration
- **Git commits** - Recent changes for context

### 2. File Content Analysis (`gather_context_for_prompt()`)

Before generating deployment configs, the agent:

- Reads actual file contents from the repository
- Extracts language/framework information
- Identifies dependencies with versions
- Detects common entry points (main.py, app.py, server.js, etc.)

### 3. Enhanced Prompt Templates

The deployment prompt templates now explicitly:

- Include actual file contents (first 300-500 chars of each file)
- Show detected dependencies with versions
- Emphasize using EXACT values from code (ports, entry points, env vars)
- Warn against generic templates

**Example from docker_default.jinja**:
```jinja
**CRITICAL**: You MUST analyze the actual generated project files below 
and create a Dockerfile that accurately reflects what was built. 
Do NOT use generic templates.

## Actual File Contents (Use These to Determine Configuration)
{% for filename, content in context.files_content.items() %}
### File: {{ filename }}
{{ content[:500] }}
{% endfor %}

**IMPORTANT**: Based on the above file contents:
- Detect the correct entry point (main file, app.py, server.js, etc.)
- Identify the actual port the application listens on
- Determine the exact dependencies and their versions
```

### 4. Validation Against Code

The `DeploymentCompletenessValidator` performs additional checks:

- Verifies Dockerfile references actual dependency files found in project
- Checks that detected entry points are used in CMD/ENTRYPOINT
- Warns if deployment configs use generic values when actual values exist

## Success Criteria

After these changes:

✅ **No Placeholder Failures**: `<PORT_NUMBER>` and similar placeholders never cause deploy failures

✅ **Complete Deployment Artifacts**: Every successful job includes Docker, Kubernetes, AND Helm artifacts

✅ **Validated Deployments**: DeploymentCompletenessValidator confirms all files exist and are valid

✅ **Fast Failure**: Pipeline fails immediately if deployment artifacts are incomplete or invalid

✅ **Clear Error Messages**: Detailed error reporting for debugging deployment issues

✅ **Code-Accurate Deployments** (NEW): Deployment files reflect actual generated code, not generic templates
  - Dockerfile copies actual dependencies (requirements.txt, package.json, etc.)
  - Ports match actual application ports
  - Entry points match actual main files
  - Environment variables match code references

## Troubleshooting

### Deployment Validation Failed

**Error**: `Deployment validation failed: Required file missing for docker: Dockerfile`

**Solution**: Ensure the deploy agent generates all required files. Check the deploy agent logs for errors.

### Failed Targets

**Error**: `Deployment failed for targets: kubernetes, helm`

**Solution**: 
1. Check if the deploy agent has plugins for all targets
2. Review deploy agent logs for specific errors
3. Ensure LLM is available and configured properly

### Unsubstituted Placeholders

**Error**: `Unsubstituted placeholders found in Dockerfile: ['<PORT_NUMBER>']`

**Solution**: This should not happen after the fix. If it does:
1. Check that `common_env_placeholders` includes the placeholder
2. Verify the placeholder substitution logic runs before validation
3. Check deploy agent logs for substitution errors

## Configuration

### Environment Variables

- `SKIP_DOCKER_VALIDATION=true`: Skip Docker build validation (for CI environments without Docker daemon)

### Feature Flags

- `include_deployment`: Set to `False` to skip deployment stage entirely

## Migration Guide

### For Existing Jobs

Existing jobs that only generated Docker artifacts will now:
1. Generate Kubernetes manifests
2. Generate Helm charts
3. Validate all artifacts before completion

### For Custom Deploy Agents

If you have custom deploy agent plugins:
1. Ensure they generate all required files (see Required Files section)
2. Test with `DeploymentCompletenessValidator`
3. Handle placeholder substitution properly

## Related Files

- `generator/agents/deploy_agent/deploy_response_handler.py` - Placeholder substitution
- `generator/agents/deploy_agent/deploy_validator.py` - Completeness validator
- `server/services/omnicore_service.py` - Pipeline orchestration and deploy_all method

## Testing

Run the verification script to test all changes:

```bash
python3 /tmp/test_deployment_changes.py
```

This validates:
- Placeholder fix is in place
- deploy_all method exists and runs all targets
- DeploymentCompletenessValidator is registered
- Pipeline integration is complete

## Future Enhancements

Potential future improvements:

1. **Parallel Deployment**: Run deployment targets in parallel for faster execution
2. **Selective Targets**: Allow users to specify which targets to generate (e.g., only Docker + Kubernetes)
3. **Custom Validators**: Plugin system for custom deployment validators
4. **Auto-Repair**: Automatically fix common deployment issues (missing files, placeholders)
5. **Deployment Testing**: Actually deploy and test generated artifacts in sandbox environments
