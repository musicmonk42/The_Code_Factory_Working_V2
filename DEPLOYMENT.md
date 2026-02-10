<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# Code Factory Platform - Deployment Guide

**Complete deployment documentation for Docker, Kubernetes, Helm, and production environments.**

Version: 1.0.0  
Last Updated: 2026-02-06

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Docker Deployment](#docker-deployment)
3. [Docker Compose](#docker-compose)
4. [Kubernetes Deployment](#kubernetes-deployment)
5. [Helm Deployment](#helm-deployment)
6. [Makefile Commands](#makefile-commands)
7. [Production Deployment](#production-deployment)
8. [CI/CD Integration](#cicd-integration)
9. [Troubleshooting](#troubleshooting)
10. [Appendices](#appendices)

---

## Quick Start

### Prerequisites

- Docker 20.10+ and Docker Compose 2.0+
- Kubernetes 1.25+ (for K8s deployment)
- Helm 3.10+ (for Helm deployment)
- Python 3.11+ (for local development)
- Git
- Make

### Local Development (5 Minutes)

```bash
# 1. Clone repository
git clone https://github.com/musicmonk42/The_Code_Factory_Working_V2.git
cd The_Code_Factory_Working_V2

# 2. Copy environment file
cp .env.example .env
# Edit .env with your API keys

# 3. Start services
make docker-up

# 4. Verify health
curl http://localhost:8000/health
curl http://localhost:8000/docs  # API documentation
```

Services will be available at:
- **API**: http://localhost:8000
- **Metrics**: http://localhost:9090/metrics
- **Grafana**: http://localhost:3000
- **Prometheus**: http://localhost:9091

---

## Docker Deployment

### Dockerfile Architecture

The Code Factory uses a production-grade **multi-stage Docker build**:

**Stage 1: Builder**
- Install dependencies
- Pre-download SpaCy models and NLTK data
- Verify critical dependencies

**Stage 2: Runtime**
- Minimal production image
- Non-root user (UID 10001)
- Security hardened
- Health checks included

**Key Features:**
- ✅ Multi-stage build for minimal image size
- ✅ Non-root user execution
- ✅ Security scanning compatible (Trivy, Snyk, Clair)
- ✅ CIS Docker Benchmark compliant
- ✅ Health checks implemented
- ✅ Dependency verification at build time

### Build Commands

```bash
# Standard build
docker build -t code-factory:latest .

# Development build
docker build -t code-factory:dev --target builder .

# CI build (skip heavy dependencies)
docker build --build-arg SKIP_HEAVY_DEPS=1 -t code-factory:ci .

# Using Makefile
make docker-build
```

### Environment Variables

**Required:**
```bash
AGENTIC_AUDIT_HMAC_KEY=<64-char-hex>
ENCRYPTION_KEY=<base64-fernet-key>
OPENAI_API_KEY=sk-...  # Or other LLM provider
```

**Infrastructure:**
```bash
REDIS_URL=redis://:password@redis:6379
DB_PATH=postgresql+asyncpg://user:pass@host:5432/db
```

**Feature Flags:**
```bash
ENABLE_DATABASE=1
ENABLE_HSM=auto
ENABLE_FEATURE_STORE=auto
ENABLE_LIBVIRT=auto
```

**Performance:**
```bash
PARALLEL_AGENT_LOADING=1
LAZY_LOAD_ML=1
WORKER_COUNT=4
```

See [Appendix A](#appendix-a-environment-variables) for complete list.

### Security Features

**Non-Root User:**
```dockerfile
USER appuser  # UID 10001, GID 10001
```

**Security Scanning:**
```bash
# Trivy
trivy image code-factory:latest

# Docker scan
docker scan code-factory:latest

# Snyk
snyk container test code-factory:latest
```

### Health Checks

**Container Healthcheck:**
```dockerfile
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3     CMD curl -f http://localhost:${PORT:-8080}/health || exit 1
```

**Manual Verification:**
```bash
docker run -d --name test code-factory:latest
sleep 60
curl http://localhost:8000/health
docker stop test && docker rm test
```

---

## Docker Compose

### Service Stack

```yaml
services:
  redis:        # Cache and message bus
  postgres:     # Database
  codefactory:  # Main application
  prometheus:   # Metrics collection
  grafana:      # Visualization
```

### Starting Services

```bash
# Start all services
make docker-up
# Or
docker compose up -d

# Check status
docker compose ps

# View logs
make docker-logs
# Or
docker compose logs -f
```

### Compose Variants

**Base Configuration:**
```bash
docker compose up -d
```

**Development:**
```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

**Production:**
```bash
docker compose -f docker-compose.yml -f docker-compose.production.yml up -d
```

**With Kafka:**
```bash
docker compose -f docker-compose.yml -f docker-compose.kafka.yml up -d
```

### Volume Management

**Persistent Volumes:**
- `redis-data` - Redis persistence
- `postgres-data` - Database files
- `platform-output` - Generated code
- `platform-uploads` - Uploaded files
- `prometheus-data` - Metrics history
- `grafana-data` - Dashboard configs

**Backup:**
```bash
# Backup volume
docker run --rm -v postgres-data:/data -v $(pwd):/backup   alpine tar czf /backup/postgres-backup.tar.gz /data

# Restore volume
docker run --rm -v postgres-data:/data -v $(pwd):/backup   alpine tar xzf /backup/postgres-backup.tar.gz -C /
```

### Common Operations

```bash
# Restart service
docker compose restart codefactory

# Scale services
docker compose up -d --scale codefactory=3

# Update services
docker compose pull
docker compose up -d

# Clean up
make docker-clean
# Or
docker compose down -v
```

### Database and Migrations

#### PostgreSQL with Citus Support

The platform now uses `citusdata/citus:12.1` image for PostgreSQL, providing:
- Standard PostgreSQL functionality
- Optional Citus extension for distributed SQL
- Production-ready scale-out capabilities

**Enable Citus features:**
```bash
# In docker-compose.yml or .env
ENABLE_CITUS=1
```

#### Running Migrations

**Automatic (recommended):**
Migrations run automatically when the application starts if the `omnicore_engine/migrations` directory exists.

**Manual migration commands:**
```bash
# Using Make
make db-migrate              # Run all pending migrations
make db-migrate-create       # Create new migration
make db-migrate-history      # View history
make db-migrate-current      # Check current version

# Or directly with Alembic
docker compose exec codefactory alembic upgrade head
docker compose exec codefactory alembic current
docker compose exec codefactory alembic history
```

**Initial setup:**
```bash
# First time setup - migrations run automatically
docker compose up -d

# Or manually trigger
docker compose exec codefactory alembic upgrade head
```

#### Database Backup and Restore

**PostgreSQL backup:**
```bash
# Backup
docker compose exec postgres pg_dump -U codefactory codefactory > backup.sql

# Restore
docker compose exec -i postgres psql -U codefactory codefactory < backup.sql

# Volume backup
docker run --rm -v postgres-data:/data -v $(pwd):/backup \
  alpine tar czf /backup/postgres-backup.tar.gz /data
```

**Migration rollback (use with caution):**
```bash
# Rollback one migration
docker compose exec codefactory alembic downgrade -1

# Or using Make
make db-migrate-downgrade
```

For detailed migration documentation:
- [Alembic Migrations README](./omnicore_engine/migrations/README.md)
- [DIAGNOSTIC_ISSUES_FIX.md](./DIAGNOSTIC_ISSUES_FIX.md)

---

## Kubernetes Deployment

### Architecture

The Kubernetes deployment uses **Kustomize** for environment-specific configurations:

```
k8s/
├── base/              # Base manifests
│   ├── api-deployment.yaml
│   ├── redis-deployment.yaml
│   ├── migration-job.yaml  # Database migrations
│   ├── service.yaml
│   ├── ingress.yaml
│   ├── configmap.yaml
│   ├── secret.yaml
│   ├── rbac.yaml
│   └── networkpolicy.yaml
└── overlays/          # Environment-specific
    ├── development/
    ├── staging/
    └── production/
```

### Database Migrations

**Pre-deployment migrations (recommended for production):**
```bash
# Step 1: Run migrations
kubectl apply -f k8s/base/migration-job.yaml

# Step 2: Wait for completion
kubectl wait --for=condition=complete --timeout=300s \
  job/codefactory-migrations -n codefactory

# Step 3: Deploy application
kubectl apply -k k8s/overlays/production

# Check migration logs
kubectl logs job/codefactory-migrations -n codefactory
```

**Enable Citus support:**
```bash
# Update ConfigMap
kubectl patch configmap codefactory-config -n codefactory \
  -p '{"data":{"ENABLE_CITUS":"1"}}'

# Restart pods to pick up change
kubectl rollout restart deployment/codefactory-api -n codefactory
```

For comprehensive Kubernetes migration guide, see [k8s/MIGRATIONS.md](./k8s/MIGRATIONS.md).

### Deployment Commands

**Development:**
```bash
make k8s-deploy-dev
# Or
kubectl apply -k k8s/overlays/development
```

**Staging:**
```bash
make k8s-deploy-staging
# Or
kubectl apply -k k8s/overlays/staging
```

**Production:**
```bash
make k8s-deploy-prod
# Or
kubectl apply -k k8s/overlays/production
```

### Monitoring Deployment

```bash
# Check status
make k8s-status
kubectl get all -n codefactory

# Watch pods
kubectl get pods -n codefactory -w

# View logs
make k8s-logs
kubectl logs -f -l app=codefactory-api -n codefactory

# Describe resources
kubectl describe deployment codefactory-api -n codefactory
```

### Scaling

**Manual:**
```bash
kubectl scale deployment codefactory-api --replicas=5 -n codefactory
```

**Autoscaling (HPA):**
```bash
kubectl autoscale deployment codefactory-api   --cpu-percent=70   --min=2   --max=10   -n codefactory
```

### Updates and Rollbacks

**Update Image:**
```bash
kubectl set image deployment/codefactory-api   api=code-factory:v1.1.0 -n codefactory

# Watch rollout
kubectl rollout status deployment/codefactory-api -n codefactory
```

**Rollback:**
```bash
# Rollback to previous
kubectl rollout undo deployment/codefactory-api -n codefactory

# Rollback to specific revision
kubectl rollout undo deployment/codefactory-api --to-revision=2 -n codefactory

# View history
kubectl rollout history deployment/codefactory-api -n codefactory
```

### Cleanup

```bash
# Delete environment
make k8s-delete-dev
kubectl delete -k k8s/overlays/development

# Delete specific resources
kubectl delete deployment codefactory-api -n codefactory
```

---

## Helm Deployment

### Chart Structure

```
helm/codefactory/
├── Chart.yaml           # Chart metadata
├── values.yaml          # Default values (300+ options)
├── templates/           # Kubernetes templates
│   ├── deployment.yaml
│   ├── migration-job.yaml  # Database migration job
│   ├── service.yaml
│   ├── ingress.yaml
│   ├── configmap.yaml
│   ├── secret.yaml
│   ├── serviceaccount.yaml
│   ├── hpa.yaml
│   └── pvc.yaml
└── README.md
```

### Database Migrations

The Helm chart supports automatic database migrations:

**Migration as Init Container (recommended):**
```yaml
# values.yaml
migrations:
  enabled: true
  runAs: "initContainer"  # Runs before app starts
```

**Migration as Pre-Install Hook:**
```yaml
migrations:
  enabled: true
  runAs: "job"
  job:
    autoRun: true
    hook:
      enabled: true  # Runs before deployment
```

**Enable Citus support:**
```yaml
env:
  ENABLE_CITUS: "1"

secrets:
  database:
    enabled: true
    urlSecretName: "codefactory-secrets"
    urlSecretKey: "database-url"
```

For detailed Helm migration configuration, see [helm/codefactory/README.md](./helm/codefactory/README.md).

### Installation

**Basic Install:**
```bash
helm install codefactory ./helm/codefactory   --namespace codefactory   --create-namespace
```

**Development:**
```bash
make helm-install-dev
```

**Production:**
```bash
make helm-install-prod
# Or
helm upgrade --install codefactory-prod ./helm/codefactory   --namespace codefactory-production   --create-namespace   --set image.tag=latest   --set replicaCount=3   --set autoscaling.enabled=true
```

### Configuration

**Custom Values:**
```bash
helm install codefactory ./helm/codefactory -f my-values.yaml
```

**Override Values:**
```bash
helm install codefactory ./helm/codefactory   --set replicaCount=3   --set image.tag=v1.0.0   --set ingress.enabled=true   --set ingress.hosts[0].host=codefactory.example.com
```

### Upgrades and Rollbacks

**Upgrade:**
```bash
helm upgrade codefactory ./helm/codefactory   --namespace codefactory   --set image.tag=v1.1.0
```

**Rollback:**
```bash
# View history
helm history codefactory -n codefactory

# Rollback
helm rollback codefactory 2 -n codefactory
```

### Helm Operations

```bash
# List releases
make helm-status
helm list -A

# Get values
helm get values codefactory -n codefactory

# Template rendering
make helm-template

# Lint chart
make helm-lint

# Package chart
make helm-package
```

### Uninstall

```bash
make helm-uninstall
# Or
helm uninstall codefactory -n codefactory
```

---

## Makefile Commands

### Quick Reference

```bash
make help  # Show all commands
```

### Installation

```bash
make install       # Production dependencies
make install-dev   # Development tools
make setup         # Initial project setup
```

### Testing

```bash
make test                  # All tests
make test-generator        # Generator tests
make test-omnicore         # OmniCore tests
make test-sfe              # SFE tests
make test-coverage         # Coverage report
```

### Code Quality

```bash
make lint          # All linters
make format        # Black formatting
make type-check    # MyPy
make security-scan # Bandit + Safety
```

### Docker

```bash
make docker-build         # Build image
make docker-up            # Start services
make docker-down          # Stop services
make docker-logs          # View logs
make docker-clean         # Remove all
make docker-validate      # Validate build
make deployment-validate  # Validate generated deployment files (NEW)
```

**NEW: Deployment Validation**

The `deployment-validate` command validates generated deployment artifacts from code generation jobs:
- Checks all required files exist (Dockerfile, docker-compose.yml, K8s manifests, Helm charts)
- Validates YAML syntax
- Ensures no unsubstituted placeholders
- Verifies deployment configs match generated code

```bash
# After running code generation
make deployment-validate
```

### Kubernetes

```bash
make k8s-deploy-dev      # Deploy to dev
make k8s-deploy-staging  # Deploy to staging
make k8s-deploy-prod     # Deploy to production
make k8s-status          # View status
make k8s-logs            # View logs
make k8s-delete-dev      # Delete dev
```

### Helm

```bash
make helm-install        # Install chart
make helm-install-dev    # Dev install
make helm-install-prod   # Production install
make helm-uninstall      # Remove release
make helm-template       # Render templates
make helm-lint           # Lint chart
make helm-package        # Package chart
```

### Development

```bash
make run-server      # Start API
make run-generator   # Start generator
make run-omnicore    # Start OmniCore
make health-check    # Check services
```

### Monitoring

```bash
make logs-generator  # Generator logs
make logs-omnicore   # OmniCore logs
make logs-sfe        # SFE logs
make metrics         # View metrics
```

### Cleanup

```bash
make clean           # Clean caches
make clean-all       # Deep clean
make db-reset        # Reset databases
make clean-old-docs  # Remove old docs
```

---

## Production Deployment

### Pre-Deployment Checklist

**Infrastructure:**
- [ ] Kubernetes cluster provisioned
- [ ] Ingress controller installed (nginx, traefik)
- [ ] Cert-manager for TLS certificates
- [ ] Persistent storage configured
- [ ] DNS records created
- [ ] Load balancer configured

**Secrets:**
- [ ] API keys stored in Kubernetes secrets
- [ ] Database credentials secured
- [ ] Encryption keys generated (Fernet)
- [ ] HMAC keys generated (64 hex chars)
- [ ] TLS certificates obtained (Let's Encrypt)

**Monitoring:**
- [ ] Prometheus configured
- [ ] Grafana dashboards installed
- [ ] Alert rules defined
- [ ] Log aggregation setup (ELK, Loki)
- [ ] On-call rotation configured

**Backup:**
- [ ] Backup schedule configured (daily)
- [ ] Backup storage configured (S3, GCS)
- [ ] Recovery procedures tested
- [ ] Disaster recovery plan documented

**Security:**
- [ ] Network policies applied
- [ ] RBAC configured
- [ ] Pod Security Standards enforced
- [ ] Security scanning enabled (Trivy)
- [ ] Audit logging enabled

### Security Hardening

**Pod Security Standards:**
```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: codefactory-production
  labels:
    pod-security.kubernetes.io/enforce: restricted
    pod-security.kubernetes.io/audit: restricted
```

**Network Policies:**
```yaml
# Default deny all
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-all
spec:
  podSelector: {}
  policyTypes:
  - Ingress
  - Egress
```

**Security Context:**
```yaml
securityContext:
  runAsNonRoot: true
  runAsUser: 10001
  fsGroup: 10001
  allowPrivilegeEscalation: false
  capabilities:
    drop:
    - ALL
  seccompProfile:
    type: RuntimeDefault
```

### High Availability

**Multiple Replicas:**
```yaml
spec:
  replicas: 3
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
```

**Pod Disruption Budget:**
```yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: codefactory-pdb
spec:
  minAvailable: 2
  selector:
    matchLabels:
      app: codefactory-api
```

**Autoscaling (HPA):**
```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: codefactory-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: codefactory-api
  minReplicas: 3
  maxReplicas: 20
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
  - type: Resource
    resource:
      name: memory
      target:
        type: Utilization
        averageUtilization: 80
```

### Backup & Restore

**Database Backup:**
```bash
# Create backup
kubectl exec -it deployment/postgres -n codefactory --   pg_dump -U codefactory codefactory > backup.sql

# Restore backup
kubectl exec -i deployment/postgres -n codefactory --   psql -U codefactory codefactory < backup.sql
```

**Automated Backup CronJob:**
```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: postgres-backup
spec:
  schedule: "0 2 * * *"  # Daily at 2 AM
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: backup
            image: postgres:15-alpine
            command:
            - /bin/sh
            - -c
            - |
              pg_dump -U codefactory codefactory |               gzip > /backup/codefactory-$(date +%Y%m%d).sql.gz
            volumeMounts:
            - name: backup
              mountPath: /backup
          volumes:
          - name: backup
            persistentVolumeClaim:
              claimName: backup-storage
          restartPolicy: OnFailure
```

---

## CI/CD Integration

### GitHub Actions

```yaml
name: Deploy to Production

on:
  push:
    branches: [main]
    tags: ['v*']

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v4
    
    - name: Build and push
      uses: docker/build-push-action@v5
      with:
        context: .
        push: true
        tags: ghcr.io/${{ github.repository }}:${{ github.sha }}

  deploy:
    needs: build
    runs-on: ubuntu-latest
    steps:
    - name: Deploy to production
      run: |
        helm upgrade --install codefactory ./helm/codefactory           --namespace codefactory-production           --set image.tag=${{ github.sha }}           --wait --timeout 5m
```

### GitLab CI

```yaml
stages:
  - build
  - deploy

build:
  stage: build
  script:
    - docker build -t $CI_REGISTRY_IMAGE:$CI_COMMIT_SHA .
    - docker push $CI_REGISTRY_IMAGE:$CI_COMMIT_SHA

deploy:production:
  stage: deploy
  script:
    - helm upgrade --install codefactory ./helm/codefactory
        --namespace codefactory-production
        --set image.tag=$CI_COMMIT_SHA
  only:
    - main
  when: manual
```

---

## Troubleshooting

### Container Won't Start

```bash
# Check logs
docker logs codefactory
kubectl logs -f deployment/codefactory-api -n codefactory

# Check events
kubectl get events -n codefactory --sort-by='.lastTimestamp'

# Describe pod
kubectl describe pod <pod-name> -n codefactory
```

### Health Check Fails

```bash
# Test endpoint
curl http://localhost:8000/health
kubectl exec -it deployment/codefactory-api -n codefactory --   curl http://localhost:8000/health

# Check startup time
kubectl get pod -n codefactory -o jsonpath='{.items[0].status.startTime}'
```

### Database Connection Issues

```bash
# Test connection
kubectl exec -it deployment/codefactory-api -n codefactory --   python -c "import asyncpg; asyncpg.connect('postgresql://...')"

# Check DNS
kubectl exec -it deployment/codefactory-api -n codefactory --   nslookup postgres

# Verify credentials
kubectl get secret codefactory-secrets -n codefactory -o yaml
```

### Network Issues

```bash
# Test connectivity
kubectl exec -it deployment/codefactory-api -n codefactory --   curl http://redis:6379

# Check network policies
kubectl get networkpolicy -n codefactory
kubectl describe networkpolicy api-network-policy -n codefactory
```

### Performance Issues

```bash
# Check resource usage
kubectl top pod -n codefactory
kubectl top node

# View metrics
curl http://localhost:9090/metrics | grep http_request
```

---

## Appendices

### Appendix A: Environment Variables

**Required:**
- `AGENTIC_AUDIT_HMAC_KEY` - 64 character hex key
- `ENCRYPTION_KEY` - Base64 encoded Fernet key
- `OPENAI_API_KEY` or other LLM provider key

**Infrastructure:**
- `REDIS_URL` - Redis connection string
- `DB_PATH` - Database connection string

**Feature Flags:**
- `ENABLE_DATABASE` - 1/0/auto
- `ENABLE_HSM` - 1/0/auto
- `ENABLE_FEATURE_STORE` - 1/0/auto
- `ENABLE_LIBVIRT` - 1/0/auto

**Performance:**
- `PARALLEL_AGENT_LOADING` - 1/0
- `LAZY_LOAD_ML` - 1/0
- `WORKER_COUNT` - Number of workers
- `STARTUP_TIMEOUT` - Startup timeout in seconds

**Monitoring:**
- `PROMETHEUS_PORT` - Metrics port (default: 9090)
- `LOG_LEVEL` - DEBUG/INFO/WARNING/ERROR
- `ENABLE_STRUCTURED_LOGGING` - 1/0

### Appendix B: Port Mappings

| Service | Container Port | Host Port | Description |
|---------|---------------|-----------|-------------|
| API | 8000 | 8000 | Main API endpoint |
| Metrics | 9090 | 9090 | Prometheus metrics |
| Redis | 6379 | 6379 | Cache/message bus |
| PostgreSQL | 5432 | 5432 | Database |
| Prometheus | 9090 | 9091 | Metrics server |
| Grafana | 3000 | 3000 | Dashboards |

### Appendix C: Resource Requirements

**Minimum (Development):**
- CPU: 2 cores
- Memory: 4 GB
- Storage: 20 GB

**Recommended (Production):**
- CPU: 8 cores
- Memory: 16 GB
- Storage: 100 GB

**Per Pod:**
- CPU Request: 500m
- CPU Limit: 2000m
- Memory Request: 1Gi
- Memory Limit: 4Gi

### Appendix D: Security Checklist

**Container Security:**
- [ ] Non-root user (UID 10001)
- [ ] Read-only root filesystem
- [ ] No privileged mode
- [ ] Capability dropping
- [ ] Security scanning enabled
- [ ] Base image updated regularly

**Kubernetes Security:**
- [ ] Network policies enforced
- [ ] RBAC configured
- [ ] Pod Security Standards (restricted)
- [ ] Secrets encrypted at rest
- [ ] TLS for external traffic
- [ ] Audit logging enabled

**Application Security:**
- [ ] API keys in secrets
- [ ] Encryption keys rotated
- [ ] HMAC keys unique per environment
- [ ] Input validation enabled
- [ ] Rate limiting configured

---

## Support

For issues and questions:
- **GitHub Issues**: https://github.com/musicmonk42/The_Code_Factory_Working_V2/issues
- **Documentation**: https://github.com/musicmonk42/The_Code_Factory_Working_V2
- **Email**: support@novatraxlabs.com

---

**Last Updated:** 2026-02-06  
**Version:** 1.0.0  
**Maintainer:** musicmonk42
