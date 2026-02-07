<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# FastAPI HTTP API Integration Guide

## Overview

The new `server/` directory provides a comprehensive FastAPI-based HTTP API that acts as the central entry point for The Code Factory Platform. All operations are centrally coordinated through **OmniCore Engine**.

## What Was Added

### Directory Structure

```
server/
├── __init__.py              # Package initialization
├── main.py                  # FastAPI application (entry point)
├── run.py                   # Server startup script
├── README.md                # Detailed server documentation
├── routers/                 # API endpoint routers
│   ├── __init__.py
│   ├── jobs.py              # Job lifecycle management
│   ├── generator.py         # Generator module integration
│   ├── omnicore.py          # OmniCore engine operations
│   ├── sfe.py               # Self-Fixing Engineer integration
│   ├── fixes.py             # Fix management
│   └── events.py            # Real-time events (WebSocket/SSE)
├── schemas/                 # Pydantic request/response models
│   ├── __init__.py
│   ├── common.py            # Common schemas
│   ├── jobs.py              # Job-related models
│   ├── events.py            # Event models
│   └── fixes.py             # Fix-related models
└── services/                # Module integration services
    ├── __init__.py
    ├── generator_service.py  # Generator integration (via OmniCore)
    ├── omnicore_service.py   # OmniCore coordination
    └── sfe_service.py        # SFE integration (via OmniCore)
```

### Key Features Implemented

1. **Complete Job Lifecycle Management**
   - Create jobs
   - Upload files (README.md, etc.)
   - Monitor progress across all pipeline stages
   - Cancel running jobs
   - View job history

2. **Per-Stage Progress Dashboard**
   - Generator clarification stage
   - Generator generation stage
   - OmniCore processing stage
   - SFE analysis stage
   - SFE fixing stage
   - Real-time status updates

3. **Error and Fix Workflow**
   - Detect errors via SFE
   - Propose automated fixes
   - Review and approve fixes
   - Apply fixes with dry-run support
   - Rollback applied fixes
   - Track fix history

4. **Real-Time Event Streaming**
   - WebSocket streaming for live updates
   - Server-Sent Events (SSE) support
   - Job status changes
   - Error notifications
   - Fix proposals and applications
   - Platform health updates

5. **Centralized OmniCore Routing**
   - All module interactions route through OmniCore
   - Message bus integration
   - Centralized logging and metrics
   - Plugin management
   - Audit trail

6. **OpenAPI Documentation**
   - Interactive Swagger UI at `/api/docs`
   - ReDoc documentation at `/api/redoc`
   - Complete schema definitions
   - Example requests and responses

## Running the Server

### Quick Start (Development)

```bash
# From project root
python server/run.py --reload
```

### Production Deployment

```bash
python server/run.py --host 0.0.0.0 --port 8000 --workers 4
```

### Access Points

- API Root: http://localhost:8000/
- Swagger UI: http://localhost:8000/api/docs
- ReDoc: http://localhost:8000/api/redoc
- Health Check: http://localhost:8000/health

## Architecture

### Centralized Routing Through OmniCore

All module interactions are routed through OmniCore Engine for centralized coordination:

```
API Request → FastAPI Router → Service Layer → OmniCore → Module
                                                    ↓
                                              Message Bus
                                                    ↓
                                         (Generator/SFE/Plugins)
```

Example flow for job creation:

1. Client sends `POST /api/jobs/` with job details
2. Jobs router creates job entry
3. Client uploads files via `POST /api/generator/{job_id}/upload`
4. Generator service routes request through OmniCore
5. OmniCore publishes message to generator module via message bus
6. Generator processes files and updates job state
7. OmniCore broadcasts progress events
8. Clients receive real-time updates via WebSocket/SSE

### Service Layer Design

All services accept an `omnicore_service` parameter for centralized routing:

```python
class GeneratorService:
    def __init__(self, omnicore_service=None):
        self.omnicore_service = omnicore_service
    
    async def create_generation_job(self, job_id, files, metadata):
        # Route through OmniCore
        if self.omnicore_service:
            payload = {"action": "create_job", "job_id": job_id, ...}
            result = await self.omnicore_service.route_job(
                job_id=job_id,
                source_module="api",
                target_module="generator",
                payload=payload,
            )
            return result
        # Fallback for direct access
        return {...}
```

## API Usage Examples

### 1. Create Job and Upload Files

```bash
# Create a job
curl -X POST http://localhost:8000/api/jobs/ \
  -H "Content-Type: application/json" \
  -d '{"description": "My app", "metadata": {}}'

# Upload files
curl -X POST http://localhost:8000/api/generator/job-123/upload \
  -F "files=@README.md" \
  -F "files=@requirements.txt"
```

### 2. Monitor Progress

```bash
# Get job progress
curl http://localhost:8000/api/jobs/job-123/progress

# Get generator status
curl http://localhost:8000/api/generator/job-123/status

# Get SFE metrics
curl http://localhost:8000/api/sfe/job-123/metrics
```

### 3. Handle Errors and Fixes

```bash
# Get errors
curl http://localhost:8000/api/sfe/job-123/errors

# Propose fix
curl -X POST http://localhost:8000/api/sfe/errors/err-001/propose-fix

# Review fix
curl -X POST http://localhost:8000/api/sfe/fixes/fix-001/review \
  -H "Content-Type: application/json" \
  -d '{"approved": true}'

# Apply fix
curl -X POST http://localhost:8000/api/sfe/fixes/fix-001/apply \
  -H "Content-Type: application/json" \
  -d '{"dry_run": false}'
```

### 4. Real-Time Events

```javascript
// WebSocket
const ws = new WebSocket('ws://localhost:8000/api/events/ws');
ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    console.log('Event:', data);
};

// Server-Sent Events
const eventSource = new EventSource('http://localhost:8000/api/events/sse?job_id=job-123');
eventSource.onmessage = (event) => {
    const data = JSON.parse(event.data);
    console.log('Update:', data);
};
```

## Integration with Existing Platform

### Generator Module Integration

The API integrates with the generator module through OmniCore:

- **File uploads** → Stored locally, routed to generator via OmniCore
- **Job creation** → Generator runner invoked via OmniCore message bus
- **Status queries** → Routed through OmniCore for centralized tracking
- **Logs** → Retrieved from generator audit log via OmniCore

### OmniCore Integration

The API leverages OmniCore as the central coordinator:

- **Message bus** → All inter-module communication
- **Plugin registry** → Query plugin status and capabilities
- **Audit trail** → Tamper-evident logging
- **Metrics** → Performance and resource usage tracking
- **Workflows** → Trigger and monitor workflows

### Self-Fixing Engineer Integration

SFE capabilities are exposed through the API via OmniCore:

- **Code analysis** → Routed to SFE arbiter.codebase_analyzer
- **Error detection** → Routed to SFE arbiter.bug_manager
- **Fix proposals** → Generated by Arbiter AI via OmniCore
- **Fix application** → Applied through SFE with OmniCore coordination
- **Meta-learning** → Insights from meta_learning_orchestrator

## Extending the API

### Adding New Endpoints

1. Create a new router file in `server/routers/`:

```python
# server/routers/my_feature.py
from fastapi import APIRouter

router = APIRouter(prefix="/my-feature", tags=["My Feature"])

@router.get("/")
async def my_endpoint():
    return {"message": "Hello!"}
```

2. Define schemas in `server/schemas/`:

```python
# server/schemas/my_feature.py
from pydantic import BaseModel

class MyRequest(BaseModel):
    name: str
    value: int
```

3. Register the router in `server/main.py`:

```python
from server.routers import my_feature_router

app.include_router(my_feature_router, prefix="/api")
```

### Adding Service Methods

Add methods to existing services or create new ones:

```python
# server/services/my_service.py
class MyService:
    def __init__(self, omnicore_service=None):
        self.omnicore_service = omnicore_service
    
    async def my_operation(self, param):
        # Route through OmniCore
        if self.omnicore_service:
            result = await self.omnicore_service.route_job(...)
            return result
        return {"fallback": "data"}
```

## Testing

### Validation Script

```bash
# Validate server structure
python validate_server.py
```

### Manual Testing

```bash
# Start server
python server/run.py --reload

# In another terminal, test endpoints
curl http://localhost:8000/health
curl http://localhost:8000/api/jobs/
```

### Automated Testing

```bash
# Create tests in server/tests/
pytest server/tests/ -v
```

## Configuration

Environment variables for the server:

```bash
# Server
export API_HOST=0.0.0.0
export API_PORT=8000
export API_WORKERS=4

# OmniCore
export OMNICORE_MESSAGE_BUS_URL=redis://localhost:6379
export OMNICORE_DB_URL=postgresql://localhost/omnicore

# Storage
export UPLOAD_STORAGE_PATH=./uploads

# CORS
export API_CORS_ORIGINS=https://app.example.com,https://api.example.com
```

## Deployment Considerations

### Docker

The server can be containerized with the rest of the platform:

```dockerfile
# Already part of main Dockerfile
CMD ["python", "server/run.py", "--host", "0.0.0.0", "--port", "8000"]
```

### Kubernetes

Deploy as part of the unified platform deployment.

### Reverse Proxy

Configure nginx or similar:

```nginx
location /api/ {
    proxy_pass http://localhost:8000/api/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}

# WebSocket support
location /api/events/ws {
    proxy_pass http://localhost:8000/api/events/ws;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
}
```

## Documentation

- **Server README**: `server/README.md` - Comprehensive server documentation
- **API Docs**: http://localhost:8000/api/docs - Interactive Swagger UI
- **ReDoc**: http://localhost:8000/api/redoc - Alternative documentation
- **This Guide**: Integration overview and examples

## Notes

- **Placeholder Logic**: Service methods contain placeholder implementations with clear integration points
- **Extensible Design**: Modular structure allows easy addition of new endpoints and features
- **OmniCore Central**: All operations route through OmniCore for centralized coordination
- **Real-time Updates**: WebSocket and SSE support for live platform monitoring
- **Well-Documented**: Comprehensive docstrings and OpenAPI schemas

## Next Steps

1. **Integrate Real Modules**: Replace placeholder logic with actual module calls
2. **Add Authentication**: Implement JWT or API key authentication
3. **Add Database**: Replace in-memory storage with persistent database
4. **Add Tests**: Create comprehensive test suite
5. **Production Hardening**: Add rate limiting, input validation, security headers

## Support

For questions or issues:
- See `server/README.md` for detailed documentation
- Check API docs at `/api/docs` for endpoint details
- Review code comments for implementation guidance
