# Kubernetes Deployment Guide for Code Factory Platform

This guide provides Kubernetes deployment configurations optimized for the Code Factory platform, including proper health checks, startup probes, and resource management. It covers both Kustomize-based deployments and raw manifest usage.

## Overview

The Code Factory platform requires specific Kubernetes configurations to handle:
- Background agent loading (~8-10s with parallel loading)
- Distributed locking for multi-replica deployments
- Redis connectivity for coordination
- Graceful startup and shutdown

## Deployment Options

The Code Factory platform can be deployed using two methods:

1. **Helm** (Recommended for most users) - See `docs/HELM_DEPLOYMENT.md`
2. **Kustomize** (Recommended for GitOps workflows) - Covered in this document

## Prerequisites

- Kubernetes 1.19+ (for startup probe support)
- kubectl with Kustomize support (v1.14+)
- Redis instance (for distributed locking)
- Persistent volume for uploads (optional)
- Load balancer or ingress controller

## Quick Start with Kustomize

### Deploy to Development

```bash
# Deploy to development environment
kubectl apply -k k8s/overlays/development

# Check status
kubectl get pods -n codefactory-dev

# View logs
kubectl logs -f -l app=codefactory-api -n codefactory-dev
```

### Deploy to Production

```bash
# First, update secrets in k8s/base/secret.yaml with actual values
# Then deploy
kubectl apply -k k8s/overlays/production

# Check status
kubectl get pods -n codefactory-production

# View HPA status
kubectl get hpa -n codefactory-production
```

## Kustomize Deployment Structure

The Code Factory includes a complete Kustomize setup with base manifests and environment-specific overlays:

```
k8s/
├── base/                          # Base manifests (shared across environments)
│   ├── kustomization.yaml         # Base kustomization config
│   ├── namespace.yaml             # Namespace definition
│   ├── configmap.yaml             # Environment variables
│   ├── secret.yaml                # Secret templates (replace before deploying!)
│   ├── rbac.yaml                  # Service account, role, role binding
│   ├── api-deployment.yaml        # Main application deployment
│   ├── redis-deployment.yaml      # Redis deployment with persistence
│   ├── ingress.yaml               # Ingress configuration
│   ├── api-networkpolicy.yaml     # Network policies for API
│   └── redis-networkpolicy.yaml   # Network policies for Redis
└── overlays/                      # Environment-specific configurations
    ├── development/               # Development environment
    │   ├── kustomization.yaml     # Dev-specific patches
    │   └── namespace.yaml         # codefactory-dev namespace
    ├── staging/                   # Staging environment
    │   ├── kustomization.yaml     # Staging-specific patches
    │   └── namespace.yaml         # codefactory-staging namespace
    └── production/                # Production environment
        ├── kustomization.yaml     # Production-specific patches
        ├── namespace.yaml         # codefactory-production namespace
        ├── hpa.yaml               # Horizontal Pod Autoscaler
        └── pdb.yaml               # Pod Disruption Budget
```

### Environment Configurations

#### Development
- **Namespace**: `codefactory-dev`
- **Replicas**: 1
- **Resources**: 250m CPU / 512Mi RAM (requests), 1 CPU / 2Gi RAM (limits)
- **Image Tag**: `dev`
- **Log Level**: `DEBUG`

#### Staging
- **Namespace**: `codefactory-staging`
- **Replicas**: 2
- **Resources**: 500m CPU / 1Gi RAM (requests), 1.5 CPU / 3Gi RAM (limits)
- **Image Tag**: `staging`
- **Log Level**: `INFO`

#### Production
- **Namespace**: `codefactory-production`
- **Replicas**: 3 (with HPA 3-10)
- **Resources**: 500m CPU / 1Gi RAM (requests), 2 CPU / 4Gi RAM (limits)
- **Image Tag**: `latest`
- **Log Level**: `INFO`
- **Additional Features**: HPA, PodDisruptionBudget, higher worker count

## Deploying with Kustomize

### Prepare Secrets

Before deploying, you MUST update the secrets in `k8s/base/secret.yaml`:

```bash
# Generate strong random secrets
REDIS_PASSWORD=$(openssl rand -base64 32)
HMAC_KEY=$(openssl rand -hex 32)
ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")

# Edit the secret file
vi k8s/base/secret.yaml

# Replace:
# - REPLACE_WITH_REDIS_PASSWORD with $REDIS_PASSWORD
# - REPLACE_WITH_HMAC_KEY with $HMAC_KEY
# - REPLACE_WITH_ENCRYPTION_KEY with $ENCRYPTION_KEY
# - REPLACE_WITH_OPENAI_API_KEY with your OpenAI API key
```

**Security Note**: For production, use external secrets management (AWS Secrets Manager, HashiCorp Vault, etc.) instead of committing secrets to git.

### Deploy to Development

```bash
# Preview what will be deployed
kubectl kustomize k8s/overlays/development

# Deploy
kubectl apply -k k8s/overlays/development

# Verify deployment
kubectl get all -n codefactory-dev

# Check pod status
kubectl get pods -n codefactory-dev -w

# View logs
kubectl logs -f -l app=codefactory-api -n codefactory-dev
```

### Deploy to Staging

```bash
# Deploy to staging
kubectl apply -k k8s/overlays/staging

# Verify
kubectl get all -n codefactory-staging
kubectl get pods -n codefactory-staging
```

### Deploy to Production

```bash
# Preview production deployment
kubectl kustomize k8s/overlays/production

# Deploy to production
kubectl apply -k k8s/overlays/production

# Verify deployment
kubectl get all -n codefactory-production

# Check HPA status
kubectl get hpa -n codefactory-production

# Check PDB
kubectl get pdb -n codefactory-production

# Monitor rollout
kubectl rollout status deployment/codefactory-api -n codefactory-production
```

### Update Deployments

```bash
# Update image tag in base kustomization.yaml or overlay
# Then apply changes
kubectl apply -k k8s/overlays/production

# Watch the rolling update
kubectl rollout status deployment/codefactory-api -n codefactory-production

# Rollback if needed
kubectl rollout undo deployment/codefactory-api -n codefactory-production
```

### Cleanup

```bash
# Delete development environment
kubectl delete -k k8s/overlays/development

# Delete staging environment
kubectl delete -k k8s/overlays/staging

# Delete production environment
kubectl delete -k k8s/overlays/production
```

## Network Policies

The Code Factory includes NetworkPolicies for security:

### API Network Policy
- **Ingress**: Allows traffic from ingress controller and Prometheus
- **Egress**: Allows DNS, Redis, and external API calls (HTTPS/HTTP)

### Redis Network Policy
- **Ingress**: Only allows connections from API pods
- **Egress**: Only allows DNS resolution

To disable NetworkPolicies (not recommended for production):
```bash
# Remove from kustomization.yaml resources list
# Or delete them after deployment
kubectl delete networkpolicy -n codefactory-production codefactory-api codefactory-redis
```

## RBAC Configuration

The deployment includes least-privilege RBAC:
- **ServiceAccount**: `codefactory`
- **Role**: Minimal permissions (get/list configmaps, get secrets)
- **RoleBinding**: Binds role to service account

No cluster-wide permissions are granted.

## Customizing Kustomize Overlays

### Add Custom Patches

Create a patch file in your overlay directory:

```yaml
# k8s/overlays/production/custom-patch.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: codefactory-api
spec:
  template:
    spec:
      nodeSelector:
        workload-type: ai-compute
```

Add it to `kustomization.yaml`:
```yaml
patches:
  - custom-patch.yaml
```

### Override Environment Variables

```yaml
# In overlay's kustomization.yaml
patches:
  - target:
      kind: ConfigMap
      name: codefactory-config
    patch: |-
      - op: add
        path: /data/CUSTOM_VAR
        value: "custom-value"
```

### Change Storage Class

```yaml
patches:
  - target:
      kind: PersistentVolumeClaim
    patch: |-
      - op: replace
        path: /spec/storageClassName
        value: "fast-ssd"
```

## Prerequisites

- Kubernetes 1.19+ (for startup probe support)
- Redis instance (for distributed locking)
- Persistent volume for uploads (optional)
- Load balancer or ingress controller

## Basic Deployment

### Deployment with Startup, Liveness, and Readiness Probes

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: code-factory
  namespace: production
  labels:
    app: code-factory
    version: v1.0.0
spec:
  replicas: 3  # Multiple replicas safe with distributed locking
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0  # Zero downtime deployments
  selector:
    matchLabels:
      app: code-factory
  template:
    metadata:
      labels:
        app: code-factory
        version: v1.0.0
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: "8000"
        prometheus.io/path: "/metrics"
    spec:
      # Use topology spread for better availability
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: kubernetes.io/hostname
          whenUnsatisfiable: DoNotSchedule
          labelSelector:
            matchLabels:
              app: code-factory
      
      # Service account for cloud provider integrations
      serviceAccountName: code-factory
      
      containers:
      - name: code-factory
        image: your-registry/code-factory:latest
        imagePullPolicy: IfNotPresent
        
        ports:
        - name: http
          containerPort: 8000
          protocol: TCP
        - name: metrics
          containerPort: 9090
          protocol: TCP
        
        env:
        # Production mode
        - name: PRODUCTION_MODE
          value: "1"
        
        # App configuration
        - name: APP_ENV
          value: "production"
        - name: PORT
          value: "8000"
        
        # Performance optimizations
        - name: PARALLEL_AGENT_LOADING
          value: "1"
        - name: LAZY_LOAD_ML
          value: "1"
        - name: STARTUP_TIMEOUT
          value: "90"
        
        # Feature flags
        - name: ENABLE_DATABASE
          value: "1"
        - name: ENABLE_PROMETHEUS
          value: "1"
        - name: ENABLE_AUDIT_LOGGING
          value: "1"
        
        # Redis for distributed locking
        - name: REDIS_URL
          value: "redis://code-factory-redis:6379/0"
        
        # API Keys from secrets
        - name: OPENAI_API_KEY
          valueFrom:
            secretKeyRef:
              name: code-factory-secrets
              key: openai-api-key
        
        # Database
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: code-factory-secrets
              key: database-url
        
        # Observability
        - name: SENTRY_DSN
          valueFrom:
            secretKeyRef:
              name: code-factory-secrets
              key: sentry-dsn
              optional: true
        
        resources:
          requests:
            memory: "2Gi"
            cpu: "1000m"
          limits:
            memory: "4Gi"
            cpu: "2000m"
        
        # STARTUP PROBE - Critical for Background Agent Loading
        # Allows up to 90 seconds for agents to load
        startupProbe:
          httpGet:
            path: /ready
            port: http
            scheme: HTTP
          initialDelaySeconds: 5
          periodSeconds: 5
          timeoutSeconds: 3
          successThreshold: 1
          failureThreshold: 18  # 18 * 5s = 90s max startup time
        
        # LIVENESS PROBE - Restart if unhealthy
        # Only starts after startup probe succeeds
        livenessProbe:
          httpGet:
            path: /health
            port: http
            scheme: HTTP
          initialDelaySeconds: 10
          periodSeconds: 30
          timeoutSeconds: 5
          successThreshold: 1
          failureThreshold: 3
        
        # READINESS PROBE - Remove from load balancer if not ready
        readinessProbe:
          httpGet:
            path: /ready
            port: http
            scheme: HTTP
          initialDelaySeconds: 5
          periodSeconds: 10
          timeoutSeconds: 3
          successThreshold: 1
          failureThreshold: 3
        
        # Graceful shutdown
        lifecycle:
          preStop:
            exec:
              command: ["/bin/sh", "-c", "sleep 15"]
        
        # Security context
        securityContext:
          runAsNonRoot: true
          runAsUser: 1000
          readOnlyRootFilesystem: false  # Needed for uploads
          allowPrivilegeEscalation: false
          capabilities:
            drop:
              - ALL
        
        volumeMounts:
        - name: uploads
          mountPath: /app/uploads
        - name: tmp
          mountPath: /tmp
      
      volumes:
      - name: uploads
        persistentVolumeClaim:
          claimName: code-factory-uploads
      - name: tmp
        emptyDir: {}
      
      # Init container to wait for Redis
      initContainers:
      - name: wait-for-redis
        image: busybox:1.35
        command: ['sh', '-c']
        args:
          - |
            until nc -z code-factory-redis 6379; do
              echo "Waiting for Redis..."
              sleep 2
            done
            echo "Redis is ready!"
```

### Service Definition

```yaml
apiVersion: v1
kind: Service
metadata:
  name: code-factory
  namespace: production
  labels:
    app: code-factory
spec:
  type: ClusterIP
  ports:
  - name: http
    port: 80
    targetPort: http
    protocol: TCP
  - name: metrics
    port: 9090
    targetPort: metrics
    protocol: TCP
  selector:
    app: code-factory
  sessionAffinity: None  # Stateless service
```

### Redis Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: code-factory-redis
  namespace: production
spec:
  replicas: 1  # For production, use Redis Sentinel or Cluster
  selector:
    matchLabels:
      app: code-factory-redis
  template:
    metadata:
      labels:
        app: code-factory-redis
    spec:
      containers:
      - name: redis
        image: redis:7-alpine
        ports:
        - containerPort: 6379
        resources:
          requests:
            memory: "256Mi"
            cpu: "250m"
          limits:
            memory: "512Mi"
            cpu: "500m"
        volumeMounts:
        - name: redis-data
          mountPath: /data
        livenessProbe:
          exec:
            command: ["redis-cli", "ping"]
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          exec:
            command: ["redis-cli", "ping"]
          initialDelaySeconds: 5
          periodSeconds: 5
      volumes:
      - name: redis-data
        emptyDir: {}

---
apiVersion: v1
kind: Service
metadata:
  name: code-factory-redis
  namespace: production
spec:
  type: ClusterIP
  ports:
  - port: 6379
    targetPort: 6379
  selector:
    app: code-factory-redis
```

### Secrets Configuration

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: code-factory-secrets
  namespace: production
type: Opaque
stringData:
  openai-api-key: "sk-..."
  anthropic-api-key: "sk-ant-..."
  database-url: "postgresql://user:pass@host:5432/db"
  sentry-dsn: "https://..."
```

### Persistent Volume Claim

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: code-factory-uploads
  namespace: production
spec:
  accessModes:
    - ReadWriteMany  # Shared across replicas
  resources:
    requests:
      storage: 10Gi
  storageClassName: standard
```

### Horizontal Pod Autoscaler

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: code-factory
  namespace: production
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: code-factory
  minReplicas: 3
  maxReplicas: 10
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
  behavior:
    scaleDown:
      stabilizationWindowSeconds: 300
      policies:
      - type: Percent
        value: 50
        periodSeconds: 60
    scaleUp:
      stabilizationWindowSeconds: 0
      policies:
      - type: Percent
        value: 100
        periodSeconds: 30
```

## Health Check Endpoints

The platform exposes three health check endpoints:

### `/health` - Liveness Probe
- **Purpose**: Determine if the container needs to be restarted
- **Returns**: HTTP 200 always (if API is responding)
- **Use for**: Liveness probe
- **Example response**:
```json
{
  "status": "healthy",
  "version": "1.0.0",
  "components": {
    "api": "healthy",
    "agents_status": "loading|ready|degraded"
  }
}
```

### `/ready` - Readiness Probe
- **Purpose**: Determine if the container should receive traffic
- **Returns**: 
  - HTTP 200 when agents are loaded and ready
  - HTTP 503 when agents are still loading or failed
- **Use for**: Readiness and startup probes
- **Example response**:
```json
{
  "ready": true,
  "status": "ready",
  "checks": {
    "api_available": "pass",
    "agents_loaded": "pass",
    "agents_available": "5/5"
  }
}
```

### `/health/detailed` - Detailed Status
- **Purpose**: Get detailed component status
- **Returns**: HTTP 200 with full status
- **Use for**: Monitoring dashboards
- **Example response**:
```json
{
  "status": "healthy",
  "agents": {
    "codegen": "available",
    "testgen": "available",
    ...
  },
  "dependencies": {
    "redis": "connected",
    "database": "configured"
  },
  "optional_features": {
    "hsm": "not_installed",
    "sphinx": "installed"
  }
}
```

## Probe Configuration Guidelines

### Startup Probe Configuration

The startup probe is **critical** for the Code Factory platform due to background agent loading.

**Recommended Settings:**
```yaml
startupProbe:
  httpGet:
    path: /ready
    port: http
  initialDelaySeconds: 5
  periodSeconds: 5
  failureThreshold: 18  # 90 seconds total
```

**Why these settings?**
- Agent loading takes 8-10s with parallel loading enabled
- Allows buffer for slower environments
- Prevents premature container restarts

### Liveness Probe Configuration

**Recommended Settings:**
```yaml
livenessProbe:
  httpGet:
    path: /health
    port: http
  initialDelaySeconds: 10
  periodSeconds: 30
  failureThreshold: 3
```

**Why these settings?**
- `/health` always returns 200 if API is responding
- 30s period avoids excessive checks
- 3 failures = 90s before restart

### Readiness Probe Configuration

**Recommended Settings:**
```yaml
readinessProbe:
  httpGet:
    path: /ready
    port: http
  initialDelaySeconds: 5
  periodSeconds: 10
  failureThreshold: 3
```

**Why these settings?**
- Checks actual agent availability
- Removes pod from load balancer if agents unavailable
- 10s period for responsive traffic management

## Multi-Replica Considerations

### Distributed Locking

When running multiple replicas, the platform uses Redis-based distributed locking to coordinate initialization:

1. **First container** acquires startup lock
2. **Other containers** detect lock is held
3. Each container loads agents independently (safe due to agent loader's internal lock)
4. Lock is released after startup completes

**This is safe and expected behavior.**

### Session Affinity

The platform is **stateless** - no session affinity required.

```yaml
sessionAffinity: None
```

### Shared Storage

If using file uploads, use `ReadWriteMany` persistent volumes:

```yaml
accessModes:
  - ReadWriteMany
```

## Monitoring and Alerts

### Prometheus Metrics

The platform exposes metrics at `/metrics` when `ENABLE_PROMETHEUS=1`.

**Recommended alerts:**
1. Agent loading time > 30s
2. Multiple containers restarting
3. Readiness failures > 10% of probes
4. Memory usage > 3.5Gi (approaching limit)

### Logging

Configure log aggregation (ELK, Loki, etc.) to capture:
- Startup timing
- Agent loading status
- Lock acquisition events
- Health check failures

## Troubleshooting

### Container Keeps Restarting

**Check:**
1. Startup probe timeout (increase `failureThreshold`)
2. Agent loading errors (check logs for agent import failures)
3. Missing API keys (check secrets)
4. Redis connectivity

**Solution:**
```bash
# Check logs
kubectl logs -f deployment/code-factory

# Check startup probe failures
kubectl describe pod <pod-name>
```

### Agents Not Loading

**Check:**
1. `PARALLEL_AGENT_LOADING=1` is set
2. Required dependencies installed
3. API keys configured
4. Memory limits not too restrictive

### Duplicate Initialization

**This is normal!** Multiple containers will each initialize agents. The Redis lock is informational and prevents race conditions, but each container loads its own agents.

## Production Checklist

- [ ] Startup probe configured with 90s timeout
- [ ] Liveness and readiness probes configured
- [ ] Redis deployed for distributed locking
- [ ] Secrets configured for API keys
- [ ] Resource requests and limits set
- [ ] HPA configured for auto-scaling
- [ ] Persistent volumes for uploads (if needed)
- [ ] Monitoring and alerting configured
- [ ] Log aggregation configured
- [ ] Ingress/load balancer configured
- [ ] SSL/TLS certificates configured
- [ ] Network policies configured
- [ ] Pod security policies applied

## Example Commands

```bash
# Deploy all resources
kubectl apply -f kubernetes/

# Check deployment status
kubectl get deployments -n production

# Check pod health
kubectl get pods -n production

# View logs
kubectl logs -f deployment/code-factory -n production

# Check health endpoint
kubectl port-forward svc/code-factory 8000:80 -n production
curl http://localhost:8000/health
curl http://localhost:8000/ready
curl http://localhost:8000/health/detailed

# Scale deployment
kubectl scale deployment code-factory --replicas=5 -n production

# Rolling update
kubectl set image deployment/code-factory code-factory=your-registry/code-factory:v2.0.0 -n production
kubectl rollout status deployment/code-factory -n production

# Rollback if needed
kubectl rollout undo deployment/code-factory -n production
```

## References

- [Kubernetes Startup Probe](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/#define-startup-probes)
- [Kubernetes Best Practices](https://kubernetes.io/docs/concepts/configuration/overview/)
- [Zero-Downtime Deployments](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/#rolling-update-deployment)
