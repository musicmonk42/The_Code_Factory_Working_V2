# Backup and Disaster Recovery Guide

This guide covers backup strategies, disaster recovery procedures, and business continuity planning for the Code Factory platform.

## Table of Contents

- [Overview](#overview)
- [Backup Strategy](#backup-strategy)
- [Backup Configuration](#backup-configuration)
- [Recovery Procedures](#recovery-procedures)
- [Disaster Recovery](#disaster-recovery)
- [Testing and Validation](#testing-and-validation)
- [Monitoring and Alerting](#monitoring-and-alerting)

## Overview

### Recovery Objectives

**Recovery Time Objective (RTO):** 4 hours
- Time from disaster declaration to service restoration

**Recovery Point Objective (RPO):** 1 hour
- Maximum acceptable data loss

### Backup Scope

Components backed up:
- **Databases:** PostgreSQL, SQLite
- **Redis:** Memory snapshots
- **File Storage:** Application data, logs, configurations
- **Secrets:** Encrypted backup of secrets (stored separately)
- **Code:** Git repositories, Docker images
- **Kafka:** Topic configurations and offsets

## Backup Strategy

### Full Backups

**Schedule:** Daily at 2:00 AM UTC
**Retention:** 30 days
**Storage:** S3 with versioning enabled
**Encryption:** AES-256 with customer-managed keys

### Incremental Backups

**Schedule:** Every 6 hours
**Retention:** 7 days
**Storage:** S3 Standard-IA

### Point-in-Time Recovery (PITR)

**PostgreSQL:** WAL archiving enabled
**Retention:** 7 days
**Allows recovery to any point in time within the retention window

## Backup Configuration

### PostgreSQL Database Backup

#### Using pg_dump

```bash
#!/bin/bash
# backup-postgres.sh

# Configuration
BACKUP_DIR="/var/backups/postgres"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
DB_NAME="codefactory_prod"
S3_BUCKET="codefactory-prod-backups"

# Create backup directory
mkdir -p $BACKUP_DIR

# Perform backup
pg_dump -h localhost -U codefactory -d $DB_NAME \
  --format=custom \
  --compress=9 \
  --file=$BACKUP_DIR/${DB_NAME}_${TIMESTAMP}.dump

# Verify backup
pg_restore --list $BACKUP_DIR/${DB_NAME}_${TIMESTAMP}.dump > /dev/null 2>&1
if [ $? -eq 0 ]; then
  echo "Backup verification successful"
else
  echo "Backup verification failed!"
  exit 1
fi

# Upload to S3
aws s3 cp $BACKUP_DIR/${DB_NAME}_${TIMESTAMP}.dump \
  s3://${S3_BUCKET}/postgres/${DB_NAME}_${TIMESTAMP}.dump \
  --storage-class STANDARD_IA \
  --server-side-encryption AES256

# Cleanup old local backups (keep last 3 days)
find $BACKUP_DIR -type f -mtime +3 -name "*.dump" -delete

# Update last backup timestamp
echo $TIMESTAMP > $BACKUP_DIR/last_backup.txt
```

#### Using WAL Archiving (PITR)

**postgresql.conf:**
```ini
# WAL archiving configuration
wal_level = replica
archive_mode = on
archive_command = 'aws s3 cp %p s3://codefactory-prod-wal/%f'
archive_timeout = 300  # 5 minutes

# Replication settings
max_wal_senders = 3
wal_keep_size = 1GB
```

### Redis Backup

```bash
#!/bin/bash
# backup-redis.sh

BACKUP_DIR="/var/backups/redis"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
REDIS_HOST="localhost"
REDIS_PORT="6379"
S3_BUCKET="codefactory-prod-backups"

# Trigger Redis save
redis-cli -h $REDIS_HOST -p $REDIS_PORT BGSAVE

# Wait for save to complete
while [ $(redis-cli -h $REDIS_HOST -p $REDIS_PORT LASTSAVE) -eq $(redis-cli -h $REDIS_HOST -p $REDIS_PORT LASTSAVE) ]; do
  sleep 1
done

# Copy RDB file
mkdir -p $BACKUP_DIR
cp /var/lib/redis/dump.rdb $BACKUP_DIR/dump_${TIMESTAMP}.rdb

# Upload to S3
aws s3 cp $BACKUP_DIR/dump_${TIMESTAMP}.rdb \
  s3://${S3_BUCKET}/redis/dump_${TIMESTAMP}.rdb \
  --server-side-encryption AES256

# Cleanup old backups
find $BACKUP_DIR -type f -mtime +7 -name "dump_*.rdb" -delete
```

### Application Files Backup

```bash
#!/bin/bash
# backup-files.sh

BACKUP_DIR="/var/backups/app"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
S3_BUCKET="codefactory-prod-backups"
APP_DIR="/app"

# Directories to backup
DIRS=(
  "$APP_DIR/output"
  "$APP_DIR/logs"
  "$APP_DIR/checkpoints"
  "/etc/code-factory"
)

# Create backup archive
mkdir -p $BACKUP_DIR
tar -czf $BACKUP_DIR/app_files_${TIMESTAMP}.tar.gz \
  --exclude='*.pyc' \
  --exclude='__pycache__' \
  --exclude='*.log' \
  "${DIRS[@]}"

# Upload to S3
aws s3 cp $BACKUP_DIR/app_files_${TIMESTAMP}.tar.gz \
  s3://${S3_BUCKET}/app-files/app_files_${TIMESTAMP}.tar.gz \
  --storage-class STANDARD_IA \
  --server-side-encryption AES256

# Cleanup
find $BACKUP_DIR -type f -mtime +7 -name "app_files_*.tar.gz" -delete
```

### Automated Backup Orchestration

**backup-all.sh:**
```bash
#!/bin/bash
# Master backup script

set -e

LOCK_FILE="/var/lock/backup.lock"
LOG_FILE="/var/log/code-factory/backup.log"

# Ensure only one backup runs at a time
if [ -f "$LOCK_FILE" ]; then
  echo "Backup already running" | tee -a $LOG_FILE
  exit 1
fi

touch $LOCK_FILE
trap "rm -f $LOCK_FILE" EXIT

echo "[$(date)] Starting backup..." | tee -a $LOG_FILE

# Run backups in parallel
{
  /opt/scripts/backup-postgres.sh 2>&1 | tee -a $LOG_FILE &
  PG_PID=$!
  
  /opt/scripts/backup-redis.sh 2>&1 | tee -a $LOG_FILE &
  REDIS_PID=$!
  
  /opt/scripts/backup-files.sh 2>&1 | tee -a $LOG_FILE &
  FILES_PID=$!
  
  # Wait for all backups to complete
  wait $PG_PID $REDIS_PID $FILES_PID
}

if [ $? -eq 0 ]; then
  echo "[$(date)] Backup completed successfully" | tee -a $LOG_FILE
  
  # Update metrics
  echo "backup_last_success_timestamp $(date +%s)" | \
    curl -X POST --data-binary @- http://localhost:9091/metrics/job/backup
else
  echo "[$(date)] Backup failed!" | tee -a $LOG_FILE
  
  # Send alert
  curl -X POST \
    -H "Content-Type: application/json" \
    -d '{"text":"Backup failed on production"}' \
    $ALERT_SLACK_WEBHOOK
  
  exit 1
fi
```

### Cron Configuration

```cron
# /etc/cron.d/code-factory-backup

# Full backup daily at 2 AM UTC
0 2 * * * root /opt/scripts/backup-all.sh

# Incremental backups every 6 hours
0 */6 * * * root /opt/scripts/backup-postgres.sh --incremental

# Verify backups daily
0 4 * * * root /opt/scripts/verify-backups.sh

# Cleanup old backups weekly
0 3 * * 0 root /opt/scripts/cleanup-old-backups.sh
```

## Recovery Procedures

### PostgreSQL Recovery

#### Full Recovery from Backup

```bash
#!/bin/bash
# restore-postgres.sh

BACKUP_FILE=$1
DB_NAME="codefactory_prod"

if [ -z "$BACKUP_FILE" ]; then
  echo "Usage: $0 <backup_file>"
  exit 1
fi

# Stop application
kubectl scale deployment code-factory --replicas=0

# Drop and recreate database
psql -U postgres -c "DROP DATABASE IF EXISTS ${DB_NAME}"
psql -U postgres -c "CREATE DATABASE ${DB_NAME}"

# Restore from backup
pg_restore -U codefactory -d ${DB_NAME} \
  --verbose \
  --clean \
  --no-acl \
  --no-owner \
  $BACKUP_FILE

# Verify restoration
psql -U codefactory -d ${DB_NAME} -c "SELECT count(*) FROM information_schema.tables"

# Restart application
kubectl scale deployment code-factory --replicas=3

echo "Database restored successfully"
```

#### Point-in-Time Recovery

```bash
#!/bin/bash
# restore-postgres-pitr.sh

TARGET_TIME=$1  # Format: 2025-11-22 14:30:00

if [ -z "$TARGET_TIME" ]; then
  echo "Usage: $0 'YYYY-MM-DD HH:MM:SS'"
  exit 1
fi

# Stop PostgreSQL
systemctl stop postgresql

# Restore base backup
pg_basebackup -h backup-host -D /var/lib/postgresql/data -U replication -P

# Create recovery configuration
cat > /var/lib/postgresql/data/recovery.conf <<EOF
restore_command = 'aws s3 cp s3://codefactory-prod-wal/%f %p'
recovery_target_time = '$TARGET_TIME'
recovery_target_action = 'promote'
EOF

# Start PostgreSQL (will enter recovery mode)
systemctl start postgresql

echo "Point-in-time recovery initiated to $TARGET_TIME"
echo "Monitor logs: tail -f /var/log/postgresql/postgresql.log"
```

### Redis Recovery

```bash
#!/bin/bash
# restore-redis.sh

BACKUP_FILE=$1

if [ -z "$BACKUP_FILE" ]; then
  echo "Usage: $0 <rdb_backup_file>"
  exit 1
fi

# Stop Redis
systemctl stop redis

# Restore RDB file
cp $BACKUP_FILE /var/lib/redis/dump.rdb
chown redis:redis /var/lib/redis/dump.rdb

# Start Redis
systemctl start redis

echo "Redis restored successfully"
```

### Application Files Recovery

```bash
#!/bin/bash
# restore-files.sh

BACKUP_FILE=$1
RESTORE_DIR="/app"

if [ -z "$BACKUP_FILE" ]; then
  echo "Usage: $0 <backup_tar_gz>"
  exit 1
fi

# Stop application
kubectl scale deployment code-factory --replicas=0

# Extract files
tar -xzf $BACKUP_FILE -C $RESTORE_DIR

# Set permissions
chown -R app:app $RESTORE_DIR

# Restart application
kubectl scale deployment code-factory --replicas=3

echo "Application files restored successfully"
```

## Disaster Recovery

### Disaster Scenarios

1. **Single Region Failure**
   - RTO: 2 hours
   - Failover to DR region (us-west-2)
   
2. **Database Corruption**
   - RTO: 4 hours
   - Restore from PITR backup
   
3. **Complete Data Center Loss**
   - RTO: 6 hours
   - Rebuild from S3 backups in new region
   
4. **Ransomware/Security Breach**
   - RTO: 8 hours
   - Restore from immutable S3 backups with Object Lock

### DR Failover Procedure

#### Step 1: Declare Disaster

```bash
# Incident commander declares disaster
echo "DISASTER DECLARED at $(date)" >> /var/log/dr/incident.log

# Notify team
./scripts/notify-team.sh "DR_INITIATED"
```

#### Step 2: Assess Impact

```bash
# Check service status
./scripts/health-check-all.sh

# Document failed components
./scripts/generate-impact-report.sh > /tmp/impact-report.txt
```

#### Step 3: Activate DR Site

```bash
#!/bin/bash
# activate-dr.sh

DR_REGION="us-west-2"
DR_CLUSTER="code-factory-dr"

echo "Activating DR site in $DR_REGION..."

# Update DNS to point to DR site
aws route53 change-resource-record-sets \
  --hosted-zone-id Z1234567890ABC \
  --change-batch file://dns-failover.json

# Scale up DR cluster
aws eks update-kubeconfig --name $DR_CLUSTER --region $DR_REGION
kubectl scale deployment code-factory --replicas=3

# Restore data
./scripts/restore-all.sh --region $DR_REGION

# Verify services
./scripts/health-check-all.sh

echo "DR site activated. Services should be available in 5-10 minutes."
```

#### Step 4: Verify Recovery

```bash
# Run smoke tests
./scripts/smoke-tests.sh

# Check metrics
curl https://grafana.dr.example.com/api/health

# Verify data integrity
./scripts/verify-data-integrity.sh
```

#### Step 5: Communicate Status

```bash
# Update status page
./scripts/update-status-page.sh "OPERATIONAL"

# Notify stakeholders
./scripts/notify-stakeholders.sh "RECOVERED"
```

### DR Site Configuration

**Infrastructure as Code (Terraform):**
```hcl
# dr-infrastructure.tf

module "dr_region" {
  source = "./modules/infrastructure"
  
  region = "us-west-2"
  environment = "dr"
  
  # Smaller capacity for DR (scale up on failover)
  instance_type = "t3.medium"
  min_nodes = 2
  max_nodes = 10
  
  # Cross-region replication
  enable_replication = true
  primary_region = "us-east-1"
  
  # Automated backups
  backup_retention_days = 30
  enable_pitr = true
  
  # Monitoring
  enable_cloudwatch = true
  enable_xray = true
}
```

### Data Replication

**PostgreSQL Replication:**
```sql
-- On primary (us-east-1)
CREATE PUBLICATION codefactory_pub FOR ALL TABLES;

-- On DR replica (us-west-2)
CREATE SUBSCRIPTION codefactory_sub
  CONNECTION 'host=primary.us-east-1.rds.amazonaws.com port=5432 dbname=codefactory'
  PUBLICATION codefactory_pub;
```

**Redis Replication:**
```conf
# redis.conf (DR site)
replicaof primary-redis.us-east-1.example.com 6379
replica-read-only yes
```

**S3 Cross-Region Replication:**
```json
{
  "Rules": [{
    "Status": "Enabled",
    "Priority": 1,
    "Filter": {},
    "Destination": {
      "Bucket": "arn:aws:s3:::codefactory-dr-backups",
      "ReplicationTime": {
        "Status": "Enabled",
        "Time": {"Minutes": 15}
      }
    }
  }]
}
```

## Testing and Validation

### Backup Verification Script

```bash
#!/bin/bash
# verify-backups.sh

S3_BUCKET="codefactory-prod-backups"
TEST_DIR="/tmp/backup-verify"

echo "Verifying backups..."

# Get latest PostgreSQL backup
LATEST_PG=$(aws s3 ls s3://${S3_BUCKET}/postgres/ | sort | tail -n 1 | awk '{print $4}')

# Download and verify
mkdir -p $TEST_DIR
aws s3 cp s3://${S3_BUCKET}/postgres/$LATEST_PG $TEST_DIR/

# Test restore
pg_restore --list $TEST_DIR/$LATEST_PG > /dev/null 2>&1
if [ $? -eq 0 ]; then
  echo "✓ PostgreSQL backup verified: $LATEST_PG"
else
  echo "✗ PostgreSQL backup verification failed!"
  exit 1
fi

# Verify Redis backup
LATEST_REDIS=$(aws s3 ls s3://${S3_BUCKET}/redis/ | sort | tail -n 1 | awk '{print $4}')
aws s3 cp s3://${S3_BUCKET}/redis/$LATEST_REDIS $TEST_DIR/

if [ -f "$TEST_DIR/$LATEST_REDIS" ]; then
  echo "✓ Redis backup verified: $LATEST_REDIS"
else
  echo "✗ Redis backup verification failed!"
  exit 1
fi

# Cleanup
rm -rf $TEST_DIR

echo "All backups verified successfully"
```

### DR Drill Procedure

**Quarterly DR drill checklist:**

1. **Pre-Drill Preparation (Week Before)**
   - [ ] Schedule drill with all stakeholders
   - [ ] Prepare runbook and test scenarios
   - [ ] Verify DR site is synced
   - [ ] Document current production state

2. **Drill Execution (2-4 hours)**
   - [ ] T+0:00 - Declare simulated disaster
   - [ ] T+0:05 - Assemble incident response team
   - [ ] T+0:10 - Begin failover to DR site
   - [ ] T+0:30 - DR site services starting
   - [ ] T+1:00 - Verify all services operational
   - [ ] T+1:30 - Run smoke tests
   - [ ] T+2:00 - Declare recovery successful

3. **Post-Drill Review (1 week after)**
   - [ ] Document actual vs. target RTO/RPO
   - [ ] Identify gaps and issues
   - [ ] Update runbooks
   - [ ] Create action items for improvements
   - [ ] Schedule remediation work

### Recovery Testing Schedule

- **Daily:** Automated backup verification
- **Weekly:** Restore test in staging environment
- **Monthly:** Full recovery test with random backup
- **Quarterly:** DR failover drill
- **Annually:** Disaster recovery tabletop exercise

## Monitoring and Alerting

### Backup Monitoring Metrics

```prometheus
# Backup success
backup_last_success_timestamp{component="postgresql"}
backup_last_success_timestamp{component="redis"}
backup_last_success_timestamp{component="files"}

# Backup size
backup_size_bytes{component="postgresql"}
backup_size_bytes{component="redis"}

# Backup duration
backup_duration_seconds{component="postgresql"}

# Backup failures
backup_failures_total{component="postgresql"}

# Storage usage
backup_storage_used_bytes
backup_storage_total_bytes
```

### Alerts

```yaml
# Backup failed
- alert: BackupFailed
  expr: time() - backup_last_success_timestamp > 86400
  for: 1h
  annotations:
    summary: "Backup has not succeeded in 24 hours"
    
# Backup storage low
- alert: BackupStorageLow
  expr: (backup_storage_used_bytes / backup_storage_total_bytes) > 0.9
  for: 1h
  annotations:
    summary: "Backup storage is 90% full"
```

## Runbooks

### Quick Reference

| Scenario | RTO | Procedure |
|----------|-----|-----------|
| Database corruption | 2h | Restore from latest PITR |
| Redis failure | 30m | Restore from latest snapshot |
| Region failure | 4h | Failover to DR region |
| Ransomware | 6h | Restore from immutable backups |
| Accidental deletion | 1h | Point-in-time recovery |

### Emergency Contacts

- **Incident Commander:** oncall@example.com
- **Database Admin:** dba-oncall@example.com
- **Cloud Infrastructure:** cloud-oncall@example.com
- **Security Team:** security@example.com
- **Management Escalation:** management@example.com

## Compliance

### Backup Retention Policy

- **Production Data:** 30 days
- **Audit Logs:** 7 years
- **Financial Records:** 7 years (per SOX requirements)
- **PII Data:** As per GDPR requirements (30 days after deletion request)

### Encryption Requirements

- **At Rest:** AES-256 encryption for all backups
- **In Transit:** TLS 1.2+ for all backup transfers
- **Key Management:** Customer-managed keys in KMS

### Audit Requirements

- **Backup Access:** All access logged to SIEM
- **Restoration:** All restorations require approval and logging
- **DR Drills:** Documented and reviewed by management

---

**Document Version:** 1.0.0  
**Last Updated:** 2025-11-22  
**Next Review:** 2026-02-22  
**Owner:** DevOps Team
