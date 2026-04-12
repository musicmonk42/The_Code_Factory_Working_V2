<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# Runbook: High Error Rate Triage

## When to use this runbook

Use this runbook when application error rate rises above normal thresholds and the root cause is not yet known. This is the first-response playbook for broad production instability.

## Detection criteria

Trigger this runbook when one or more of the following are true:

- Error-rate alerts fire for core APIs or worker pipelines.
- A sudden increase in 4xx or 5xx responses appears across multiple endpoints.
- Queue backlog, timeout rate, or saturation metrics rise together with application errors.
- Health checks remain technically up but customer workflows are failing.
- Multiple downstream runbooks may apply, but the dominant failure mode is not yet identified.

## Immediate response

1. Triage severity.
   - P0 if core production workflows are broadly unavailable.
   - P1 if major workflows are degraded but partial service remains.
2. Establish incident command.
   - Assign an incident commander and start a timestamped incident log.
3. Confirm blast radius.
   - Identify affected endpoints, stages, regions, tenants, or internal subsystems.
4. Check the change window.
   - Ask what changed immediately before the spike: deploy, secret change, traffic surge, quota event, schema change, or broker instability.
5. Mitigate fast.
   - Roll back the last risky change if the correlation is strong and rollback is low risk.
   - Rate-limit or temporarily disable the narrowest failing path if it protects the rest of the platform.

## Investigation checklist

- Are errors concentrated in one endpoint, one stage, or one dependency?
- Are they 4xx client errors, 5xx server errors, or timeout-related failures?
- Do logs point to database, message bus, provider, auth, or deployment causes?
- Is the spike caused by saturation: CPU, memory, connection pools, queue depth, or rate limits?
- Is the system healthy but one new release or config change is misbehaving?

## Recovery actions

- Route to a more specific runbook as soon as a dominant cause is identified.
- Apply the smallest reversible mitigation first.
- Roll back bad config or code changes quickly when evidence supports it.
- Restart only the failing component unless a wider recycle is clearly justified.
- Keep communication updates frequent for P0/P1 incidents.

## Recovery verification

Do not close the incident until all of the following are true:

- Error rate is back within expected bounds.
- Latency and queue metrics have stabilized.
- At least one full customer workflow passes.
- The immediate triggering factor is understood well enough to prevent blind recurrence.
- A follow-up owner is assigned if the deeper fix is not yet complete.

## Escalation path

- **P1**: Platform on-call and platform lead
- **P0**: Platform lead immediately, plus customer communications owner if externally visible
- **Security or auth anomalies**: Security owner as well

## Follow-up

Capture:

- Exact time the spike began and ended
- Triggering change or dependency failure, if known
- Whether rollback, throttling, or restart resolved the issue
- What alert or dashboard should be tightened based on the incident
