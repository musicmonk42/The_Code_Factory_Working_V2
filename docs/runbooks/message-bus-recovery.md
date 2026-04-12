<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# Runbook: Message Bus Recovery

## When to use this runbook

Use this runbook when the platform message bus is unavailable, dispatchers are not running, queues are stuck, events are being dropped, or downstream workers stop receiving routed jobs.

## Detection criteria

Trigger this runbook when one or more of the following are true:

- Job routing succeeds at the API layer but no downstream processing begins.
- Queue depth grows while worker throughput drops toward zero.
- Dead-letter volume spikes or retry storms begin.
- Health checks show the message bus as unavailable or dispatcher tasks not started.
- WebSocket or event-stream consumers stop receiving expected lifecycle events.

## Immediate response

1. Confirm whether the failure is in the bus, the broker bridge, or downstream workers.
2. Stabilize producers.
   - Slow or pause nonessential publishers if queue growth threatens memory or storage safety.
3. Check dispatcher state.
   - Verify the message bus instance exists, dispatcher tasks are running, and the component is marked available.
4. Check broker dependencies.
   - Verify Redis, Kafka, or any configured bridge dependencies are reachable and healthy.
5. Restart the smallest safe unit.
   - Prefer restarting failed dispatcher tasks or the affected worker before recycling the full stack.

## Investigation checklist

- Are messages failing to publish, publish but not dispatch, or dispatch but not process?
- Is the fault inside the in-process bus, the Redis/Kafka bridge, or the subscriber layer?
- Did the incident begin after a deploy or config change?
- Are dead-letter and retry settings amplifying the outage?
- Are workers healthy but subscribed to the wrong topics or callbacks?

## Recovery actions

- Restore broker connectivity.
- Restart or recreate failed dispatcher tasks.
- Reconnect subscribers and verify expected topic bindings.
- Drain or replay dead-lettered messages only after the root cause is controlled.
- Resume paused publishers gradually to avoid an immediate second backlog wave.

## Recovery verification

Do not close the incident until all of the following are true:

- New jobs are routed and processed end-to-end.
- Queue depth and dead-letter volume trend down toward baseline.
- Subscribers receive expected lifecycle events again.
- No dispatcher task is repeatedly crashing or restarting.
- Error logs no longer show publish, subscribe, or bridge connectivity failures.

## Escalation path

- **P1**: Platform on-call and message-bus owner
- **P0**: Platform lead immediately
- **If data loss or dropped event ordering is suspected**: Escalate to leadership and capture forensic detail before replay

## Follow-up

Capture:

- Whether the outage originated in the bus implementation, external broker, or subscriber layer
- Backlog size and replay volume
- Any retry-policy or dead-letter-policy changes required to prevent recurrence
