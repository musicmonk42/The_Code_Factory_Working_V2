<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# Runbook: Secret Rotation

## When to use this runbook

Use this runbook for scheduled secret rotation and for emergency rotation when a credential may be exposed, expired, misissued, or otherwise untrusted.

## Detection criteria

Trigger this runbook when one or more of the following are true:

- A credential has reached its scheduled rotation window.
- A provider key, JWT secret, database password, or broker credential may be compromised.
- Authentication failures begin after a secret change and state is uncertain.
- Security tooling, audit review, or operator action identifies a secret handling concern.

## Immediate response

1. Classify urgency.
   - Emergency rotation if compromise is suspected.
   - Planned rotation if this is routine lifecycle maintenance.
2. Identify dependencies.
   - List every service, worker, job, provider, and environment that reads the secret.
3. Prepare replacement value.
   - Generate the new secret in the approved secret manager.
   - Do not place raw secrets in tickets, logs, chat, or source control.
4. Update consumers safely.
   - Prefer staged rollout if dual-read or overlapping validity is supported.
   - Restart or recycle services that do not reload secrets dynamically.
5. Revoke the old value.
   - Remove or disable the previous credential as soon as all required consumers are confirmed healthy.

## Investigation checklist

- Which exact secret is rotating?
- Which systems depend on it?
- Can the old and new values overlap safely during cutover?
- Are there cached tokens, pooled connections, or background workers that will keep using the old secret?
- Is this rotation triggered by compromise, expiry, or routine policy?

## Recovery actions

- Generate and store the new credential in the approved secret backend.
- Roll out the new value to all dependent services.
- Restart or redeploy services that require process restart for secret reload.
- Revoke the old value only after health checks and consumer validation pass.
- For emergency rotation, shorten the incident loop and preserve evidence for later review.

## Recovery verification

Do not close the incident or maintenance window until all of the following are true:

- Every dependent service authenticates successfully with the new secret.
- No logs or alerts show continued use of the old credential.
- The previous secret is revoked or disabled.
- At least one golden-path workflow using the rotated secret succeeds.
- Rotation details are recorded without exposing the secret material itself.

## Escalation path

- **Planned rotation**: Platform on-call or service owner
- **Emergency rotation**: Security owner and platform lead immediately
- **Broad auth failure after rotation**: Platform lead and affected service owners

## Follow-up

Capture:

- Systems touched by the rotation
- Whether overlapping validity was available
- Which services required restart versus live reload
- Any logging or operational hygiene issues discovered during rotation
