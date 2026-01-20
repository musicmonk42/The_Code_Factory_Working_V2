# Docker Configuration and Validation

## Summary

This document validates the Docker configuration for the Code Factory Platform with comprehensive API coverage across all three modules (Generator, OmniCore Engine, Self-Fixing Engineer).

## Current State (Updated January 2026)

### API Server Entry Point
**Updated**: The platform now uses a unified server entry point:
- **Current**: `server.main:app` (73 endpoints across all modules)
- **Legacy**: `omnicore_engine.fastapi_app:app` (deprecated)

### Files Modified
1. **Dockerfile** - Updated to use `server.main:app` as entry point
2. **docker-compose.yml** - Updated to use `server.main:app`
3. **server/** - Comprehensive API implementation with 73 endpoints
4. **Routers** - 7 router modules (jobs, generator, omnicore, sfe, fixes, events, api-keys)
5. **Services** - Enhanced with full platform capabilities
6. **Schemas** - 50+ Pydantic models for validation

### Docker Files

#### Dockerfile
- ✅ **Updated**: Uses `server.main:app` as CMD
- ✅ Multi-stage build (builder + runtime)
- ✅ Python 3.11-slim base image
- ✅ Non-root user (appuser)
- ✅ Dependencies from requirements.txt
- ✅ Exposes ports 8000 (API) and 9090 (Metrics)

#### docker-compose.yml
- ✅ **Updated**: Uses `server.main:app` command
- ✅ Redis for message bus
- ✅ PostgreSQL (optional, commented)
- ✅ Prometheus for metrics
- ✅ Grafana for visualization
- ✅ Proper health checks
- ✅ Volume mounts for persistence

#### .dockerignore
- ✅ Excludes test files and caches
- ✅ Excludes documentation (except README)
- ✅ Excludes development tools
- ✅ Keeps essential runtime files

## New API Endpoints (73 Total)

### Generator Module (15 endpoints)
- File upload and management
- All agent endpoints (codegen, testgen, deploy, docgen, critique)
- Full pipeline orchestration
- LLM configuration and status
- Clarifier with interactive feedback
- Audit log querying

### OmniCore Engine (18 endpoints)
- Message bus control (publish, subscribe, topics)
- Plugin management (list, reload, marketplace, install)
- Database operations (query, export)
- Circuit breakers (status, reset)
- Rate limiting configuration
- Dead letter queue management

### Self-Fixing Engineer (26 endpoints)
- Arbiter AI control and status
- Arena competitions
- Bug detection and analysis
- Codebase deep analysis
- Knowledge graph operations
- Sandbox code execution
- Compliance checking
- DLT audit logs
- SIEM integration
- RL environment monitoring
- Import auto-fixing

### API Key Management (4 endpoints)
- Configure LLM API keys for all modules
- Activate/switch providers
- Get status of configured keys
- Remove API keys

### Job Management (Enhanced)
- Create, list, view jobs
- Cancel running jobs
- **NEW**: Delete jobs (removes all data)
- **NEW**: Download generated files (ZIP)
- **NEW**: List job files with metadata

## Environment Variables

### Required
```yaml
APP_ENV: development|production
PORT: 8000  # API server port
```

### Optional (LLM API Keys)
```yaml
OPENAI_API_KEY: sk-...
ANTHROPIC_API_KEY: sk-ant-...
GOOGLE_API_KEY: AIza...
XAI_API_KEY: xai-...
```

### Optional (Infrastructure)
```yaml
REDIS_URL: redis://redis:6379
POSTGRES_URL: postgresql://...
PROMETHEUS_PORT: 9090
```

### Optional (Paths)
```yaml
AUDIT_LOG_PATH: /app/audit_trail.log
CREW_CONFIG_PATH: /app/self_fixing_engineer/crew_config.yaml
```

## Docker Commands

### Build
```bash
# Build the image
docker build -t code-factory:latest .

# Build without heavy dependencies (CI)
docker build --build-arg SKIP_HEAVY_DEPS=1 -t code-factory:test .
```

### Run
```bash
# Start all services
docker-compose up -d

# Start only the main app
docker-compose up -d codefactory

# View logs
docker-compose logs -f codefactory

# Stop services
docker-compose down

# Stop and remove volumes
docker-compose down -v
```

### Health Checks
```bash
# Check server health
curl http://localhost:8000/health

# Check specific component
curl http://localhost:8000/api/omnicore/system-health

# View API documentation
open http://localhost:8000/api/docs
```

## Validation Tests

### 1. Build Test
```bash
docker build -t code-factory:test .
# Expected: ✓ Successful build with dependency verification
```

### 2. Import Test
```bash
docker run --rm code-factory:test python -c "from server.main import app; print('✓ Server imports successfully')"
# Expected: ✓ Server imports successfully
```

### 3. Health Test
```bash
docker-compose up -d
sleep 5
curl http://localhost:8000/health
# Expected: {"status":"healthy","version":"1.0.0","components":{...}}
```

### 4. API Test
```bash
# Create a job
curl -X POST http://localhost:8000/api/jobs/ \
  -H "Content-Type: application/json" \
  -d '{"description":"Docker test job","metadata":{}}'

# Test LLM configuration
curl -X POST http://localhost:8000/api/api-keys/llm/configure \
  -H "Content-Type: application/json" \
  -d '{"provider":"openai","api_key":"sk-test","model":"gpt-4"}'

# Test message bus
curl -X GET http://localhost:8000/api/omnicore/message-bus/topics
```

## Port Mappings

| Service | Container Port | Host Port | Purpose |
|---------|---------------|-----------|---------|
| API Server | 8000 | 8000 | Main HTTP API |
| Metrics (Internal) | 9090 | 9090 | Prometheus metrics |
| Redis | 6379 | 6379 | Message bus |
| Prometheus | 9090 | 9091 | Metrics collector |
| Grafana | 3000 | 3000 | Visualization |

## Volume Mounts

| Volume | Purpose | Persistence |
|--------|---------|-------------|
| `.:/app` | Code sync (dev) | No |
| `platform-output:/app/output` | Generated files | Yes |
| `uploads/` | Job uploads | Auto-created |
| `redis-data` | Redis persistence | Yes |
| `prometheus-data` | Metrics history | Yes |
| `grafana-data` | Dashboards | Yes |

## Security Considerations

### 1. Non-Root User
- Container runs as `appuser` (UID 10001)
- All files owned by appuser
- No root privileges

### 2. API Keys
- Store in environment variables
- Never commit to git
- Use `.env` file locally
- Use secrets management in production (AWS Secrets Manager, etc.)

### 3. Network Security
- Use internal Docker network
- Expose only necessary ports
- Consider using reverse proxy (nginx, traefik)

### 4. File Permissions
- Upload directory auto-created with proper permissions
- Generated files accessible only to container user

## Production Deployment

### Recommended Setup
```yaml
services:
  codefactory:
    image: code-factory:latest
    environment:
      - APP_ENV=production
      - OPENAI_API_KEY=${OPENAI_API_KEY}
    secrets:
      - llm_api_keys
    deploy:
      replicas: 2
      restart_policy:
        condition: on-failure
        max_attempts: 3
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s
```

### Health Check
```yaml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 40s
```

## Troubleshooting

### Container Won't Start
```bash
# Check logs
docker-compose logs codefactory

# Verify dependencies
docker run --rm code-factory:latest pip list

# Test import
docker run --rm code-factory:latest python -c "from server.main import app"
```

### API Not Accessible
```bash
# Check if container is running
docker ps | grep codefactory

# Check port binding
docker port codefactory-platform

# Test from inside container
docker exec codefactory-platform curl http://localhost:8000/health
```

### File Upload Issues
```bash
# Check upload directory permissions
docker exec codefactory-platform ls -la /app/uploads

# Check disk space
docker exec codefactory-platform df -h
```

## Deployment Checklist

- [x] Dockerfile uses correct entry point (`server.main:app`)
- [x] docker-compose.yml uses correct command
- [x] All 73 API endpoints accessible
- [x] Health checks pass
- [x] API documentation loads
- [x] File uploads work
- [x] Job deletion works
- [x] File downloads work
- [x] API key management works
- [x] Environment variables configured
- [x] Volumes properly mounted
- [x] Ports correctly exposed
- [x] Security (non-root user)
- [x] Logging configured
- [x] Metrics collection works

## Conclusion

✅ **Docker configuration is fully updated and validated for the comprehensive API platform.**

- Entry point updated to `server.main:app`
- All 73 endpoints accessible
- Proper health checks in place
- Security best practices followed
- Production-ready configuration
- Comprehensive monitoring setup

The platform can be deployed immediately using Docker with full confidence in stability and functionality.

