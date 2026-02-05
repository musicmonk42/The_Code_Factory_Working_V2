# Helm Deployment Guide for Code Factory

This guide provides comprehensive instructions for deploying The Code Factory platform using Helm.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Creating Secrets](#creating-secrets)
- [Installing with Helm](#installing-with-helm)
- [Configuration Options](#configuration-options)
- [Upgrading and Rollback](#upgrading-and-rollback)
- [Troubleshooting](#troubleshooting)
- [Advanced Configuration](#advanced-configuration)

## Prerequisites

Before deploying Code Factory with Helm, ensure you have:

- **Kubernetes Cluster**: Version 1.23 or later
- **Helm**: Version 3.8 or later
- **kubectl**: Configured to access your cluster
- **Persistent Storage**: StorageClass supporting ReadWriteMany (for multi-replica deployments)
- **Ingress Controller**: (Optional) nginx-ingress or similar for external access
- **Cert Manager**: (Optional) for automatic TLS certificate management

### Verify Prerequisites

```bash
# Check Kubernetes version
kubectl version --short

# Check Helm version
helm version --short

# Check available storage classes
kubectl get storageclass

# Check if ingress controller is installed
kubectl get pods -n ingress-nginx
```

## Quick Start

The fastest way to get started with Code Factory on Kubernetes:

```bash
# 1. Create secrets
kubectl create namespace codefactory
kubectl create secret generic codefactory-secrets \
  --from-literal=redis-password=$(openssl rand -base64 32) \
  --from-literal=openai-api-key=sk-YOUR-KEY \
  --from-literal=hmac-key=$(openssl rand -hex 32) \
  --from-literal=encryption-key=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())") \
  -n codefactory

# 2. Install with Helm
helm install codefactory ./helm/codefactory \
  --namespace codefactory \
  --set image.tag=latest

# 3. Verify deployment
kubectl get pods -n codefactory
kubectl logs -f -l app.kubernetes.io/name=codefactory -n codefactory

# 4. Access the application
kubectl port-forward svc/codefactory 8000:80 -n codefactory
# Open http://localhost:8000
```

## Creating Secrets

Code Factory requires several secrets for proper operation. Create them before installing the Helm chart.

### Generate Strong Random Secrets

```bash
# Redis password
REDIS_PASSWORD=$(openssl rand -base64 32)

# HMAC key for audit logging (64 hex characters)
HMAC_KEY=$(openssl rand -hex 32)

# Encryption key (Fernet key)
ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")

# Display generated secrets (save these securely!)
echo "Redis Password: $REDIS_PASSWORD"
echo "HMAC Key: $HMAC_KEY"
echo "Encryption Key: $ENCRYPTION_KEY"
```

### Create Kubernetes Secret

```bash
kubectl create namespace codefactory

kubectl create secret generic codefactory-secrets \
  --from-literal=redis-password="$REDIS_PASSWORD" \
  --from-literal=openai-api-key="sk-YOUR-OPENAI-KEY" \
  --from-literal=anthropic-api-key="sk-ant-YOUR-ANTHROPIC-KEY" \
  --from-literal=hmac-key="$HMAC_KEY" \
  --from-literal=encryption-key="$ENCRYPTION_KEY" \
  --from-literal=database-url="postgresql://user:pass@host:5432/db" \
  -n codefactory
```

### Using External Secrets Operator (Recommended for Production)

For production deployments, use External Secrets Operator to sync secrets from AWS Secrets Manager, HashiCorp Vault, or other secret stores:

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: codefactory-secrets
  namespace: codefactory
spec:
  secretStoreRef:
    name: aws-secrets-manager
    kind: SecretStore
  target:
    name: codefactory-secrets
  data:
    - secretKey: redis-password
      remoteRef:
        key: codefactory/redis-password
    - secretKey: openai-api-key
      remoteRef:
        key: codefactory/openai-api-key
    # ... other secrets
```

## Installing with Helm

### Basic Installation

```bash
# Install with default settings
helm install codefactory ./helm/codefactory \
  --namespace codefactory \
  --create-namespace
```

### Development Installation

```bash
# Install for development with lower resource limits
helm install codefactory-dev ./helm/codefactory \
  --namespace codefactory-dev \
  --create-namespace \
  --set image.tag=dev \
  --set replicaCount=1 \
  --set resources.requests.cpu=250m \
  --set resources.requests.memory=512Mi \
  --set resources.limits.cpu=1000m \
  --set resources.limits.memory=2Gi
```

### Production Installation

```bash
# Install for production with autoscaling
helm install codefactory-prod ./helm/codefactory \
  --namespace codefactory-production \
  --create-namespace \
  --set image.tag=latest \
  --set replicaCount=3 \
  --set autoscaling.enabled=true \
  --set autoscaling.minReplicas=3 \
  --set autoscaling.maxReplicas=10 \
  --set ingress.enabled=true \
  --set ingress.hosts[0].host=codefactory.example.com
```

### Using a Custom Values File

Create a custom `values-prod.yaml`:

```yaml
image:
  repository: ghcr.io/musicmonk42/codefactory
  tag: "v1.2.3"
  pullPolicy: IfNotPresent

replicaCount: 3

autoscaling:
  enabled: true
  minReplicas: 3
  maxReplicas: 10

resources:
  requests:
    cpu: 500m
    memory: 1Gi
  limits:
    cpu: 2000m
    memory: 4Gi

ingress:
  enabled: true
  className: nginx
  hosts:
    - host: codefactory.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: codefactory-tls
      hosts:
        - codefactory.example.com

persistence:
  uploads:
    enabled: true
    size: 50Gi
  workspace:
    enabled: true
    size: 20Gi
```

Install with custom values:

```bash
helm install codefactory-prod ./helm/codefactory \
  --namespace codefactory-production \
  --create-namespace \
  --values values-prod.yaml
```

## Configuration Options

### Image Configuration

```yaml
image:
  repository: ghcr.io/musicmonk42/codefactory
  pullPolicy: IfNotPresent
  tag: "latest"  # Override with specific version
```

### Scaling Configuration

```yaml
replicaCount: 3  # Manual replica count (if autoscaling disabled)

autoscaling:
  enabled: true
  minReplicas: 3
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70
  targetMemoryUtilizationPercentage: 80
```

### Resource Limits

```yaml
resources:
  requests:
    cpu: 500m      # Guaranteed CPU
    memory: 1Gi    # Guaranteed memory
  limits:
    cpu: 2000m     # Maximum CPU
    memory: 4Gi    # Maximum memory
```

### Probes Configuration

```yaml
startupProbe:
  initialDelaySeconds: 5
  periodSeconds: 5
  failureThreshold: 18  # 90 seconds for agent loading

livenessProbe:
  initialDelaySeconds: 10
  periodSeconds: 30
  failureThreshold: 3

readinessProbe:
  initialDelaySeconds: 5
  periodSeconds: 10
  failureThreshold: 3
```

### Ingress Configuration

```yaml
ingress:
  enabled: true
  className: nginx
  annotations:
    cert-manager.io/cluster-issuer: "letsencrypt-prod"
    nginx.ingress.kubernetes.io/rate-limit: "100"
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
  hosts:
    - host: codefactory.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: codefactory-tls
      hosts:
        - codefactory.example.com
```

### Persistence Configuration

```yaml
persistence:
  uploads:
    enabled: true
    storageClass: "nfs-client"  # Your storage class
    accessMode: ReadWriteMany   # Required for multi-replica
    size: 10Gi
  workspace:
    enabled: true
    storageClass: "nfs-client"
    accessMode: ReadWriteMany
    size: 5Gi
```

### Environment Variables

```yaml
env:
  PRODUCTION_MODE: "1"
  APP_ENV: "production"
  LOG_LEVEL: "INFO"
  WORKER_COUNT: "4"
  ENABLE_PROMETHEUS: "1"
  # Add custom environment variables here
```

## Upgrading and Rollback

### Upgrading the Release

```bash
# Upgrade to new version
helm upgrade codefactory ./helm/codefactory \
  --namespace codefactory \
  --set image.tag=v1.2.3

# Upgrade with new values file
helm upgrade codefactory ./helm/codefactory \
  --namespace codefactory \
  --values values-prod.yaml \
  --reuse-values
```

### Checking Upgrade Status

```bash
# View upgrade history
helm history codefactory -n codefactory

# Check rollout status
kubectl rollout status deployment/codefactory -n codefactory

# Watch pods during upgrade
watch kubectl get pods -n codefactory
```

### Rolling Back

```bash
# Rollback to previous version
helm rollback codefactory -n codefactory

# Rollback to specific revision
helm rollback codefactory 3 -n codefactory

# View rollback history
helm history codefactory -n codefactory
```

## Troubleshooting

### Checking Installation Status

```bash
# View Helm release status
helm status codefactory -n codefactory

# View all resources
kubectl get all -n codefactory

# Describe problematic pods
kubectl describe pod <pod-name> -n codefactory
```

### Viewing Logs

```bash
# View application logs
kubectl logs -f -l app.kubernetes.io/name=codefactory -n codefactory

# View logs from specific pod
kubectl logs -f <pod-name> -n codefactory

# View previous pod logs (if crashed)
kubectl logs --previous <pod-name> -n codefactory
```

### Common Issues

#### Pods Not Starting

**Symptom**: Pods stuck in `Pending` or `CrashLoopBackOff`

**Solutions**:
```bash
# Check pod events
kubectl describe pod <pod-name> -n codefactory

# Common causes:
# 1. Insufficient resources
kubectl describe nodes

# 2. Missing secrets
kubectl get secrets -n codefactory

# 3. Image pull errors
kubectl describe pod <pod-name> -n codefactory | grep -A 5 "Events:"
```

#### Startup Probe Failing

**Symptom**: Pods restarting with "Startup probe failed"

**Solutions**:
```bash
# Increase startup probe timeout
helm upgrade codefactory ./helm/codefactory \
  --namespace codefactory \
  --set startupProbe.failureThreshold=30  # 150 seconds
  --reuse-values

# Check agent loading time in logs
kubectl logs <pod-name> -n codefactory | grep -i "agent"
```

#### Persistent Volume Issues

**Symptom**: Pods stuck in `Pending` with "FailedMount" events

**Solutions**:
```bash
# Check PVC status
kubectl get pvc -n codefactory

# Describe problematic PVC
kubectl describe pvc <pvc-name> -n codefactory

# Verify storage class exists
kubectl get storageclass

# If using ReadWriteMany, ensure storage class supports it
```

#### Redis Connection Issues

**Symptom**: Application logs show Redis connection errors

**Solutions**:
```bash
# Check Redis pod status
kubectl get pods -l app=codefactory-redis -n codefactory

# Test Redis connectivity
kubectl run -it --rm redis-test --image=redis:7-alpine --restart=Never -n codefactory -- \
  redis-cli -h codefactory-redis -a <password> ping

# Check Redis password in secret
kubectl get secret codefactory-secrets -n codefactory -o jsonpath='{.data.redis-password}' | base64 -d
```

### Debugging Commands

```bash
# Get detailed pod information
kubectl get pods -n codefactory -o wide

# View all events in namespace
kubectl get events -n codefactory --sort-by='.lastTimestamp'

# Execute command in pod
kubectl exec -it <pod-name> -n codefactory -- /bin/sh

# Check resource usage
kubectl top pods -n codefactory
kubectl top nodes

# Test service connectivity
kubectl run -it --rm curl-test --image=curlimages/curl --restart=Never -n codefactory -- \
  curl http://codefactory/health
```

## Advanced Configuration

### Using External Redis

If you have an external Redis instance:

```yaml
redis:
  enabled: false  # Disable built-in Redis

secrets:
  redis:
    host: "redis.example.com"
    port: "6379"
    passwordSecretName: "external-redis-secret"
    passwordSecretKey: "password"
```

### Using External Database

```yaml
secrets:
  database:
    enabled: true
    urlSecretName: "database-credentials"
    urlSecretKey: "connection-url"

env:
  ENABLE_DATABASE: "1"
```

### Custom Security Contexts

```yaml
podSecurityContext:
  runAsNonRoot: true
  runAsUser: 1000
  fsGroup: 1000
  seccompProfile:
    type: RuntimeDefault

securityContext:
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true  # If your app supports it
  runAsNonRoot: true
  runAsUser: 1000
  capabilities:
    drop:
    - ALL
```

### Monitoring with Prometheus

Enable ServiceMonitor for Prometheus Operator:

```yaml
monitoring:
  serviceMonitor:
    enabled: true
    interval: 30s
    scrapeTimeout: 10s
    labels:
      prometheus: kube-prometheus
```

### Node Affinity and Tolerations

```yaml
nodeSelector:
  workload-type: compute-intensive

tolerations:
  - key: "dedicated"
    operator: "Equal"
    value: "codefactory"
    effect: "NoSchedule"

affinity:
  podAntiAffinity:
    preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 100
        podAffinityTerm:
          labelSelector:
            matchLabels:
              app.kubernetes.io/name: codefactory
          topologyKey: kubernetes.io/hostname
```

## Best Practices

### Production Checklist

- [ ] Use specific image tags (not `latest`)
- [ ] Enable autoscaling with appropriate min/max replicas
- [ ] Configure resource requests and limits
- [ ] Use external secrets management (AWS Secrets Manager, Vault, etc.)
- [ ] Enable ingress with TLS/SSL
- [ ] Configure Prometheus monitoring
- [ ] Set up backup for persistent volumes
- [ ] Configure PodDisruptionBudget
- [ ] Use topology spread constraints for high availability
- [ ] Enable network policies
- [ ] Review and harden security contexts
- [ ] Set up log aggregation
- [ ] Configure alerts for critical metrics

### Security Best Practices

1. **Never commit secrets to git** - Use external secrets management
2. **Use least privilege RBAC** - Service account has minimal permissions
3. **Run as non-root** - All containers run as unprivileged users
4. **Enable network policies** - Restrict pod-to-pod communication
5. **Use TLS everywhere** - Encrypt all external communication
6. **Regular security scans** - Scan images for vulnerabilities
7. **Rotate secrets regularly** - Implement secret rotation policy

### Disaster Recovery

```bash
# Backup Helm release values
helm get values codefactory -n codefactory > backup-values.yaml

# Backup secrets (store securely!)
kubectl get secret codefactory-secrets -n codefactory -o yaml > backup-secrets.yaml

# Backup persistent volume data
kubectl exec <pod-name> -n codefactory -- tar czf - /app/uploads > uploads-backup.tar.gz

# Restore from backup
kubectl apply -f backup-secrets.yaml
helm upgrade --install codefactory ./helm/codefactory \
  --namespace codefactory \
  --values backup-values.yaml
```

## Support and Resources

- **Documentation**: See `docs/KUBERNETES_DEPLOYMENT.md` for Kustomize deployment
- **Issues**: Report issues on GitHub
- **Community**: Join our community discussions

## Appendix: Complete Example

Here's a complete production deployment example:

```bash
# 1. Create namespace
kubectl create namespace codefactory-production

# 2. Create secrets
kubectl create secret generic codefactory-secrets \
  --from-literal=redis-password=$(openssl rand -base64 32) \
  --from-literal=openai-api-key=$OPENAI_API_KEY \
  --from-literal=hmac-key=$(openssl rand -hex 32) \
  --from-literal=encryption-key=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())") \
  -n codefactory-production

# 3. Create values file
cat <<EOF > values-production.yaml
image:
  repository: ghcr.io/musicmonk42/codefactory
  tag: "v1.2.3"
  pullPolicy: IfNotPresent

replicaCount: 3

autoscaling:
  enabled: true
  minReplicas: 3
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70

resources:
  requests:
    cpu: 500m
    memory: 1Gi
  limits:
    cpu: 2000m
    memory: 4Gi

ingress:
  enabled: true
  className: nginx
  hosts:
    - host: codefactory.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: codefactory-tls
      hosts:
        - codefactory.example.com

persistence:
  uploads:
    enabled: true
    size: 50Gi
  workspace:
    enabled: true
    size: 20Gi

monitoring:
  serviceMonitor:
    enabled: true
EOF

# 4. Install with Helm
helm install codefactory-prod ./helm/codefactory \
  --namespace codefactory-production \
  --values values-production.yaml

# 5. Wait for deployment
kubectl rollout status deployment/codefactory-prod -n codefactory-production

# 6. Verify
kubectl get pods -n codefactory-production
kubectl get ingress -n codefactory-production
curl -k https://codefactory.example.com/health

# 7. Monitor
kubectl logs -f -l app.kubernetes.io/name=codefactory -n codefactory-production
```
