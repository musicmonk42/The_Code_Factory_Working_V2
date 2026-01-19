# Port Configuration Guide

## Overview

This document describes the port allocation and configuration for the Code Factory Platform services.

## Port Allocation

| Service | Port | Description | Environment Variable |
|---------|------|-------------|---------------------|
| FastAPI Main API | 8000 | Main application API endpoint | `API_PORT` (default: 8000) |
| Application Metrics | 8001 | Application-level metrics endpoint | `METRICS_PORT` (default: 8001) |
| **Prometheus Metrics Server** | **9090** | Prometheus HTTP server for metrics | `PROMETHEUS_PORT` (default: 9090) |
| Prometheus Server | 9090 | Prometheus monitoring server | - |
| Grafana | 3000 | Grafana visualization dashboard | - |
| Redis | 6379 | Redis message bus and cache | - |
| PostgreSQL | 5432 | PostgreSQL database (optional) | - |

## Changes Made

### Problem
The application was experiencing a port conflict where both the Prometheus metrics HTTP server and FastAPI's Uvicorn server were attempting to bind to port 8000, causing the following error:

```
ERROR: [Errno 98] error while attempting to bind on address ('0.0.0.0', 8000): address already in use
```

### Solution
Changed the Prometheus metrics HTTP server default port from 8000 to 9090 to follow industry best practices and avoid conflicts.

## Configuration

### Environment Variables

Set the `PROMETHEUS_PORT` environment variable to customize the Prometheus metrics server port:

```bash
export PROMETHEUS_PORT=9090  # Default
```

Example configurations:

**Development (.env file):**
```env
PROMETHEUS_PORT=9090
METRICS_PORT=8001
```

**Docker Compose:**
```yaml
environment:
  - PROMETHEUS_PORT=9090
ports:
  - "9090:9090"  # Prometheus metrics HTTP server
```

### Files Modified

1. **omnicore_engine/metrics.py** - Changed default port from 8000 to 9090
2. **Dockerfile** - Added EXPOSE directive for port 9090
3. **docker-compose.yml** - Added port mapping for 9090
4. **monitoring/prometheus.yml** - Updated scrape targets to use port 9090
5. **.env.example** - Documented PROMETHEUS_PORT environment variable
6. **Makefile** - Updated docker-up target to display correct port information

## Industry Best Practices

### Why Port 9090?

1. **Standard Convention**: Port 9090 is the standard port used by Prometheus servers
2. **Avoids Conflicts**: Separates metrics collection from application API
3. **Clear Separation**: Makes it explicit that this is the metrics endpoint
4. **Security**: Allows for different firewall rules and access controls for metrics vs API

### Port Ranges

- **8000-8999**: Application services (APIs, web servers)
- **9000-9999**: Monitoring and observability (Prometheus, metrics exporters)
- **3000-3999**: Visualization tools (Grafana)
- **6000-6999**: Databases and caches (Redis, etc.)

## Accessing Metrics

### Prometheus Metrics Server
```bash
curl http://localhost:9090/metrics
```

### Application Metrics
```bash
curl http://localhost:8001/metrics
```

### Prometheus Server UI
```bash
# Web browser
http://localhost:9090
```

### Grafana Dashboard
```bash
# Web browser
http://localhost:3000
```

## Docker Deployment

### Building and Running

```bash
# Build the image
make docker-build

# Start all services
make docker-up

# Check logs
make docker-logs

# Stop services
make docker-down
```

### Accessing Services in Docker

When running in Docker, services are accessible at:

- **FastAPI**: http://localhost:8000
- **Application Metrics**: http://localhost:8001
- **Prometheus Metrics**: http://localhost:9090/metrics
- **Prometheus UI**: http://localhost:9090
- **Grafana**: http://localhost:3000

## Troubleshooting

### Port Already in Use

If you see "address already in use" errors:

1. Check what's using the port:
   ```bash
   lsof -i :9090
   ```

2. Kill the process or change the port:
   ```bash
   export PROMETHEUS_PORT=9091
   ```

3. Check Docker container port mappings:
   ```bash
   docker ps
   ```

### Metrics Not Available

1. Verify the Prometheus server started:
   ```bash
   docker logs codefactory-platform | grep "Prometheus"
   ```

2. Check if the port is exposed:
   ```bash
   docker inspect codefactory-platform | grep -A 10 "ExposedPorts"
   ```

3. Verify environment variable:
   ```bash
   docker exec codefactory-platform env | grep PROMETHEUS_PORT
   ```

## Security Considerations

### Production Deployment

1. **Firewall Rules**: Restrict access to metrics endpoints
   ```bash
   # Example: Only allow from monitoring server
   iptables -A INPUT -p tcp --dport 9090 -s monitoring.internal.com -j ACCEPT
   iptables -A INPUT -p tcp --dport 9090 -j DROP
   ```

2. **Authentication**: Add authentication for metrics endpoints in production
3. **TLS/SSL**: Use HTTPS for metrics collection in production
4. **Network Isolation**: Use Docker networks to isolate metrics from public access

### Example Production docker-compose.yml

```yaml
services:
  codefactory:
    ports:
      - "8000:8000"  # Public API
      # Note: Metrics ports not exposed to host in production
    networks:
      - public
      - monitoring

  prometheus:
    ports:
      - "9090:9090"
    networks:
      - monitoring  # Isolated network
    
networks:
  public:
  monitoring:
    internal: true  # Not accessible from outside
```

## References

- [Prometheus Best Practices](https://prometheus.io/docs/practices/)
- [FastAPI Deployment](https://fastapi.tiangolo.com/deployment/)
- [Docker Port Configuration](https://docs.docker.com/config/containers/container-networking/)
