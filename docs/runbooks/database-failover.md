<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# Runbook: Database Failover

## When to use this runbook

Use this runbook when the primary database is unavailable, unhealthy, too slow to support safe operation, or when failover to a replica is required to preserve service continuity.

## Detection criteria

Trigger this runbook when one or more of the following are true:

- Database connectivity checks fail from application services.
- Readiness checks fail because the database cannot accept connections.
- API latency or error rate spikes are dominated by database timeout or connection errors.
- The primary database is reachable but unable to serve write traffic reliably.
- Replica lag, failover alarms, or infrastructure alerts indicate primary instability.

## Immediate response

1. Confirm impact.
   - Identify affected services and whether the issue is read-only, write-only, or total unavailability.
2. Freeze unsafe change activity.
   - Halt schema changes, bulk jobs, and manual maintenance while diagnosis is in progress.
3. Protect data integrity.
   - Do not fail over to a replica with unacceptable lag.
   - Capture current replication state before changing writer endpoints.
4. Execute failover.
   - Promote the designated healthy replica or switch application traffic to the pre-approved standby.
   - Update connection targets, service discovery, or secret-backed connection strings as required.
5. Restart or recycle application connections.
   - Force stale connection pools to reconnect to the new primary.

## Investigation checklist

- Is the problem isolated to the primary, the network path, or connection exhaustion in the app?
- What is current replica lag?
- Were there recent migrations, connection pool changes, or credential changes?
- Did storage pressure, IOPS exhaustion, or disk saturation trigger the event?
- Is SQLite dev fallback accidentally in use where a production database is expected?

## Recovery actions

- Promote the healthiest replica that meets lag and durability requirements.
- Point application services to the new writer endpoint.
- Recycle workers so pooled connections do not keep using the old primary.
- Pause replay-heavy or write-heavy background jobs until stability is confirmed.
- Preserve logs and metadata from the failed primary for later root-cause analysis.

## Recovery verification

Do not close the incident until all of the following are true:

- Write operations succeed against the new primary.
- Read operations are consistent and do not show stale or split-brain behavior.
- Connection errors and query timeout alerts have returned toward baseline.
- Replication topology is understood and documented after failover.
- Application health checks and at least one end-to-end workflow pass.

## Escalation path

- **P1**: Platform on-call and database owner
- **P0**: Platform lead immediately
- **Potential data loss or split-brain risk**: Security owner and leadership immediately

## Follow-up

Capture:

- Failover start and end time
- Replica lag at decision time
- Whether connection pooling, DNS, or service discovery slowed recovery
- Any migrations, hot queries, or capacity issues that contributed to the failure
