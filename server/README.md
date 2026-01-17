# Code Factory Platform API Server

 HTTP API for The Code Factory Platform, providing centralized access to all platform capabilities through OmniCore Engine.

## Overview

The server package provides a comprehensive FastAPI-based HTTP API that acts as the central entry point for the Code Factory Platform. All operations are coordinated through **OmniCore Engine**, which manages inter-module communication, job routing, and workflow orchestration.

### Key Features

- **File Uploads**: Accept `.md` and other files for generator jobs
- **Job Lifecycle Management**: Create, list, view, retrieve status and progress
- **Per-Stage Progress Dashboard**: Real-time tracking across all pipeline stages
- **Error & Fix Workflow**: Detect errors, propose fixes, review, apply, or rollback
- **Real-Time Events**: WebSocket and SSE streaming for live platform updates
- **Modular Routing**: Extensible architecture for each subsystem
- **OpenAPI Documentation**: Interactive API documentation
- **Centralized Coordination**: All operations routed through OmniCore

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    FastAPI HTTP API Server                   │
│                      (server/main.py)                        │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                    OmniCore Engine                           │
│           (Central Coordinator & Message Bus)                │
└─────────┬───────────────────┬───────────────────┬───────────┘
          │                   │                   │
          ▼                   ▼                   ▼
    ┌──────────┐      ┌──────────────┐    ┌──────────────────┐
    │Generator │      │  OmniCore    │    │Self-Fixing       │
    │  Module  │      │  Operations  │    │Engineer (SFE)    │
    └──────────┘      └──────────────┘    └──────────────────┘
```

## Quick Start

### Installation

The server is part of the unified Code Factory platform. Install dependencies:

```bash
# From project root
pip install -r requirements.txt
```

### Running the Server

#### Development Mode (with auto-reload)

```bash
python server/run.py --reload
```

#### Production Mode

```bash
python server/run.py --host 0.0.0.0 --port 8000 --workers 4
```

#### Using Uvicorn Directly

```bash
uvicorn server.main:app --host 0.0.0.0 --port 8000
```

### Accessing the API

- **API Root**: http://localhost:8000/
- **Interactive Docs**: http://localhost:8000/api/docs
- **ReDoc**: http://localhost:8000/api/redoc
- **OpenAPI Schema**: http://localhost:8000/api/openapi.json
- **Health Check**: http://localhost:8000/health

## API Endpoints

### Job Management (`/api/jobs`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/jobs/` | POST | Create a new job |
| `/api/jobs/` | GET | List all jobs with pagination |
| `/api/jobs/{job_id}` | GET | Get job details |
| `/api/jobs/{job_id}/progress` | GET | Get detailed progress |
| `/api/jobs/{job_id}` | DELETE | Cancel a job |

### Generator Module (`/api/generator`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/generator/{job_id}/upload` | POST | Upload files for job |
| `/api/generator/{job_id}/status` | GET | Get generator status |
| `/api/generator/{job_id}/logs` | GET | Get generator logs |

### OmniCore Engine (`/api/omnicore`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/omnicore/plugins` | GET | Get plugin status |
| `/api/omnicore/{job_id}/metrics` | GET | Get job metrics |
| `/api/omnicore/{job_id}/audit` | GET | Get audit trail |
| `/api/omnicore/health` | GET | Get system health |
| `/api/omnicore/{job_id}/workflow/{name}` | POST | Trigger workflow |

### Self-Fixing Engineer (`/api/sfe`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/sfe/{job_id}/analyze` | POST | Analyze code |
| `/api/sfe/{job_id}/errors` | GET | Get detected errors |
| `/api/sfe/errors/{error_id}/propose-fix` | POST | Propose a fix |
| `/api/sfe/fixes/{fix_id}` | GET | Get fix details |
| `/api/sfe/fixes/{fix_id}/review` | POST | Review a fix |
| `/api/sfe/fixes/{fix_id}/apply` | POST | Apply a fix |
| `/api/sfe/fixes/{fix_id}/rollback` | POST | Rollback a fix |
| `/api/sfe/{job_id}/metrics` | GET | Get SFE metrics |
| `/api/sfe/insights` | GET | Get learning insights |

### Fix Management (`/api/fixes`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/fixes/` | GET | List all fixes |
| `/api/fixes/{fix_id}` | GET | Get fix details |

### Real-Time Events (`/api/events`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/events/ws` | WebSocket | Real-time event stream |
| `/api/events/sse` | GET | Server-Sent Events stream |

## Usage Examples

### 1. Create a Job and Upload Files

```bash
# Create a job
curl -X POST http://localhost:8000/api/jobs/ \
  -H "Content-Type: application/json" \
  -d '{"description": "My project", "metadata": {}}'

# Response: {"id": "job-123", ...}

# Upload README file
curl -X POST http://localhost:8000/api/generator/job-123/upload \
  -F "files=@README.md"
```

### 2. Monitor Job Progress

```bash
# Get overall progress
curl http://localhost:8000/api/jobs/job-123/progress

# Get generator-specific status
curl http://localhost:8000/api/generator/job-123/status

# Get SFE metrics
curl http://localhost:8000/api/sfe/job-123/metrics
```

### 3. Handle Errors and Fixes

```bash
# Get errors for a job
curl http://localhost:8000/api/sfe/job-123/errors

# Propose a fix for an error
curl -X POST http://localhost:8000/api/sfe/errors/err-001/propose-fix

# Review and approve fix
curl -X POST http://localhost:8000/api/sfe/fixes/fix-001/review \
  -H "Content-Type: application/json" \
  -d '{"approved": true, "comments": "Looks good"}'

# Apply the fix
curl -X POST http://localhost:8000/api/sfe/fixes/fix-001/apply \
  -H "Content-Type: application/json" \
  -d '{"force": false, "dry_run": false}'
```

### 4. Real-Time Event Streaming

#### WebSocket (JavaScript)

```javascript
const ws = new WebSocket('ws://localhost:8000/api/events/ws');

ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    console.log('Event:', data.event_type, data.message);
};
```

#### Server-Sent Events (JavaScript)

```javascript
const eventSource = new EventSource('http://localhost:8000/api/events/sse?job_id=job-123');

eventSource.addEventListener('job_updated', (event) => {
    const data = JSON.parse(event.data);
    console.log('Job update:', data);
});
```

## Project Structure

```
server/
├── __init__.py           # Package initialization
├── main.py              # FastAPI application
├── run.py               # Startup script
├── README.md            # This file
├── routers/             # API endpoint routers
│   ├── __init__.py
│   ├── jobs.py          # Job management endpoints
│   ├── generator.py     # Generator module endpoints
│   ├── omnicore.py      # OmniCore engine endpoints
│   ├── sfe.py           # SFE module endpoints
│   ├── fixes.py         # Fix management endpoints
│   └── events.py        # Real-time event streaming
├── schemas/             # Pydantic models
│   ├── __init__.py
│   ├── common.py        # Common schemas
│   ├── jobs.py          # Job-related schemas
│   ├── events.py        # Event schemas
│   └── fixes.py         # Fix-related schemas
├── services/            # Module integration services
│   ├── __init__.py
│   ├── generator_service.py    # Generator integration
│   ├── omnicore_service.py     # OmniCore integration
│   └── sfe_service.py          # SFE integration
└── middleware/          # Custom middleware (future)
```

## Development

### Adding New Endpoints

1. Create or update a router in `server/routers/`
2. Define request/response schemas in `server/schemas/`
3. Implement service methods in `server/services/`
4. Register the router in `server/main.py`

Example:

```python
# server/routers/my_module.py
from fastapi import APIRouter

router = APIRouter(prefix="/my-module", tags=["My Module"])

@router.get("/")
async def my_endpoint():
    return {"message": "Hello!"}

# server/main.py
from server.routers import my_module_router
app.include_router(my_module_router, prefix="/api")
```

### Testing

```bash
# Run tests
pytest server/tests/

# Run with coverage
pytest server/tests/ --cov=server --cov-report=html
```

### Code Quality

```bash
# Format code
black server/

# Lint code
ruff check server/

# Type checking
mypy server/
```

## Integration with Modules

All module interactions are centralized through **OmniCore Engine**:

### Generator Integration

```python
# Services route through OmniCore's message bus
from server.services import GeneratorService, OmniCoreService

omnicore = OmniCoreService()
generator = GeneratorService(omnicore_service=omnicore)

# All generator operations are routed through OmniCore
await generator.create_generation_job(job_id, files, metadata)
```

### Self-Fixing Engineer Integration

```python
# SFE operations also route through OmniCore
from server.services import SFEService, OmniCoreService

omnicore = OmniCoreService()
sfe = SFEService(omnicore_service=omnicore)

# All SFE operations are coordinated through OmniCore
await sfe.analyze_code(job_id, code_path)
```

## Configuration

Configuration can be set via environment variables:

```bash
# Server configuration
export API_HOST=0.0.0.0
export API_PORT=8000
export API_WORKERS=4

# OmniCore configuration
export OMNICORE_MESSAGE_BUS_URL=redis://localhost:6379
export OMNICORE_DB_URL=postgresql://localhost/omnicore

# Storage configuration
export UPLOAD_STORAGE_PATH=./uploads
```

## Deployment

### Docker

```dockerfile
# Dockerfile
FROM python:3.10-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

CMD ["python", "server/run.py", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

### Docker Compose

```yaml
version: '3.8'

services:
  api:
    build: .
    ports:
      - "8000:8000"
    environment:
      - API_HOST=0.0.0.0
      - API_PORT=8000
      - OMNICORE_MESSAGE_BUS_URL=redis://redis:6379
    depends_on:
      - redis
      - postgres

  redis:
    image: redis:7-alpine

  postgres:
    image: postgres:15-alpine
```

### Kubernetes

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: code-factory-api
spec:
  replicas: 3
  selector:
    matchLabels:
      app: code-factory-api
  template:
    metadata:
      labels:
        app: code-factory-api
    spec:
      containers:
      - name: api
        image: code-factory-api:latest
        ports:
        - containerPort: 8000
        env:
        - name: API_WORKERS
          value: "1"
```

## Monitoring

### Metrics

The API exposes Prometheus metrics (when enabled):

```bash
curl http://localhost:8000/metrics
```

### Logging

Structured logging to stdout in JSON format:

```json
{
  "timestamp": "2026-01-15T04:15:00Z",
  "level": "INFO",
  "logger": "server.routers.jobs",
  "message": "Created job job-123",
  "job_id": "job-123"
}
```

## Troubleshooting

### Common Issues

**Port already in use:**
```bash
# Change port
python server/run.py --port 8001
```

**Module import errors:**
```bash
# Ensure you're in the project root
cd /path/to/The_Code_Factory_Working_V2
python server/run.py
```

**Dependencies missing:**
```bash
# Reinstall dependencies
pip install -r requirements.txt
```

## Security

### Authentication

(To be implemented) The API will support:
- JWT token authentication
- API key authentication
- OAuth2 integration

### CORS

Configure CORS in production:

```python
# server/main.py
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://yourdomain.com"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)
```

### Rate Limiting

(To be implemented) Rate limiting per endpoint and per user.

## License

Proprietary - © 2025 Novatrax Labs LLC

## Support

For issues and questions:
- GitHub Issues: (enterprise repo)
- Email: support@novatraxlabs.com
