# Rollback Procedures

This document outlines procedures for rolling back deployments and changes in the Code Factory platform.

## Table of Contents

- [Overview](#overview)
- [Kubernetes Rollbacks](#kubernetes-rollbacks)
- [Database Rollbacks](#database-rollbacks)
- [Configuration Rollbacks](#configuration-rollbacks)
- [Code Rollbacks](#code-rollbacks)
- [Emergency Procedures](#emergency-procedures)

## Overview

### When to Rollback

Rollback when:
- New deployment causes service degradation
- Error rates spike above acceptable levels
- Critical bugs discovered in production
- Performance significantly degrades
- Security vulnerability introduced

### Rollback Decision Matrix

| Severity | Error Rate | Latency Increase | Decision | Response Time |
|----------|-----------|------------------|----------|---------------|
| Critical | > 5% | > 100% | Immediate rollback | < 5 minutes |
| High | > 2% | > 50% | Rollback after quick fix attempt | < 15 minutes |
| Medium | > 1% | > 25% | Investigate, rollback if no quick fix | < 30 minutes |
| Low | < 1% | < 25% | Monitor, rollback if worsens | < 1 hour |

## Kubernetes Rollbacks

### Quick Rollback (Recommended)

**Rollback to previous deployment:**
```bash
# Check rollout history
kubectl rollout history deployment/code-factory -n production

# Rollback to previous version
kubectl rollout undo deployment/code-factory -n production

# Check status
kubectl rollout status deployment/code-factory -n production

# Verify pods are running
kubectl get pods -n production -l app=code-factory
```

**Rollback to specific revision:**
```bash
# List revisions
kubectl rollout history deployment/code-factory -n production

# View specific revision
kubectl rollout history deployment/code-factory -n production --revision=3

# Rollback to specific revision
kubectl rollout undo deployment/code-factory -n production --to-revision=3
```

### Complete Rollback Script

**File: `scripts/rollback_deployment.sh`**
```bash
#!/bin/bash
# Rollback deployment with validation

set -e

NAMESPACE="${NAMESPACE:-production}"
DEPLOYMENT="${DEPLOYMENT:-code-factory}"
TO_REVISION="${1:-}"

echo "=== Code Factory Rollback Procedure ==="
echo "Namespace: $NAMESPACE"
echo "Deployment: $DEPLOYMENT"

# Check current status
echo "Current status:"
kubectl get deployment $DEPLOYMENT -n $NAMESPACE

# Show rollout history
echo -e "\nRollout history:"
kubectl rollout history deployment/$DEPLOYMENT -n $NAMESPACE

# Confirm rollback
if [ -z "$TO_REVISION" ]; then
  read -p "Rollback to previous version? (yes/no): " CONFIRM
  ROLLBACK_CMD="kubectl rollout undo deployment/$DEPLOYMENT -n $NAMESPACE"
else
  read -p "Rollback to revision $TO_REVISION? (yes/no): " CONFIRM
  ROLLBACK_CMD="kubectl rollout undo deployment/$DEPLOYMENT -n $NAMESPACE --to-revision=$TO_REVISION"
fi

if [ "$CONFIRM" != "yes" ]; then
  echo "Rollback cancelled"
  exit 0
fi

# Perform rollback
echo -e "\nPerforming rollback..."
$ROLLBACK_CMD

# Wait for rollout to complete
echo "Waiting for rollout to complete..."
kubectl rollout status deployment/$DEPLOYMENT -n $NAMESPACE --timeout=5m

# Verify pods
echo -e "\nVerifying pods:"
kubectl get pods -n $NAMESPACE -l app=$DEPLOYMENT

# Health check
echo -e "\nRunning health check..."
POD_NAME=$(kubectl get pods -n $NAMESPACE -l app=$DEPLOYMENT -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n $NAMESPACE $POD_NAME -- curl -s http://localhost:8000/health | jq .

echo -e "\n✓ Rollback completed successfully"

# Send notification
curl -X POST \
  -H "Content-Type: application/json" \
  -d "{\"text\":\"Deployment $DEPLOYMENT rolled back in $NAMESPACE\"}" \
  ${SLACK_WEBHOOK_URL}
```

### Helm Rollback

If using Helm:
```bash
# List releases
helm list -n production

# Show history
helm history code-factory -n production

# Rollback to previous release
helm rollback code-factory -n production

# Rollback to specific revision
helm rollback code-factory 3 -n production

# Verify
helm status code-factory -n production
```

## Database Rollbacks

### Migration Rollbacks

**Alembic (SQLAlchemy):**
```bash
# Show current revision
alembic current

# Show migration history
alembic history

# Downgrade one revision
alembic downgrade -1

# Downgrade to specific revision
alembic downgrade abc123

# Downgrade all
alembic downgrade base
```

**Django:**
```bash
# Show migrations
python manage.py showmigrations

# Rollback app to specific migration
python manage.py migrate myapp 0003_previous_migration

# Rollback all migrations for an app
python manage.py migrate myapp zero
```

### Data Rollback from Backup

**Restore from PITR backup:**
```bash
#!/bin/bash
# Restore database to point in time

TARGET_TIME="2025-11-22 14:30:00"
BACKUP_HOST="backup-db.example.com"

# Stop application
kubectl scale deployment code-factory -n production --replicas=0

# Stop database
kubectl scale statefulset postgresql -n production --replicas=0

# Restore using pg_basebackup + WAL replay
pg_basebackup -h $BACKUP_HOST -D /var/lib/postgresql/data -U replication

# Configure recovery
cat > /var/lib/postgresql/data/recovery.conf <<EOF
restore_command = 'aws s3 cp s3://backups/wal/%f %p'
recovery_target_time = '$TARGET_TIME'
recovery_target_action = 'promote'
EOF

# Start database
kubectl scale statefulset postgresql -n production --replicas=1

# Wait for database to be ready
while ! pg_isready -h postgresql -p 5432; do
  sleep 2
done

# Verify data
psql -h postgresql -U admin -d codefactory -c "SELECT NOW()"

# Start application
kubectl scale deployment code-factory -n production --replicas=3

echo "Database restored to $TARGET_TIME"
```

### Schema-Only Rollback

If only schema changed (no data):
```bash
# Dump schema from backup
pg_dump -h backup-db -U admin -d codefactory --schema-only > schema_backup.sql

# Restore schema (be careful - this drops existing schema)
psql -h localhost -U admin -d codefactory < schema_backup.sql
```

## Configuration Rollbacks

### ConfigMap/Secret Rollback

**Kubernetes ConfigMaps:**
```bash
# View previous ConfigMap versions (if versioned)
kubectl get configmaps -n production | grep code-factory-config

# Restore from backup
kubectl apply -f backups/configmap-backup-20251122.yaml -n production

# Restart pods to pick up new config
kubectl rollout restart deployment/code-factory -n production
```

**Environment Variables:**
```bash
# Edit deployment with previous values
kubectl edit deployment code-factory -n production

# Or patch specific values
kubectl patch deployment code-factory -n production \
  -p '{"spec":{"template":{"spec":{"containers":[{"name":"code-factory","env":[{"name":"DEBUG","value":"false"}]}]}}}}'

# Restart to apply
kubectl rollout restart deployment/code-factory -n production
```

### Feature Flag Rollback

**Disable problematic feature:**
```bash
# Via API (if feature flag service available)
curl -X POST https://api.example.com/admin/features/disable \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -d '{"feature": "new_ai_model"}'

# Via database (direct)
psql -h postgresql -U admin -d codefactory <<EOF
UPDATE feature_flags 
SET enabled = false 
WHERE name = 'new_ai_model';
EOF

# Via ConfigMap
kubectl patch configmap code-factory-config -n production \
  --type merge \
  -p '{"data":{"FEATURE_NEW_AI_MODEL":"false"}}'
kubectl rollout restart deployment/code-factory -n production
```

## Code Rollbacks

### Git Rollback

**Revert specific commit:**
```bash
# Revert last commit (creates new commit)
git revert HEAD
git push origin main

# Revert specific commit
git revert abc123
git push origin main

# Revert multiple commits
git revert abc123..def456
git push origin main
```

**Reset to previous state (use with caution):**
```bash
# Create backup branch first
git checkout -b backup-before-reset

# Reset to previous commit (soft - keeps changes)
git reset --soft HEAD~1

# Reset to previous commit (hard - discards changes)
git reset --hard HEAD~1

# Force push (only if safe to do so)
git push --force origin main
```

### Docker Image Rollback

**Pull previous image:**
```bash
# List available tags
docker images code-factory

# Tag previous version
docker tag code-factory:v1.2.3-prev code-factory:latest

# Push updated tag
docker push code-factory:latest

# Or update Kubernetes to use specific tag
kubectl set image deployment/code-factory \
  code-factory=code-factory:v1.2.3 \
  -n production
```

## Emergency Procedures

### Emergency Stop

**Stop all services immediately:**
```bash
#!/bin/bash
# Emergency stop script

echo "EMERGENCY STOP INITIATED"

# Scale down all deployments
kubectl scale deployment --all --replicas=0 -n production

# Update status page
curl -X POST https://status.example.com/api/incidents \
  -d '{"status":"major_outage","message":"System maintenance in progress"}'

# Notify team
curl -X POST $SLACK_WEBHOOK \
  -d '{"text":"🚨 EMERGENCY STOP: All services scaled down"}'

echo "All services stopped. System in maintenance mode."
```

### Traffic Diversion

**Route traffic away from bad deployment:**
```bash
# Update Ingress to route to stable service
kubectl patch ingress code-factory-ingress -n production \
  --type merge \
  -p '{"spec":{"rules":[{"http":{"paths":[{"backend":{"service":{"name":"code-factory-stable"}}}]}}]}}'

# Or update DNS
aws route53 change-resource-record-sets \
  --hosted-zone-id Z1234567890ABC \
  --change-batch file://dns-failover.json
```

### Gradual Rollback

**Roll back gradually using canary:**
```bash
# Reduce new version to 0%
kubectl patch deployment code-factory-canary -n production \
  -p '{"spec":{"replicas":0}}'

# Increase stable version
kubectl scale deployment code-factory-stable -n production --replicas=10

# Monitor
watch kubectl get pods -n production
```

## Rollback Validation

### Post-Rollback Checks

**Automated validation script:**
```bash
#!/bin/bash
# Validate rollback was successful

ERRORS=0

# 1. Check pod status
echo "Checking pod status..."
READY_PODS=$(kubectl get pods -n production -l app=code-factory -o json | jq '[.items[] | select(.status.phase=="Running")] | length')
DESIRED_PODS=$(kubectl get deployment code-factory -n production -o json | jq '.spec.replicas')

if [ "$READY_PODS" -ne "$DESIRED_PODS" ]; then
  echo "✗ Pod count mismatch: $READY_PODS ready, $DESIRED_PODS desired"
  ERRORS=$((ERRORS + 1))
else
  echo "✓ All pods running"
fi

# 2. Health check
echo "Checking health endpoint..."
HEALTH=$(curl -s http://code-factory.production.svc.cluster.local:8000/health | jq -r '.status')
if [ "$HEALTH" != "healthy" ]; then
  echo "✗ Health check failed: $HEALTH"
  ERRORS=$((ERRORS + 1))
else
  echo "✓ Health check passed"
fi

# 3. Error rate check
echo "Checking error rates..."
ERROR_RATE=$(curl -s http://prometheus:9090/api/v1/query?query=rate\(http_requests_total{status=~"5.."}[5m]\)/rate\(http_requests_total[5m]\) | jq -r '.data.result[0].value[1]')
if (( $(echo "$ERROR_RATE > 0.01" | bc -l) )); then
  echo "✗ High error rate: $ERROR_RATE"
  ERRORS=$((ERRORS + 1))
else
  echo "✓ Error rate normal: $ERROR_RATE"
fi

# 4. Response time check
echo "Checking response times..."
P95_LATENCY=$(curl -s http://prometheus:9090/api/v1/query?query=histogram_quantile\(0.95,rate\(http_request_duration_seconds_bucket[5m]\)\) | jq -r '.data.result[0].value[1]')
if (( $(echo "$P95_LATENCY > 1" | bc -l) )); then
  echo "⚠ High latency: ${P95_LATENCY}s"
else
  echo "✓ Latency normal: ${P95_LATENCY}s"
fi

# Summary
if [ $ERRORS -gt 0 ]; then
  echo -e "\n✗ Rollback validation FAILED with $ERRORS errors"
  exit 1
else
  echo -e "\n✓ Rollback validation PASSED"
  exit 0
fi
```

### Smoke Tests

**Run smoke tests after rollback:**
```bash
# Run integration tests
python run_integration_tests.py --suite smoke

# Run API tests
pytest tests/api/ -v -m smoke

# Check key metrics
curl http://localhost:8000/metrics | grep http_requests_total
```

## Communication

### Rollback Notification Template

**Internal (Slack):**
```markdown
🔄 **Deployment Rollback**

**Service:** Code Factory Production
**Action:** Rolled back deployment
**Reason:** [High error rate / Performance degradation / Bug discovered]
**From Version:** v1.2.4
**To Version:** v1.2.3
**Status:** [In Progress / Completed / Validated]
**Impact:** [None / Minimal / Moderate]
**Next Steps:** [Investigate root cause / Deploy fix / Monitor]

Incident Channel: #incident-20251122-rollback
```

**External (Status Page):**
```markdown
**Update - Service Restored**

We have rolled back a recent deployment that was causing [issue description]. 
Service has been restored and is operating normally.

**Status:** Operational
**Duration:** [Start time] to [End time] ([Duration] minutes)
**Impact:** [Brief description]

We apologize for any inconvenience.
```

## Prevention

### Pre-Deployment Checklist

To avoid needing rollbacks:
- [ ] All tests passing in CI
- [ ] Code review completed
- [ ] Load testing performed
- [ ] Canary deployment tested
- [ ] Rollback plan documented
- [ ] Monitoring alerts configured
- [ ] Feature flags ready to toggle

### Gradual Rollout Strategy

**Use canary deployments:**
```yaml
# canary-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: code-factory-canary
spec:
  replicas: 1  # Start with 1 pod
  selector:
    matchLabels:
      app: code-factory
      version: canary
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: code-factory-stable
spec:
  replicas: 9  # 90% traffic
  selector:
    matchLabels:
      app: code-factory
      version: stable
```

**Gradual traffic shift:**
```bash
# 10% canary
kubectl scale deployment code-factory-canary -n production --replicas=1
kubectl scale deployment code-factory-stable -n production --replicas=9

# Monitor for 15 minutes...

# 50% canary
kubectl scale deployment code-factory-canary -n production --replicas=5
kubectl scale deployment code-factory-stable -n production --replicas=5

# Monitor for 15 minutes...

# 100% canary (becomes new stable)
kubectl scale deployment code-factory-canary -n production --replicas=10
kubectl scale deployment code-factory-stable -n production --replicas=0
```

---

**Document Version:** 1.0.0  
**Last Updated:** 2025-11-22  
**Owner:** DevOps Team
