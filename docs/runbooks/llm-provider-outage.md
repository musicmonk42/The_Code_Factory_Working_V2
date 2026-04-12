<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# Runbook: LLM Provider Outage

## When to use this runbook

Use this runbook when one or more configured LLM providers are unavailable, timing out, rate-limiting heavily, or returning malformed responses that block generation or repair workflows.

## Detection criteria

Trigger this runbook when one or more of the following are true:

- A sustained spike in 5xx responses or timeout errors from provider APIs.
- Provider-specific authentication or quota errors begin appearing across multiple jobs.
- Generation, critique, or clarifier stages stall because no provider can complete requests.
- Health checks or diagnostics show zero available providers while requests are actively arriving.
- Alerting shows high retry volume, rising queue age, or cascading stage failures tied to LLM calls.

## Immediate response

1. Confirm scope.
   - Determine whether the outage affects one provider or all configured providers.
   - Identify which user-facing workflows are degraded: codegen, testgen, deploy, docgen, critique, or clarifier.
2. Reduce blast radius.
   - Shift traffic to healthy providers if multi-provider configuration is available.
   - Disable nonessential LLM-heavy background work if it is starving primary workflows.
   - If no provider is healthy, place affected endpoints into a clearly signaled degraded mode.
3. Validate credentials and quotas.
   - Check whether failures are caused by expired keys, quota exhaustion, or provider-side service errors.
   - Confirm environment variables and secret injection still match expected production values.
4. Protect the queue.
   - Slow or pause retry loops that are amplifying provider errors.
   - Watch for growing job backlog and dead-letter behavior.
5. Communicate impact.
   - Mark the incident as P1 or P0 depending on whether core generation is unavailable.
   - Notify the platform lead if no healthy fallback exists.

## Investigation checklist

- Which provider is failing?
- Are errors consistent across regions, workers, and stages?
- Did the incident start immediately after a config, dependency, or credential change?
- Are failures hard errors, slow responses, or partial malformed outputs?
- Is the fallback provider configured but not selected, or unavailable entirely?

## Recovery actions

- Re-route traffic to an alternate provider.
- Correct invalid or rotated credentials if the provider itself is healthy.
- Increase provider timeout or lower concurrency only if the provider is slow rather than down.
- Re-enable paused queues gradually after provider health stabilizes.
- Retry only the failed or stuck jobs after healthy provider response is confirmed.

## Recovery verification

Do not close the incident until all of the following are true:

- Provider health checks succeed consistently.
- At least one end-to-end generation workflow completes successfully.
- Queue age, retry count, and timeout rates return toward baseline.
- Error logs no longer show sustained provider outage symptoms.
- Operators confirm user-visible workflow recovery in API and CLI paths.

## Escalation path

- **P1**: Platform on-call and platform lead
- **P0**: Platform lead immediately; customer communications owner if the public API is materially degraded
- **Security-sensitive credential failure**: Security owner as well

## Follow-up

Capture:

- Affected providers and time window
- Whether fallback routing worked as designed
- Needed changes to quotas, alerts, provider prioritization, or retry policy
