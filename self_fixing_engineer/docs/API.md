<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

\# API.md



\## Overview



This document provides details for integrating with the Arbiter orchestration APIs, user intent capture endpoints, and Human-in-the-Loop (HITL) notification system. All endpoints are secured with JWT authentication and role-based access.



---



\## Arbiter API



\### `GET /health`

\*\*Check arena status and version.\*\*



\- \*\*Auth:\*\* Required (any role)



\*\*Response:\*\*

```json

{

&nbsp; "status": "healthy",

&nbsp; "source": "arena\_name",

&nbsp; "version": "1.1.0"

}

```



---



\### `POST /scan`

\*\*Trigger a codebase scan via Arbiter.\*\*



\- \*\*Auth:\*\* Required (editor, admin)



\*\*Request:\*\*

```json

{

&nbsp; "paths": \["/src"]

}

```

\*\*Response:\*\*

```json

{

&nbsp; "message": "Codebase scan initiated",

&nbsp; "scan\_results": {

&nbsp;   /\* ...scan metadata or IDs for async poll... \*/

&nbsp; }

}

```



---



\### `POST /repair`

\*\*Request automatic repair on a module or file.\*\*



\- \*\*Auth:\*\* Required (editor, admin)



\*\*Request:\*\*

```json

{

&nbsp; "module": "main.py"

}

```

\*\*Response:\*\*

```json

{

&nbsp; "message": "Repair dispatched",

&nbsp; "result": {

&nbsp;   /\* ...repair outcome, diagnostics, or job ID... \*/

&nbsp; }

}

```



---



\## Intent Capture API



\### `POST /chat`

\*\*Submit user intent for LLM, codegen, or analysis.\*\*



\- \*\*Auth:\*\* Required (any role)



\*\*Request:\*\*

```json

{

&nbsp; "query": "Generate a Python function"

}

```

\*\*Response:\*\*

```json

{

&nbsp; "response": "def example(): pass"

}

```



---



\### `POST /feedback`

\*\*Submit HITL feedback or decisions.\*\*



\- \*\*Auth:\*\* Required (reviewer, admin)



\*\*Request:\*\*

```json

{

&nbsp; "decision\_id": "123",

&nbsp; "approved": true,

&nbsp; "user\_id": "alice",

&nbsp; "comment": "Looks safe."

}

```

\*\*Response:\*\*

```json

{

&nbsp; "message": "Feedback received",

&nbsp; "status": "pending" | "recorded"

}

```



---



\## WebSocket Notifications



\### `/ws/hitl`

\*\*Real-time notifications for human approval requests and feedback.\*\*



\- \*\*Auth:\*\* JWT required (reviewer, admin)

\- \*\*Connection:\*\* `ws://host/ws/hitl?token=...`



\*\*Events:\*\*



\#### `approval\_request`

```json

{

&nbsp; "event\_type": "approval\_request",

&nbsp; "decision\_id": "abc123",

&nbsp; "context": { ... }

}

```



\#### `approval\_response`

```json

{

&nbsp; "event\_type": "approval\_response",

&nbsp; "decision\_id": "abc123",

&nbsp; "approved": true,

&nbsp; "user\_id": "alice"

}

```



\#### `error`

```json

{

&nbsp; "event\_type": "error",

&nbsp; "detail": "Signature validation failed"

}

```



---



\## Authentication



\- \*\*Mechanism:\*\* JWT via `Authorization: Bearer <token>` header for REST, or `?token=` for WebSocket.



\*\*Roles:\*\*



\- \*\*admin\*\* – Full access to all endpoints and system actions.

\- \*\*editor\*\* – Can trigger scans and repairs.

\- \*\*reviewer\*\* – Can review/approve/deny decisions via HITL.

\- \*\*viewer\*\* – Read-only access to most endpoints.



\*\*Example header:\*\*

```makefile

Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI...

```



---



\## Examples



\*\*Scan Request:\*\*

```bash

curl -X POST https://api.example.com/scan \\

&nbsp; -H "Authorization: Bearer <token>" \\

&nbsp; -H "Content-Type: application/json" \\

&nbsp; -d '{"paths":\["/src"]}'

```



\*\*WebSocket Connection:\*\*

```nginx

wscat -c ws://localhost/ws/hitl?token=<token>

```



---



\## See Also



\- `arena.py`: FastAPI routes for Arbiter orchestration.

\- `api.py`: User intent and feedback endpoints.

\- `human\_loop.py`: WebSocket notifications and HITL events.

