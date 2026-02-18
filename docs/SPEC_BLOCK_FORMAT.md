# Spec Block Format Guide

## Overview

The Code Factory now supports **structured YAML specification blocks** embedded in your README files. These spec blocks provide authoritative, structured input that takes priority over unstructured text extraction, ensuring consistent and predictable code generation.

## Why Use Spec Blocks?

✅ **Explicit Control**: Clearly define project structure without ambiguity  
✅ **Priority Processing**: Spec blocks are parsed first, overriding text-based extraction  
✅ **Type Safety**: Validated schema prevents common mistakes  
✅ **Gap Filling**: Interactive questions fill in missing required fields  
✅ **Reproducibility**: Lock files ensure consistent generation across runs  

## Basic Format

Spec blocks are fenced code blocks with the `code_factory:` marker:

````markdown
```code_factory:
project_type: fastapi_service
package_name: my_app
output_dir: generated/my_app
```
````

## Complete Example

Here's a comprehensive example showing all available fields:

````markdown
# My API Service

Brief description of your project.

```code_factory:
# Core project identification
project_type: fastapi_service
package_name: my_api
module_name: my_api
output_dir: generated/my_api

# Service interfaces
interfaces:
  http:
    - GET /health
    - POST /api/items
    - GET /api/items/{id}
    - PUT /api/items/{id}
    - DELETE /api/items/{id}
  events:
    - item.created
    - item.updated
    - item.deleted
  queues:
    - task_queue
    - dead_letter_queue

# Python dependencies (pip-installable)
dependencies:
  - fastapi>=0.100.0
  - pydantic>=2.0.0
  - uvicorn[standard]>=0.23.0
  - sqlalchemy>=2.0.0
  - alembic>=1.11.0

# Non-functional requirements
nonfunctional:
  - rate_limiting: 100 requests/minute
  - authentication: JWT with refresh tokens
  - logging: structured JSON logs
  - monitoring: Prometheus metrics
  - tracing: OpenTelemetry

# Backend adapters
adapters:
  database: postgresql
  cache: redis
  message_broker: rabbitmq
  storage: s3

# Post-generation validation checks
acceptance_checks:
  - All HTTP endpoints return proper status codes
  - Database migrations apply successfully
  - Tests pass with >80% coverage
  - OpenAPI schema validates
  - Security scan shows no critical vulnerabilities

# Schema version for compatibility
schema_version: "1.0"
```

## Additional content...
````

## Field Reference

### Required Fields

These fields must be provided (either in spec block or via interactive questions):

#### `project_type`
Type of project to generate. Determines scaffolding and structure.

**Supported Types:**
- `fastapi_service` - FastAPI REST API service
- `flask_service` - Flask web application
- `django_service` - Django web application
- `cli_tool` - Command-line interface tool
- `library` - Python library/package
- `batch_job` - Batch processing job
- `lambda_function` - AWS Lambda function
- `microservice` - Generic microservice
- `data_pipeline` - Data processing pipeline

**Example:**
```yaml
project_type: fastapi_service
```

#### `package_name`
Python package/module name. Used for imports.

**Rules:**
- Lowercase letters, numbers, underscores only
- Must start with a letter or underscore
- Used in import statements: `from <package_name> import ...`

**Example:**
```yaml
package_name: user_service
```

#### `output_dir`
Directory where generated code will be written.

**Rules:**
- Relative paths recommended for portability
- Avoid leading/trailing slashes
- Common pattern: `generated/<package_name>`

**Example:**
```yaml
output_dir: generated/user_service
```

### Optional Fields

#### `module_name`
Main module name. Defaults to `package_name` if not specified.

```yaml
module_name: user_service
```

#### `interfaces`
Service interfaces defining how the application communicates.

**Subfields:**
- `http`: List of HTTP endpoints (format: `METHOD /path`)
- `events`: List of event types in dot notation
- `queues`: List of queue names
- `grpc`: List of gRPC services
- `websocket`: List of WebSocket endpoints

**Example:**
```yaml
interfaces:
  http:
    - GET /health
    - POST /users
    - GET /users/{id}
  events:
    - user.created
    - user.updated
  queues:
    - email_queue
```

#### `dependencies`
Python package dependencies (pip-installable format).

**Example:**
```yaml
dependencies:
  - fastapi>=0.100.0
  - pydantic>=2.0.0
  - python-jose[cryptography]
```

#### `nonfunctional`
Non-functional requirements like rate limiting, auth, monitoring.

**Example:**
```yaml
nonfunctional:
  - rate_limiting: 100/minute
  - authentication: JWT
  - logging: structured JSON
  - monitoring: Prometheus + Grafana
```

#### `adapters`
Backend adapters and integrations.

**Common Adapters:**
- `database`: Database system (postgresql, mysql, sqlite, mongodb)
- `cache`: Caching system (redis, memcached)
- `message_broker`: Message broker (rabbitmq, kafka, sqs)
- `storage`: Object storage (s3, gcs, azure)
- `search`: Search engine (elasticsearch, opensearch)

**Example:**
```yaml
adapters:
  database: postgresql
  cache: redis
  message_broker: kafka
```

#### `acceptance_checks`
Post-generation validation criteria.

**Example:**
```yaml
acceptance_checks:
  - All endpoints return 200 OK
  - Database migrations applied
  - Tests pass with coverage >80%
  - No security vulnerabilities
```

## Alternative Syntax

You can also use a YAML block with a comment marker:

````markdown
```yaml
# code_factory
project_type: cli_tool
package_name: my_cli
output_dir: generated/cli
```
````

## Multiple Spec Blocks

For multi-service repositories, include multiple spec blocks:

````markdown
# Microservices Project

## User Service
```code_factory:
project_type: fastapi_service
package_name: user_service
output_dir: generated/user_service
```

## Auth Service
```code_factory:
project_type: fastapi_service
package_name: auth_service
output_dir: generated/auth_service
```
````

## Interactive Gap-Filling

If required fields are missing, the Code Factory will:

1. **Detect Missing Fields**: Identify incomplete specifications
2. **Generate Questions**: Create targeted prompts for missing data
3. **Interactive Session**: Present questions in CLI/API
4. **Create Lock File**: Save answers to `spec.lock.yaml`
5. **Use Lock File**: Subsequent runs use locked specification

### Example Question Flow

```
**********************************************************
SPECIFICATION GAP-FILLING
**********************************************************

The specification is incomplete. Please answer 3 question(s):

[Question 1/3]
============================================================
Question: What type of project are you building?
Hint: This determines the scaffolding, structure, and generated files.
Examples: fastapi_service, cli_tool, library, batch_job, lambda_function
Default: fastapi_service
============================================================
Your answer [fastapi_service]: <press Enter to use default>

[Question 2/3]
...
```

## spec.lock.yaml

After answering questions, a lock file is generated:

```yaml
project_type: fastapi_service
package_name: my_app
module_name: my_app
output_dir: generated/my_app
interfaces:
  http:
    - GET /health
dependencies: []
nonfunctional: []
adapters: {}
acceptance_checks: []
schema_version: "1.0"
generated_at: "2025-02-18T18:30:00.123456"
answered_questions:
  - field_name: project_type
    value: fastapi_service
    confidence: 0.7
    source: default
```

## Validation and Contract Enforcement

After code generation, the pipeline validates:

✅ **Required Files Exist**: All specified files are present  
✅ **Module Paths Match**: Import paths align with package structure  
✅ **Interfaces Present**: Requested endpoints/events have implementations  
✅ **Dependencies Installed**: requirements.txt is complete  
✅ **Acceptance Criteria**: Custom checks pass  

Validation failures generate a detailed diff-style report and halt the pipeline.

## Best Practices

### 1. Start with Minimal Spec
```yaml
project_type: fastapi_service
package_name: my_app
output_dir: generated/my_app
```
Let the question loop fill in the rest.

### 2. Be Explicit About Interfaces
```yaml
interfaces:
  http:
    - GET /health  # Always include health check
    - GET /metrics # Monitoring endpoint
```

### 3. Version Your Dependencies
```yaml
dependencies:
  - fastapi>=0.100.0,<0.110.0  # Pin major version
```

### 4. Define Clear Acceptance Checks
```yaml
acceptance_checks:
  - Health endpoint returns 200
  - Database connection succeeds
  - All tests pass
```

### 5. Use Consistent Naming
- `package_name`: `user_service` (lowercase, underscores)
- `output_dir`: `generated/user_service` (matches package name)

## Migration from Unstructured READMEs

If you have an existing README without spec blocks:

1. **Add a Spec Block**: Insert minimal spec at top of README
2. **Run Generation**: Let question loop fill gaps
3. **Review Lock File**: Check `spec.lock.yaml` for accuracy
4. **Update Spec Block**: Copy lock file contents into README spec block
5. **Commit**: Version control the complete spec

## Troubleshooting

### "Spec block not found"
- Ensure code fence uses `code_factory:` marker
- Check YAML syntax (indentation, colons, dashes)

### "Invalid project_type"
- Use one of the supported types (see Field Reference)
- Check for typos (e.g., `fastapi-service` vs `fastapi_service`)

### "Missing required field"
- Run in interactive mode to answer questions
- Or add missing fields to spec block manually

### "Output directory conflict"
- Ensure `output_dir` is relative path
- Avoid absolute paths or paths outside project

## Examples

### Minimal CLI Tool
````markdown
```code_factory:
project_type: cli_tool
package_name: my_cli
output_dir: generated/cli
```
````

### Full-Featured API Service
````markdown
```code_factory:
project_type: fastapi_service
package_name: order_api
output_dir: generated/order_api
interfaces:
  http:
    - GET /health
    - POST /orders
    - GET /orders/{id}
dependencies:
  - fastapi>=0.100.0
  - pydantic>=2.0.0
  - sqlalchemy>=2.0.0
adapters:
  database: postgresql
  cache: redis
acceptance_checks:
  - All endpoints return proper status codes
  - Database migrations apply
```
````

### Event-Driven Service
````markdown
```code_factory:
project_type: microservice
package_name: notification_service
output_dir: generated/notification_service
interfaces:
  events:
    - user.registered
    - order.placed
  queues:
    - email_queue
    - sms_queue
dependencies:
  - pika>=1.3.0
  - jinja2>=3.0.0
adapters:
  message_broker: rabbitmq
```
````

## Support

For issues or questions:
- Check the [troubleshooting](#troubleshooting) section
- Review [examples](#examples)
- Consult the [field reference](#field-reference)
- Open an issue on the repository

---

**Version:** 1.0  
**Last Updated:** 2025-02-18
