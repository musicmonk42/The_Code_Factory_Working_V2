# Code Factory Helm Chart

Official Helm chart for deploying The Code Factory - an AI-powered code generation platform.

## TL;DR

```bash
# Create secrets first
kubectl create namespace codefactory
kubectl create secret generic codefactory-secrets \
  --from-literal=redis-password=$(openssl rand -base64 32) \
  --from-literal=openai-api-key=sk-YOUR-KEY \
  --from-literal=hmac-key=$(openssl rand -hex 32) \
  --from-literal=encryption-key=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())") \
  -n codefactory

# Install the chart
helm install codefactory ./helm/codefactory --namespace codefactory
```

## Introduction

This chart bootstraps a Code Factory deployment on a Kubernetes cluster using the Helm package manager.

## Prerequisites

- Kubernetes 1.23+
- Helm 3.8+
- PV provisioner support in the underlying infrastructure (for persistence)
- StorageClass with ReadWriteMany support (for multi-replica deployments)

## Installing the Chart

To install the chart with the release name `codefactory`:

```bash
helm install codefactory ./helm/codefactory \
  --namespace codefactory \
  --create-namespace
```

The command deploys Code Factory on the Kubernetes cluster in the default configuration. The [Parameters](#parameters) section lists the parameters that can be configured during installation.

## Uninstalling the Chart

To uninstall/delete the `codefactory` deployment:

```bash
helm uninstall codefactory -n codefactory
```

The command removes all the Kubernetes components associated with the chart and deletes the release.

## Parameters

### Global Parameters

| Name                      | Description                                          | Value                                  |
|---------------------------|------------------------------------------------------|----------------------------------------|
| `nameOverride`            | String to partially override codefactory.fullname    | `""`                                   |
| `fullnameOverride`        | String to fully override codefactory.fullname        | `""`                                   |

### Image Parameters

| Name                | Description                                                  | Value                                 |
|---------------------|--------------------------------------------------------------|---------------------------------------|
| `image.repository`  | Code Factory image repository                                | `ghcr.io/musicmonk42/codefactory`     |
| `image.pullPolicy`  | Code Factory image pull policy                               | `IfNotPresent`                        |
| `image.tag`         | Overrides the image tag (default is the chart appVersion)    | `""`                                  |
| `imagePullSecrets`  | Specify docker-registry secret names as an array             | `[]`                                  |

### Deployment Parameters

| Name                                    | Description                                                      | Value       |
|-----------------------------------------|------------------------------------------------------------------|-------------|
| `replicaCount`                          | Number of Code Factory replicas to deploy                        | `1`         |
| `podAnnotations`                        | Annotations for Code Factory pods                                | `{}`        |
| `podLabels`                             | Extra labels for Code Factory pods                               | `{}`        |
| `podSecurityContext.runAsNonRoot`       | Set pod's security context runAsNonRoot                          | `true`      |
| `podSecurityContext.runAsUser`          | Set pod's security context runAsUser                             | `1000`      |
| `podSecurityContext.fsGroup`            | Set pod's security context fsGroup                               | `1000`      |
| `securityContext.allowPrivilegeEscalation` | Set container's security context allowPrivilegeEscalation     | `false`     |
| `securityContext.readOnlyRootFilesystem`| Set container's security context readOnlyRootFilesystem          | `false`     |
| `securityContext.runAsNonRoot`          | Set container's security context runAsNonRoot                    | `true`      |
| `securityContext.runAsUser`             | Set container's security context runAsUser                       | `1000`      |

### Service Parameters

| Name                | Description                                  | Value         |
|---------------------|----------------------------------------------|---------------|
| `service.type`      | Code Factory service type                    | `ClusterIP`   |
| `service.port`      | Code Factory service HTTP port               | `80`          |
| `service.metricsPort` | Code Factory service metrics port          | `9090`        |

### Ingress Parameters

| Name                       | Description                                              | Value                  |
|----------------------------|----------------------------------------------------------|------------------------|
| `ingress.enabled`          | Enable ingress record generation                         | `false`                |
| `ingress.className`        | IngressClass that will be used                           | `nginx`                |
| `ingress.annotations`      | Additional annotations for the Ingress resource          | `{}`                   |
| `ingress.hosts[0].host`    | Default host for the ingress record                      | `codefactory.example.com` |
| `ingress.hosts[0].paths[0].path` | Default path for the ingress record            | `/`                    |
| `ingress.tls`              | Enable TLS configuration                                 | `[]`                   |

### Resource Limits

| Name                        | Description                            | Value     |
|-----------------------------|----------------------------------------|-----------|
| `resources.limits.cpu`      | The CPU limit                          | `2000m`   |
| `resources.limits.memory`   | The memory limit                       | `4Gi`     |
| `resources.requests.cpu`    | The requested CPU                      | `500m`    |
| `resources.requests.memory` | The requested memory                   | `1Gi`     |

### Autoscaling Parameters

| Name                                            | Description                                         | Value   |
|-------------------------------------------------|-----------------------------------------------------|---------|
| `autoscaling.enabled`                           | Enable Horizontal Pod Autoscaler                    | `false` |
| `autoscaling.minReplicas`                       | Minimum number of replicas                          | `1`     |
| `autoscaling.maxReplicas`                       | Maximum number of replicas                          | `10`    |
| `autoscaling.targetCPUUtilizationPercentage`    | Target CPU utilization percentage                   | `70`    |
| `autoscaling.targetMemoryUtilizationPercentage` | Target Memory utilization percentage                | `80`    |

### Persistence Parameters

| Name                              | Description                                      | Value           |
|-----------------------------------|--------------------------------------------------|-----------------|
| `persistence.uploads.enabled`     | Enable persistence for uploads                   | `true`          |
| `persistence.uploads.storageClass`| Storage class for uploads PVC                    | `""`            |
| `persistence.uploads.accessMode`  | Access mode for uploads PVC                      | `ReadWriteMany` |
| `persistence.uploads.size`        | Size of uploads PVC                              | `10Gi`          |
| `persistence.workspace.enabled`   | Enable persistence for workspace                 | `true`          |
| `persistence.workspace.storageClass` | Storage class for workspace PVC               | `""`            |
| `persistence.workspace.accessMode`| Access mode for workspace PVC                    | `ReadWriteMany` |
| `persistence.workspace.size`      | Size of workspace PVC                            | `5Gi`           |

### Monitoring Parameters

| Name                                      | Description                              | Value    |
|-------------------------------------------|------------------------------------------|----------|
| `monitoring.serviceMonitor.enabled`       | Create ServiceMonitor for Prometheus     | `false`  |
| `monitoring.serviceMonitor.interval`      | Scrape interval                          | `30s`    |
| `monitoring.serviceMonitor.scrapeTimeout` | Scrape timeout                           | `10s`    |

## Configuration and Installation Details

### Secrets Management

The chart requires several secrets to be created before installation:

```bash
kubectl create secret generic codefactory-secrets \
  --from-literal=redis-password=$(openssl rand -base64 32) \
  --from-literal=openai-api-key=sk-YOUR-KEY \
  --from-literal=anthropic-api-key=sk-ant-YOUR-KEY \
  --from-literal=hmac-key=$(openssl rand -hex 32) \
  --from-literal=encryption-key=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())") \
  --from-literal=database-url=postgresql://user:pass@host:5432/db \
  -n codefactory
```

For production, use external secrets management:
- AWS Secrets Manager
- HashiCorp Vault
- Azure Key Vault
- External Secrets Operator

### Custom Values File

Create a `values-custom.yaml` file:

```yaml
image:
  tag: "v1.2.3"

replicaCount: 3

autoscaling:
  enabled: true
  minReplicas: 3
  maxReplicas: 10

ingress:
  enabled: true
  hosts:
    - host: codefactory.yourdomain.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: codefactory-tls
      hosts:
        - codefactory.yourdomain.com
```

Install with custom values:

```bash
helm install codefactory ./helm/codefactory \
  --namespace codefactory \
  --values values-custom.yaml
```

### Upgrading

To upgrade the release:

```bash
helm upgrade codefactory ./helm/codefactory \
  --namespace codefactory \
  --values values-custom.yaml
```

### Rollback

To rollback to a previous version:

```bash
helm rollback codefactory -n codefactory
```

## Troubleshooting

### View Logs

```bash
kubectl logs -f -l app.kubernetes.io/name=codefactory -n codefactory
```

### Check Pod Status

```bash
kubectl get pods -n codefactory
kubectl describe pod <pod-name> -n codefactory
```

### Port Forward for Testing

```bash
kubectl port-forward svc/codefactory 8000:80 -n codefactory
```

## License

MIT

## Support

For issues and questions:
- GitHub Issues: https://github.com/musicmonk42/The_Code_Factory_Working_V2/issues
- Documentation: docs/HELM_DEPLOYMENT.md
