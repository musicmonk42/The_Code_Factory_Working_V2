# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# WebSocket Job Status API

The Code Factory platform exposes a WebSocket endpoint for real-time job
status updates, eliminating the need to poll the REST API.

---

## Endpoint

```
ws[s]://<host>/api/v2/jobs/{job_id}/ws
```

| Parameter | Description |
|-----------|-------------|
| `job_id`  | The unique job identifier returned by `POST /api/jobs`. |

---

## Connection Lifecycle

1. **Client connects** → server sends a `connected` acknowledgement.
2. **Job runs** → server streams `stage_progress` events as each pipeline
   stage completes.
3. **Job finishes** → server sends `job_complete` (or `job_failed`) and
   closes the stream.
4. **Idle** → server sends a `heartbeat` every 30 seconds to keep the
   connection alive.

---

## Message Schema

All messages are UTF-8 encoded JSON objects with an `event` field.

### `connected`

Sent immediately after the WebSocket handshake succeeds.

```json
{
  "event": "connected",
  "job_id": "job-abc123",
  "connection_id": "127.0.0.1_job-abc123_140234",
  "timestamp": "2025-01-01T00:00:00+00:00"
}
```

### `stage_progress`

Sent when a pipeline stage reports progress.

```json
{
  "event": "stage_progress",
  "job_id": "job-abc123",
  "stage": "CODEGEN",
  "percent": 40,
  "timestamp": "2025-01-01T00:00:01+00:00",
  "data": {}
}
```

| Field     | Type    | Description |
|-----------|---------|-------------|
| `stage`   | string  | Pipeline stage name (e.g. `READ_MD`, `CODEGEN`, `VALIDATE`). |
| `percent` | integer | Estimated completion percentage (0–100). |
| `data`    | object  | Additional stage-specific metadata. |

### `job_complete`

Sent when the job finishes successfully.  After receiving this event the
server closes the WebSocket.

```json
{
  "event": "job_complete",
  "job_id": "job-abc123",
  "result": {
    "status": "complete",
    "files": ["app.py", "tests/test_app.py"]
  },
  "timestamp": "2025-01-01T00:00:05+00:00"
}
```

### `job_failed`

Sent when the job terminates with an error.  After receiving this event the
server closes the WebSocket.

```json
{
  "event": "job_failed",
  "job_id": "job-abc123",
  "error": "LLM provider returned an empty response",
  "timestamp": "2025-01-01T00:00:03+00:00"
}
```

### `heartbeat`

Sent every 30 seconds when no other event has been emitted.

```json
{
  "event": "heartbeat",
  "job_id": "job-abc123",
  "timestamp": "2025-01-01T00:00:30+00:00"
}
```

---

## Rate Limiting

The endpoint enforces the same limits as the existing `/api/events/ws`
endpoint:

| Limit | Value |
|-------|-------|
| Max active connections per IP | 5 |
| Max total server connections | 500 |
| Max new connections per IP per 60 s | 10 |

Connections that exceed these limits receive a `1008 Policy Violation`
close code with a human-readable reason.

---

## JavaScript Usage Example

```javascript
const jobId = 'job-abc123';
const ws = new WebSocket(`wss://api.example.com/api/v2/jobs/${jobId}/ws`);

ws.addEventListener('open', () => {
    console.log('Connected to job status stream');
});

ws.addEventListener('message', (event) => {
    const msg = JSON.parse(event.data);
    switch (msg.event) {
        case 'connected':
            console.log('Connection acknowledged, job:', msg.job_id);
            break;
        case 'stage_progress':
            console.log(`Stage ${msg.stage}: ${msg.percent}%`);
            updateProgressBar(msg.percent);
            break;
        case 'job_complete':
            console.log('Job finished!', msg.result);
            ws.close();
            break;
        case 'job_failed':
            console.error('Job failed:', msg.error);
            ws.close();
            break;
        case 'heartbeat':
            // Keep-alive — no action needed
            break;
    }
});

ws.addEventListener('close', (event) => {
    console.log('Stream closed', event.code, event.reason);
});

ws.addEventListener('error', (error) => {
    console.error('WebSocket error:', error);
});
```

---

## Python (websockets library) Example

```python
import asyncio
import json
import websockets

async def stream_job(job_id: str) -> None:
    uri = f"ws://localhost:8000/api/v2/jobs/{job_id}/ws"
    async with websockets.connect(uri) as ws:
        async for raw in ws:
            msg = json.loads(raw)
            print(msg)
            if msg["event"] in {"job_complete", "job_failed"}:
                break

asyncio.run(stream_job("job-abc123"))
```

---

## Comparison with `/api/events/ws`

| Feature | `/api/events/ws` | `/api/v2/jobs/{job_id}/ws` |
|---------|-----------------|--------------------------|
| Scope | All platform events | Single job |
| Filtering | None (all events) | Automatic by `job_id` |
| Terminal event | None | `job_complete` / `job_failed` |
| Auto-close on completion | No | Yes |
