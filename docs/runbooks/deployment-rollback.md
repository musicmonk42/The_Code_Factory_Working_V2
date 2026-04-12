<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# Runbook: Deployment Rollback

## When to use this runbook

Use this runbook when a recent deploy causes production instability, customer-visible regressions, dependency mismatches, or safety concerns that can be mitigated faster by reverting than by repairing in place.

## Detection criteria

Trigger this runbook when one or more of the following are true:

- Error rate, latency, or saturation worsens sharply after a deployment.
- A new release breaks core generation, routing, auth, or persistence paths.
- Health checks fail only on the new version.
- Roll-forward confidence is low and rollback is the fastest safe stabilization path.
- Multiple operators independently identify the release as the most likely trigger.

## Immediate response

1. Halt forward changes.
   - Freeze further deploys, migrations, or feature-flag expansions until stability returns.
2. Confirm rollback target.
   - Identify the last known good version, image tag, or rollout revision.
3. Check for non-reversible changes.
   - Confirm whether database migrations, secret format changes, or queue schema changes require extra care.
4. Roll back using the platform-appropriate mechanism.
   - Kubernetes: revert the deployment revision.
   - Docker Compose or equivalent: redeploy the previous known good image and configuration.
5. Watch health and traffic immediately after rollback begins.

## Investigation checklist

- Which exact commit, image, config, or migration introduced the regression?
- Is rollback safe without data loss or schema incompatibility?
- Did the release include hidden dependencies such as secret format or provider changes?
- Are only some services on the bad version, creating mixed-version behavior?

## Recovery actions

- Revert the affected service or services to the last known good version.
- If a migration prevents clean rollback, place the system in degraded or read-only mode while you stabilize dependency order.
- Restart pods or processes only as needed to ensure old code and old config are fully active.
- Keep the bad release blocked from automatic redeploy until root cause is known.

## Recovery verification

Do not close the incident until all of the following are true:

- The intended previous version is fully serving production traffic.
- Health checks and golden-path workflows succeed.
- Error rate and latency have returned toward baseline.
- No automated system is poised to reintroduce the bad release.
- Operators have captured the exact release artifact and rollback target in the incident log.

## Escalation path

- **P1**: Platform on-call and release owner
- **P0**: Platform lead immediately
- **If rollback is blocked by schema or data risk**: Database owner and security owner as needed

## Follow-up

Capture:

- Release identifier and rollback target
- Whether rollback was clean or required manual intervention
- Any migration, feature-flag, or config-ordering issue that complicated reversal
- Gating changes needed in CI/CD or progressive delivery before redeploying
