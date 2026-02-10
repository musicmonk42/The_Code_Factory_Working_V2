# Kubernetes Database Migrations Guide

This guide explains how to run database migrations for the Code Factory platform deployed on Kubernetes.

## Overview

Database migrations are managed using Alembic and can be run in several ways:
1. **Pre-deployment Job**: Run migrations before deploying the application (recommended for production)
2. **Manual Job**: Trigger migrations on-demand
3. **Init Container**: Run migrations as part of pod startup (available in Helm chart)

## Prerequisites

- Kubernetes cluster with kubectl access
- Database connection configured in secrets
- Alembic migrations initialized in the repository

## Quick Start

### 1. Apply Migration Job

The migration job is included in the base manifests:

```bash
# Deploy all resources including migration job
kubectl apply -k k8s/overlays/production

# Or apply just the migration job
kubectl apply -f k8s/base/migration-job.yaml
```

### 2. Monitor Migration Progress

```bash
# Watch job status
kubectl get jobs -n codefactory -w

# View migration logs
kubectl logs -f job/codefactory-migrations -n codefactory

# Check pod status
kubectl get pods -n codefactory | grep migrations
```

### 3. Verify Migration Success

```bash
# Check if job completed successfully
kubectl get job codefactory-migrations -n codefactory

# Expected output:
# NAME                       COMPLETIONS   DURATION   AGE
# codefactory-migrations     1/1           45s        2m
```

## Deployment Workflows

### Production Deployment (Recommended)

Run migrations as a separate step before deploying the application:

```bash
# Step 1: Apply migration job
kubectl apply -f k8s/base/migration-job.yaml

# Step 2: Wait for completion
kubectl wait --for=condition=complete --timeout=300s job/codefactory-migrations -n codefactory

# Step 3: Deploy application
kubectl apply -k k8s/overlays/production
```

### Development Deployment

For development, you can run migrations and deploy in one command:

```bash
kubectl apply -k k8s/overlays/development
```

The migration job will run automatically before the API pods start.

## Manual Migration Commands

### Run Migrations Manually

```bash
# Create a one-time migration pod
kubectl run migrations-manual \
  --image=ghcr.io/musicmonk42/codefactory:latest \
  --restart=Never \
  --rm -it \
  --namespace=codefactory \
  --env="DB_PATH=$(kubectl get secret codefactory-secrets -n codefactory -o jsonpath='{.data.database-url}' | base64 -d)" \
  -- /bin/sh -c "cd /app && alembic upgrade head"
```

### Check Migration Status

```bash
# Get current migration version
kubectl run check-migrations \
  --image=ghcr.io/musicmonk42/codefactory:latest \
  --restart=Never \
  --rm -it \
  --namespace=codefactory \
  --env="DB_PATH=$(kubectl get secret codefactory-secrets -n codefactory -o jsonpath='{.data.database-url}' | base64 -d)" \
  -- /bin/sh -c "cd /app && alembic current"
```

### View Migration History

```bash
# Show migration history
kubectl run history-migrations \
  --image=ghcr.io/musicmonk42/codefactory:latest \
  --restart=Never \
  --rm -it \
  --namespace=codefactory \
  --env="DB_PATH=$(kubectl get secret codefactory-secrets -n codefactory -o jsonpath='{.data.database-url}' | base64 -d)" \
  -- /bin/sh -c "cd /app && alembic history"
```

## Configuration

### Database Connection

The migration job uses the database URL from the `codefactory-secrets` secret:

```bash
# Create or update database secret
kubectl create secret generic codefactory-secrets \
  --from-literal=database-url="postgresql+asyncpg://user:pass@postgres:5432/codefactory" \
  --namespace=codefactory \
  --dry-run=client -o yaml | kubectl apply -f -
```

### Enable Citus Support

For PostgreSQL with Citus extension:

1. Update the ConfigMap:
   ```bash
   kubectl patch configmap codefactory-config -n codefactory \
     -p '{"data":{"ENABLE_CITUS":"1"}}'
   ```

2. Ensure your PostgreSQL deployment uses the Citus image:
   ```yaml
   image: citusdata/citus:12.1
   ```

## Troubleshooting

### Migration Job Failed

```bash
# View job details
kubectl describe job codefactory-migrations -n codefactory

# View pod logs for failed jobs
kubectl logs job/codefactory-migrations -n codefactory

# Check recent events
kubectl get events -n codefactory --sort-by='.lastTimestamp' | grep migrations
```

### Database Connection Issues

```bash
# Verify database secret exists
kubectl get secret codefactory-secrets -n codefactory

# Test database connectivity
kubectl run db-test \
  --image=postgres:15 \
  --restart=Never \
  --rm -it \
  --namespace=codefactory \
  -- psql "$(kubectl get secret codefactory-secrets -n codefactory -o jsonpath='{.data.database-url}' | base64 -d)" -c "SELECT version();"
```

### Migration Conflicts

If multiple migrations were created in different branches:

```bash
# Connect to a pod with shell access
kubectl exec -it deployment/codefactory-api -n codefactory -- bash

# Inside the pod, merge migration heads
cd /app
alembic merge heads -m "Merge migration branches"
alembic upgrade head
```

### Rollback Migration

To rollback a migration (use with caution):

```bash
kubectl run rollback-migration \
  --image=ghcr.io/musicmonk42/codefactory:latest \
  --restart=Never \
  --rm -it \
  --namespace=codefactory \
  --env="DB_PATH=$(kubectl get secret codefactory-secrets -n codefactory -o jsonpath='{.data.database-url}' | base64 -d)" \
  -- /bin/sh -c "cd /app && alembic downgrade -1"
```

## Best Practices

1. **Always test migrations in development first**
   ```bash
   kubectl apply -k k8s/overlays/development
   ```

2. **Run migrations as a separate job in production**
   - Allows validation before deploying new application version
   - Easier to troubleshoot if migrations fail
   - Can be integrated into CI/CD pipelines

3. **Monitor migration jobs**
   - Set up alerts for failed migration jobs
   - Keep job history for debugging (ttlSecondsAfterFinished: 300)

4. **Backup database before migrations**
   ```bash
   kubectl exec -it deployment/postgres -n codefactory -- \
     pg_dump -U codefactory codefactory > backup-$(date +%Y%m%d).sql
   ```

5. **Use Helm for complex deployments**
   - Helm provides better control over migration timing
   - Supports pre/post-install hooks
   - See `helm/codefactory/README.md` for details

## CI/CD Integration

### GitLab CI Example

```yaml
deploy:production:
  stage: deploy
  script:
    # Run migrations
    - kubectl apply -f k8s/base/migration-job.yaml
    - kubectl wait --for=condition=complete --timeout=300s job/codefactory-migrations -n codefactory
    
    # Deploy application
    - kubectl apply -k k8s/overlays/production
    
    # Verify deployment
    - kubectl rollout status deployment/codefactory-api -n codefactory
  only:
    - main
```

### GitHub Actions Example

```yaml
- name: Run Database Migrations
  run: |
    kubectl apply -f k8s/base/migration-job.yaml
    kubectl wait --for=condition=complete --timeout=300s job/codefactory-migrations -n codefactory

- name: Deploy Application
  run: |
    kubectl apply -k k8s/overlays/production
    kubectl rollout status deployment/codefactory-api -n codefactory
```

## Related Documentation

- [Alembic Migrations README](../../omnicore_engine/migrations/README.md) - Detailed migration documentation
- [Helm Deployment Guide](../../helm/codefactory/README.md) - Helm-based migration strategies
- [DEPLOYMENT.md](../../DEPLOYMENT.md) - General deployment guide
