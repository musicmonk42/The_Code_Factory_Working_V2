# Production Deployment Checklist

This checklist ensures all production readiness tasks are complete before deploying to production.

**Date:** _______________  
**Deployer:** _______________  
**Version:** _______________  
**Target Environment:** Production

## Pre-Deployment Validation

### Code & Build
- [ ] All unit tests passing
- [ ] All integration tests passing
- [ ] Code review completed and approved
- [ ] No high/critical security vulnerabilities
- [ ] Build successful in staging environment
- [ ] Docker images built and tagged
- [ ] Version numbers updated

### Security
- [ ] **CRITICAL**: Private keys rotated (if exposed in history)
- [ ] Secrets stored in secrets manager (AWS/Vault/GCP/Azure)
- [ ] No hardcoded credentials in code
- [ ] Security scanning completed (Bandit, Safety, CodeQL)
- [ ] SSL/TLS certificates installed
- [ ] CORS policies configured for production origins
- [ ] Security headers configured
- [ ] Rate limiting enabled

### Infrastructure
- [ ] Production Kubernetes cluster ready
- [ ] Database provisioned (PostgreSQL recommended)
- [ ] Redis cluster configured
- [ ] S3/storage buckets created
- [ ] DNS records configured
- [ ] Load balancer configured
- [ ] CDN configured (if applicable)
- [ ] VPC/Network security groups configured

### Monitoring & Observability
- [ ] Prometheus deployed and configured
- [ ] Grafana deployed with dashboards
- [ ] Alertmanager configured with notification channels
- [ ] Alert rules deployed and tested
- [ ] Log aggregation configured (ELK/Splunk/CloudWatch)
- [ ] APM/tracing configured (if using)
- [ ] Status page configured

### Data & Backup
- [ ] Database backup enabled
- [ ] Backup retention policy configured
- [ ] Point-in-time recovery enabled
- [ ] Backup verification tested
- [ ] Disaster recovery plan documented
- [ ] Cross-region replication configured (if required)

### Configuration
- [ ] Production environment variables configured
- [ ] Secrets provider configured (`SECRETS_PROVIDER=aws|vault|gcp|azure`)
- [ ] Feature flags configured
- [ ] Resource limits set (CPU, memory, disk)
- [ ] Auto-scaling policies configured
- [ ] Health check endpoints verified

### Testing
- [ ] Smoke tests passed in staging
- [ ] Load testing completed with acceptable results
- [ ] Performance benchmarks within targets
- [ ] Failover testing completed
- [ ] Integration tests passed
- [ ] User acceptance testing completed

### Documentation
- [ ] Deployment runbook updated
- [ ] Rollback procedures verified
- [ ] Incident response contacts updated
- [ ] On-call rotation configured
- [ ] API documentation published
- [ ] User documentation updated

### Compliance
- [ ] Audit logging enabled
- [ ] Data retention policies configured
- [ ] GDPR compliance verified (if applicable)
- [ ] SOC2 requirements met (if applicable)
- [ ] Compliance reports generated

## Deployment Steps

### Step 1: Pre-Deployment Communication
- [ ] Notify stakeholders of deployment window
- [ ] Update status page (scheduled maintenance)
- [ ] Inform customer support team
- [ ] Schedule deployment during low-traffic window

### Step 2: Backup Current State
```bash
# Backup database
pg_dump -h production-db -U admin codefactory_prod > backup_pre_deploy_$(date +%Y%m%d_%H%M%S).sql

# Backup configurations
kubectl get all -n production -o yaml > backup_k8s_$(date +%Y%m%d_%H%M%S).yaml

# Tag current deployment
git tag -a v1.0.0-pre-deploy -m "Pre-deployment backup"
git push origin v1.0.0-pre-deploy
```
- [ ] Database backup completed
- [ ] Configuration backup completed
- [ ] Git tag created

### Step 3: Deploy Infrastructure
```bash
# Deploy monitoring stack
kubectl apply -f monitoring/

# Deploy secrets
kubectl apply -f secrets/

# Deploy ConfigMaps
kubectl apply -f configs/

# Deploy application
kubectl apply -f k8s/production/
```
- [ ] Monitoring stack deployed
- [ ] Secrets deployed
- [ ] ConfigMaps deployed
- [ ] Application deployed

### Step 4: Verify Deployment
```bash
# Check pod status
kubectl get pods -n production

# Check rollout status
kubectl rollout status deployment/code-factory -n production

# Run health checks
python run_integration_tests.py --suite smoke

# Check metrics
curl https://prometheus.example.com/api/v1/query?query=up
```
- [ ] All pods running
- [ ] Rollout successful
- [ ] Health checks passing
- [ ] Metrics collecting

### Step 5: Smoke Testing
- [ ] Health endpoint responding
- [ ] API endpoints accessible
- [ ] Authentication working
- [ ] Database connections working
- [ ] Redis connections working
- [ ] External integrations working

### Step 6: Monitoring Validation
- [ ] Prometheus receiving metrics
- [ ] Grafana dashboards updating
- [ ] Alerts configured and working
- [ ] Logs flowing to aggregation
- [ ] APM traces visible

### Step 7: Traffic Ramp-Up
```bash
# Start with 10% traffic
kubectl scale deployment code-factory-canary -n production --replicas=1
kubectl scale deployment code-factory-stable -n production --replicas=9

# Monitor for 15 minutes...

# Increase to 50% traffic
kubectl scale deployment code-factory-canary -n production --replicas=5
kubectl scale deployment code-factory-stable -n production --replicas=5

# Monitor for 15 minutes...

# Full traffic
kubectl scale deployment code-factory-canary -n production --replicas=10
kubectl scale deployment code-factory-stable -n production --replicas=0
```
- [ ] 10% traffic successful (15 min monitoring)
- [ ] 50% traffic successful (15 min monitoring)
- [ ] 100% traffic successful

### Step 8: Post-Deployment Validation
```bash
# Run full integration tests
python run_integration_tests.py --suite full

# Check error rates
# Should be < 1%

# Check latency
# p95 should be < 500ms

# Check resource utilization
# CPU < 70%, Memory < 80%
```
- [ ] Integration tests passing
- [ ] Error rate acceptable (< 1%)
- [ ] Latency acceptable (p95 < 500ms)
- [ ] Resource utilization normal
- [ ] No alerts firing

### Step 9: Communication
- [ ] Update status page (operational)
- [ ] Notify stakeholders of successful deployment
- [ ] Inform customer support team
- [ ] Post deployment announcement (if applicable)

## Post-Deployment

### Immediate (0-4 hours)
- [ ] Monitor error rates closely
- [ ] Monitor performance metrics
- [ ] Watch for alerts
- [ ] Be ready for rollback if needed

### Short-term (4-24 hours)
- [ ] Continue monitoring
- [ ] Review logs for issues
- [ ] Collect user feedback
- [ ] Document any issues

### Follow-up (1-7 days)
- [ ] Post-deployment review meeting
- [ ] Update runbooks based on lessons learned
- [ ] Create tickets for any issues found
- [ ] Celebrate successful deployment! 🎉

## Rollback Criteria

Initiate rollback if:
- [ ] Error rate > 5%
- [ ] p95 latency > 2x baseline
- [ ] Critical bug discovered
- [ ] Data corruption detected
- [ ] Security vulnerability exposed

### Rollback Procedure
```bash
# Quick rollback
kubectl rollout undo deployment/code-factory -n production

# Or to specific version
kubectl rollout undo deployment/code-factory -n production --to-revision=3

# Verify rollback
kubectl rollout status deployment/code-factory -n production

# Run smoke tests
python run_integration_tests.py --suite smoke
```
See `ROLLBACK_PROCEDURES.md` for detailed instructions.

## Emergency Contacts

| Role | Name | Phone | Email |
|------|------|-------|-------|
| Deployment Lead | _________ | _________ | _________ |
| Engineering Manager | _________ | _________ | _________ |
| DevOps Lead | _________ | _________ | _________ |
| Database Admin | _________ | _________ | _________ |
| Security Lead | _________ | _________ | _________ |
| On-Call Engineer | _________ | _________ | _________ |

## Sign-off

### Pre-Deployment
- [ ] Engineering Lead: _________________ Date: _______
- [ ] DevOps Lead: _________________ Date: _______
- [ ] Security Lead: _________________ Date: _______
- [ ] Product Owner: _________________ Date: _______

### Post-Deployment
- [ ] Deployment successful: _________________ Date: _______
- [ ] All checks passed: _________________ Date: _______
- [ ] Production stable: _________________ Date: _______

## Notes

Use this section to document any issues, deviations from plan, or observations:

```
___________________________________________________________________
___________________________________________________________________
___________________________________________________________________
___________________________________________________________________
___________________________________________________________________
```

---

**Document Version:** 1.0.0  
**Last Updated:** 2025-11-22  
**Next Review:** Before each major deployment
