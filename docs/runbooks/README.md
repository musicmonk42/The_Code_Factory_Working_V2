<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# Incident Runbooks

This directory contains operator-facing incident response playbooks for the highest-value platform failure modes. These runbooks complement, rather than replace, the broader guidance in `docs/DEPLOYMENT.md`, `docs/KAFKA_SETUP.md`, `docs/SECRETS_MANAGEMENT.md`, and `docs/TROUBLESHOOTING.md`.

## How to use these runbooks

1. Stabilize first. Reduce customer impact before optimizing the fix.
2. Name an incident commander for any customer-facing or security-relevant event.
3. Record timestamps, actions taken, affected services, and commands run.
4. Prefer reversible changes and validated rollback paths.
5. Verify recovery with explicit checks before declaring the incident resolved.

## Severity guide

- **P0**: Full production outage, data integrity risk, or active security incident.
- **P1**: Major degradation affecting core workflows or many users.
- **P2**: Partial degradation or single-component failure with safe fallback.
- **P3**: Low-risk operational issue or warning-only condition.

## Escalation defaults

- **Primary on-call**: SRE / platform operator
- **Secondary**: Platform lead
- **Security-sensitive events**: Security owner and platform lead immediately
- **Customer-facing P0/P1**: Communications lead or customer success owner

## Runbook index

- [LLM Provider Outage](./llm-provider-outage.md)
- [Database Failover](./database-failover.md)
- [Message Bus Recovery](./message-bus-recovery.md)
- [High Error Rate Triage](./high-error-rate-triage.md)
- [Deployment Rollback](./deployment-rollback.md)
- [Secret Rotation](./secret-rotation.md)

## Common recovery checklist

Before closing any incident, confirm all of the following:

- Health endpoints return healthy or expected degraded state.
- Error rate, queue depth, latency, and saturation metrics have stabilized.
- No active data corruption, replay backlog, or retry storm remains.
- Monitoring alerts have cleared or been intentionally suppressed with justification.
- Incident notes and follow-up actions are captured in the ticket or postmortem.
