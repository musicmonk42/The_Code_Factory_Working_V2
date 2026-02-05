# Kubernetes and Helm Quick Reference

This is a quick reference guide for deploying The Code Factory using Kubernetes and Helm.

## Quick Commands

### Helm Deployment

```bash
# Development
make helm-install-dev

# Production
make helm-install-prod

# Check status
make helm-status

# Uninstall
make helm-uninstall
```

### Kustomize Deployment

```bash
# Development
make k8s-deploy-dev

# Staging
make k8s-deploy-staging

# Production
make k8s-deploy-prod

# Check status
make k8s-status-prod

# View logs
make k8s-logs-prod
```

## File Structure

```
The_Code_Factory_Working_V2/
├── helm/
│   └── codefactory/              # Helm chart
│       ├── Chart.yaml            # Chart metadata
│       ├── values.yaml           # Default configuration
│       ├── README.md             # Chart documentation
│       ├── .helmignore           # Files to ignore in packaging
│       └── templates/            # Kubernetes resource templates
│           ├── _helpers.tpl      # Template helper functions
│           ├── NOTES.txt         # Post-install instructions
│           ├── deployment.yaml   # Application deployment
│           ├── service.yaml      # Service definition
│           ├── ingress.yaml      # Ingress configuration
│           ├── configmap.yaml    # Configuration
│           ├── secret.yaml       # Secrets template
│           ├── serviceaccount.yaml # Service account
│           ├── hpa.yaml          # Horizontal Pod Autoscaler
│           ├── pvc.yaml          # Persistent volume claims
│           └── servicemonitor.yaml # Prometheus monitoring
│
├── k8s/
│   ├── base/                     # Base Kustomize manifests
│   │   ├── kustomization.yaml    # Base configuration
│   │   ├── configmap.yaml        # Environment variables
│   │   ├── secret.yaml           # Secret templates
│   │   ├── rbac.yaml             # RBAC configuration
│   │   ├── api-deployment.yaml   # API deployment
│   │   ├── redis-deployment.yaml # Redis deployment
│   │   ├── ingress.yaml          # Ingress
│   │   ├── api-networkpolicy.yaml # API network policy
│   │   └── redis-networkpolicy.yaml # Redis network policy
│   │
│   └── overlays/                 # Environment-specific configs
│       ├── development/          # Dev environment
│       │   ├── kustomization.yaml
│       │   └── namespace.yaml
│       ├── staging/              # Staging environment
│       │   ├── kustomization.yaml
│       │   └── namespace.yaml
│       └── production/           # Production environment
│           ├── kustomization.yaml
│           ├── namespace.yaml
│           ├── hpa.yaml          # Auto-scaling
│           └── pdb.yaml          # Pod disruption budget
│
└── docs/
    ├── HELM_DEPLOYMENT.md        # Comprehensive Helm guide
    └── KUBERNETES_DEPLOYMENT.md  # Comprehensive K8s guide
```

## Security Features

All deployments include:

- ✅ **Non-root containers**: All pods run as user 1000
- ✅ **Read-only root filesystem**: Where possible
- ✅ **Dropped capabilities**: ALL capabilities dropped by default
- ✅ **NetworkPolicies**: Service-to-service communication restricted
- ✅ **RBAC**: Least privilege access (no cluster-wide permissions)
- ✅ **Secret management**: Externalized, never committed to git
- ✅ **SecurityContext**: Comprehensive pod and container security

## Reliability Features

All deployments include:

- ✅ **Startup probe**: 90-second timeout for agent loading
- ✅ **Liveness probe**: Detect and restart crashed pods
- ✅ **Readiness probe**: Control traffic routing
- ✅ **Resource limits**: Prevent resource exhaustion
- ✅ **HPA** (production): Auto-scaling based on CPU/memory
- ✅ **PDB** (production): Maintain availability during updates
- ✅ **Rolling updates**: Zero-downtime deployments

## Environment Configurations

| Feature | Development | Staging | Production |
|---------|-------------|---------|------------|
| Namespace | codefactory-dev | codefactory-staging | codefactory-production |
| Replicas | 1 | 2 | 3 (HPA: 3-10) |
| CPU Request | 250m | 500m | 500m |
| CPU Limit | 1 | 1.5 | 2 |
| Memory Request | 512Mi | 1Gi | 1Gi |
| Memory Limit | 2Gi | 3Gi | 4Gi |
| Log Level | DEBUG | INFO | INFO |
| Worker Count | 2 | 4 | 8 |
| Auto-scaling | No | No | Yes |
| PDB | No | No | Yes |

## Prerequisites

- Kubernetes 1.23+
- Helm 3.8+
- kubectl with Kustomize support
- StorageClass with ReadWriteMany (for multi-replica)
- Ingress controller (optional)
- Cert Manager (optional, for TLS)

## Required Secrets

Before deploying, create secrets:

```bash
# Generate strong secrets
REDIS_PASSWORD=$(openssl rand -base64 32)
HMAC_KEY=$(openssl rand -hex 32)
ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")

# Create secret
kubectl create secret generic codefactory-secrets \
  --from-literal=redis-password="$REDIS_PASSWORD" \
  --from-literal=openai-api-key="sk-YOUR-KEY" \
  --from-literal=hmac-key="$HMAC_KEY" \
  --from-literal=encryption-key="$ENCRYPTION_KEY" \
  -n codefactory
```

## Monitoring

Access metrics:

```bash
# Port-forward to metrics endpoint
kubectl port-forward svc/codefactory-api 9090:9090 -n codefactory

# View metrics
curl http://localhost:9090/metrics
```

## Troubleshooting

```bash
# View pod status
kubectl get pods -n codefactory

# View logs
kubectl logs -f -l app=codefactory-api -n codefactory

# Describe pod
kubectl describe pod <pod-name> -n codefactory

# Check events
kubectl get events -n codefactory --sort-by='.lastTimestamp'

# Port-forward for local testing
kubectl port-forward svc/codefactory-api 8000:80 -n codefactory
```

## Documentation

- **Helm Guide**: `docs/HELM_DEPLOYMENT.md` - Complete Helm deployment guide
- **Kubernetes Guide**: `docs/KUBERNETES_DEPLOYMENT.md` - Complete Kubernetes/Kustomize guide
- **Chart README**: `helm/codefactory/README.md` - Helm chart documentation

## Support

For issues:
- GitHub Issues: https://github.com/musicmonk42/The_Code_Factory_Working_V2/issues
- Documentation: See guides above
