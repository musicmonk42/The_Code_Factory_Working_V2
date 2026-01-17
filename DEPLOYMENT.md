# Code Factory Platform - Deployment Guide

This guide covers deploying the Code Factory Platform to various environments including cloud providers, Kubernetes, and on-premises infrastructure.

## Table of Contents

- [Deployment Overview](#deployment-overview)
- [Pre-Deployment Checklist](#pre-deployment-checklist)
- [Environment Configuration](#environment-configuration)
- [Docker Deployment](#docker-deployment)
- [Kubernetes Deployment](#kubernetes-deployment)
- [Cloud Provider Deployments](#cloud-provider-deployments)
- [CI/CD Pipeline Setup](#cicd-pipeline-setup)
- [Monitoring and Observability](#monitoring-and-observability)
- [Security Hardening](#security-hardening)
- [Backup and Recovery](#backup-and-recovery)
- [Troubleshooting](#troubleshooting)

## Deployment Overview

The Code Factory Platform consists of three main components:

1. **Generator** - AI-powered code generation service
2. **OmniCore Engine** - Orchestration and coordination layer
3. **Self-Fixing Engineer** - Automated maintenance and healing

**Important**: The platform uses a **unified Docker image** that includes all three components. This simplifies deployment, ensures consistency, and reduces complexity. The same image can be configured to run different components by specifying different startup commands.

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Load Balancer / Ingress                   │
└───────────────────────┬─────────────────────────────────────┘
                        │
        ┌───────────────┼───────────────┐
        │               │               │
        ▼               ▼               ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│  Generator   │  │  OmniCore    │  │     SFE      │
│   Service    │  │   Engine     │  │   Service    │
└──────┬───────┘  └──────┬───────┘  └──────┬───────┘
       │                 │                 │
       └─────────────────┼─────────────────┘
                         │
        ┌────────────────┼────────────────┐
        ▼                ▼                ▼
   ┌────────┐      ┌─────────┐     ┌──────────┐
   │ Redis  │      │Database │     │ Storage  │
   └────────┘      └─────────┘     └──────────┘
```

## Pre-Deployment Checklist

### Required

- [ ] Python 3.11+ runtime environment
- [ ] Redis instance (for message bus)
- [ ] PostgreSQL database (optional, SQLite for dev)
- [ ] SSL/TLS certificates (for production)
- [ ] API keys for LLM providers
- [ ] Secrets management solution
- [ ] Monitoring and logging infrastructure

### Recommended

- [ ] Container orchestration (Kubernetes/ECS/AKS)
- [ ] Load balancer with health checks
- [ ] Auto-scaling configuration
- [ ] Backup strategy
- [ ] Disaster recovery plan
- [ ] CDN for static assets
- [ ] DDoS protection
- [ ] WAF (Web Application Firewall)

## Environment Configuration

### Production Environment Variables

Create a `.env.production` file:

```bash
# Application
APP_ENV=production
DEBUG=false

# API Keys (use secrets manager in production)
GROK_API_KEY=${GROK_API_KEY}
OPENAI_API_KEY=${OPENAI_API_KEY}

# Database
DATABASE_URL=postgresql://user:pass@db-host:5432/codefactory

# Redis
REDIS_URL=redis://redis-host:6379
REDIS_PASSWORD=${REDIS_PASSWORD}

# Security
SECRET_KEY=${SECRET_KEY}
JWT_SECRET_KEY=${JWT_SECRET_KEY}
CORS_ORIGINS=https://app.example.com

# Observability
OTEL_EXPORTER_OTLP_ENDPOINT=https://otel-collector:4317
LOG_LEVEL=INFO

# Performance
WORKER_COUNT=8
MAX_CONCURRENT_TASKS=50
```

### Secrets Management

**AWS Secrets Manager Example:**

```bash
# Store secrets
aws secretsmanager create-secret \
  --name codefactory/prod/api-keys \
  --secret-string '{"grok":"key","openai":"key"}'

# Retrieve in application
aws secretsmanager get-secret-value \
  --secret-id codefactory/prod/api-keys \
  --query SecretString --output text
```

**HashiCorp Vault Example:**

```bash
# Store secrets
vault kv put secret/codefactory/prod \
  grok_api_key="xxx" \
  openai_api_key="xxx"

# Retrieve in application
vault kv get -field=grok_api_key secret/codefactory/prod
```

## Docker Deployment

### Unified Platform Build

The Code Factory platform uses a **unified Docker image** that includes all three modules (Generator, OmniCore Engine, and Self-Fixing Engineer). This approach simplifies deployment and ensures consistency.

```bash
# Build the unified platform image
make docker-build

# Or build directly with Docker
docker build -t code-factory:latest -f Dockerfile .
```

### Production Docker Compose

Create `docker-compose.production.yml`:

```yaml
version: '3.8'

services:
  codefactory:
    image: ghcr.io/musicmonk42/code-factory:latest
    restart: always
    env_file: .env.production
    ports:
      - "8000:8000"  # Main API endpoint
      - "8001:8001"  # Metrics port
    depends_on:
      - redis
      - postgres
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
    command: python -m uvicorn omnicore_engine.fastapi_app:app --host 0.0.0.0 --port 8000
    deploy:
      resources:
        limits:
          cpus: '4'
          memory: 8G
        reservations:
          cpus: '2'
          memory: 4G

  redis:
    image: redis:7-alpine
    restart: always
    command: redis-server --requirepass ${REDIS_PASSWORD}
    volumes:
      - redis-data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  postgres:
    image: postgres:15-alpine
    restart: always
    environment:
      POSTGRES_DB: codefactory
      POSTGRES_USER: ${DB_USER}
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${DB_USER}"]
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  redis-data:
  postgres-data:
```

### Deploy with Docker

```bash
# Build the unified platform image
make docker-build

# Tag for registry
docker tag code-factory:latest ghcr.io/musicmonk42/code-factory:latest

# Push to registry
docker push ghcr.io/musicmonk42/code-factory:latest

# Deploy
docker-compose -f docker-compose.production.yml up -d

# Verify deployment
docker-compose -f docker-compose.production.yml ps
docker-compose -f docker-compose.production.yml logs -f codefactory
```

**Note**: The unified image includes Generator, OmniCore Engine, and Self-Fixing Engineer. The command specified in docker-compose determines which component starts.

## Kubernetes Deployment

### Prerequisites

- Kubernetes cluster (v1.24+)
- kubectl configured
- Helm 3+ (optional)

### Deploy with Kubernetes

Create deployment manifests:

**1. Namespace**

```yaml
# k8s/namespace.yml
apiVersion: v1
kind: Namespace
metadata:
  name: codefactory
```

**2. Secrets**

```yaml
# k8s/secrets.yml
apiVersion: v1
kind: Secret
metadata:
  name: codefactory-secrets
  namespace: codefactory
type: Opaque
stringData:
  grok-api-key: "your-key"
  openai-api-key: "your-key"
  secret-key: "your-secret"
  db-password: "your-password"
```

**3. ConfigMap**

```yaml
# k8s/configmap.yml
apiVersion: v1
kind: ConfigMap
metadata:
  name: codefactory-config
  namespace: codefactory
data:
  APP_ENV: "production"
  REDIS_URL: "redis://redis-service:6379"
  DATABASE_URL: "postgresql://postgres-service:5432/codefactory"
```

**4. Deployments**

```yaml
# k8s/generator-deployment.yml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: generator
  namespace: codefactory
spec:
  replicas: 3
  selector:
    matchLabels:
      app: generator
  template:
    metadata:
      labels:
        app: generator
    spec:
      containers:
      - name: generator
        image: ghcr.io/musicmonk42/codefactory-generator:latest
        ports:
        - containerPort: 8000
        envFrom:
        - configMapRef:
            name: codefactory-config
        - secretRef:
            name: codefactory-secrets
        resources:
          requests:
            memory: "2Gi"
            cpu: "1000m"
          limits:
            memory: "4Gi"
            cpu: "2000m"
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /ready
            port: 8000
          initialDelaySeconds: 10
          periodSeconds: 5
```

**5. Services**

```yaml
# k8s/services.yml
apiVersion: v1
kind: Service
metadata:
  name: generator-service
  namespace: codefactory
spec:
  selector:
    app: generator
  ports:
  - port: 8000
    targetPort: 8000
  type: ClusterIP
```

**6. Ingress**

```yaml
# k8s/ingress.yml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: codefactory-ingress
  namespace: codefactory
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
spec:
  ingressClassName: nginx
  tls:
  - hosts:
    - api.codefactory.example.com
    secretName: codefactory-tls
  rules:
  - host: api.codefactory.example.com
    http:
      paths:
      - path: /generator
        pathType: Prefix
        backend:
          service:
            name: generator-service
            port:
              number: 8000
      - path: /omnicore
        pathType: Prefix
        backend:
          service:
            name: omnicore-service
            port:
              number: 8001
```

### Deploy to Kubernetes

```bash
# Apply manifests
kubectl apply -f k8s/namespace.yml
kubectl apply -f k8s/secrets.yml
kubectl apply -f k8s/configmap.yml
kubectl apply -f k8s/

# Check deployment
kubectl get pods -n codefactory
kubectl get services -n codefactory
kubectl get ingress -n codefactory

# View logs
kubectl logs -f deployment/generator -n codefactory
```

## Cloud Provider Deployments

### AWS (ECS + Fargate)

```bash
# Create ECS cluster
aws ecs create-cluster --cluster-name codefactory-cluster

# Register task definition
aws ecs register-task-definition --cli-input-json file://ecs-task-def.json

# Create service
aws ecs create-service \
  --cluster codefactory-cluster \
  --service-name generator-service \
  --task-definition generator:1 \
  --desired-count 3 \
  --launch-type FARGATE
```

### Google Cloud (GKE)

```bash
# Create GKE cluster
gcloud container clusters create codefactory-cluster \
  --num-nodes=3 \
  --machine-type=n1-standard-4 \
  --zone=us-central1-a

# Get credentials
gcloud container clusters get-credentials codefactory-cluster

# Deploy
kubectl apply -f k8s/
```

### Azure (AKS)

```bash
# Create AKS cluster
az aks create \
  --resource-group codefactory-rg \
  --name codefactory-cluster \
  --node-count 3 \
  --node-vm-size Standard_D4s_v3 \
  --enable-addons monitoring

# Get credentials
az aks get-credentials --resource-group codefactory-rg --name codefactory-cluster

# Deploy
kubectl apply -f k8s/
```

## CI/CD Pipeline Setup

The Code Factory Platform includes GitHub Actions workflows for CI/CD.

### GitHub Actions Secrets

Configure these secrets in your GitHub repository:

- `GROK_API_KEY` - xAI Grok API key
- `OPENAI_API_KEY` - OpenAI API key
- `REGISTRY_TOKEN` - Container registry token
- `KUBE_CONFIG` - Kubernetes config (base64 encoded)
- `AWS_ACCESS_KEY_ID` - AWS credentials (if using AWS)
- `AWS_SECRET_ACCESS_KEY` - AWS credentials (if using AWS)

### Workflows

The platform includes these workflows:

- `.github/workflows/ci.yml` - Continuous Integration
- `.github/workflows/cd.yml` - Continuous Deployment
- `.github/workflows/security.yml` - Security Scanning
- `.github/workflows/dependency-updates.yml` - Dependency Management

## Monitoring and Observability

### Prometheus + Grafana

```bash
# Deploy Prometheus
kubectl apply -f monitoring/prometheus/

# Deploy Grafana
kubectl apply -f monitoring/grafana/

# Access Grafana
kubectl port-forward -n monitoring svc/grafana 3000:3000
```

### OpenTelemetry

Configure OTLP exporters in `.env`:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=https://otel-collector:4317
OTEL_SERVICE_NAME=code-factory
OTEL_TRACES_EXPORTER=otlp
```

### Application Logs

Centralized logging with ELK/Loki:

```bash
# Filebeat for Elasticsearch
kubectl apply -f monitoring/filebeat/

# Promtail for Loki
kubectl apply -f monitoring/promtail/
```

## Security Hardening

### Network Security

1. **Use private subnets** for databases and internal services
2. **Enable network policies** in Kubernetes
3. **Configure security groups** with minimal access
4. **Use VPN/Bastion** for administrative access

### Application Security

1. **Enable HTTPS** with valid certificates
2. **Implement rate limiting**
3. **Use secrets management** (never commit secrets)
4. **Enable CORS** with specific origins only
5. **Implement authentication** (JWT/OAuth)
6. **Regular security scans** (automated in CI/CD)

### Container Security

```bash
# Scan images for vulnerabilities
trivy image ghcr.io/musicmonk42/codefactory:latest

# Use non-root user in Dockerfile
USER appuser

# Read-only root filesystem
securityContext:
  readOnlyRootFilesystem: true
```

## Backup and Recovery

### Database Backup

```bash
# PostgreSQL backup
kubectl exec -n codefactory postgres-0 -- \
  pg_dump -U codefactory codefactory > backup.sql

# Automated backups with CronJob
kubectl apply -f k8s/backup-cronjob.yml
```

### Disaster Recovery

1. **Regular backups** (daily minimum)
2. **Off-site storage** (S3, GCS, Azure Blob)
3. **Tested recovery procedures**
4. **Multi-region deployment** for critical systems
5. **Database replication** for high availability

## Troubleshooting

### Common Issues

**Pods Crashing:**
```bash
kubectl logs -n codefactory <pod-name>
kubectl describe pod -n codefactory <pod-name>
```

**Service Unreachable:**
```bash
kubectl get svc -n codefactory
kubectl describe svc -n codefactory <service-name>
```

**High Memory Usage:**
```bash
kubectl top pods -n codefactory
# Adjust resource limits in deployment
```

### Health Checks

```bash
# Check service health
curl https://api.codefactory.example.com/health

# Check all components
make health-check
```

### Rollback

```bash
# Kubernetes
kubectl rollout undo deployment/generator -n codefactory

# Docker Compose
docker-compose -f docker-compose.production.yml down
docker-compose -f docker-compose.production.yml up -d --force-recreate
```

---

For more information, see:
- [QUICKSTART.md](./QUICKSTART.md) - Quick start guide
- [README.md](./README.md) - Main documentation
- [SECURITY_DEPLOYMENT_GUIDE.md](./SECURITY_DEPLOYMENT_GUIDE.md) - Security details
