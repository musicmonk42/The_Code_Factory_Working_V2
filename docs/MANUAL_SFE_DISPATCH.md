<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# Manual Self-Fixing Engineer Dispatch

## Overview

As of the latest update, **job dispatch to the Self-Fixing Engineer (SFE) is now MANUAL ONLY**. This gives users full control over when and which completed jobs are sent to the SFE for automated analysis and improvement.

## Why Manual Dispatch?

**User Control**: Users can review job outputs before sending them to the SFE, ensuring only desired code is processed.

**Resource Management**: Prevents automatic processing of every completed job, reducing unnecessary SFE workload.

**Cost Optimization**: For cloud deployments, manual dispatch helps control API/compute costs.

**Quality Assurance**: Users can verify generated code meets requirements before initiating automated improvements.

## How It Works

### 1. Job Completion

When a code generation job completes successfully:
- Job status changes to `COMPLETED`
- Output files are generated and stored
- File count is automatically displayed in the UI
- **NO automatic dispatch occurs** - job waits for manual action

### 2. Manual Dispatch via UI

#### In the Jobs Tab:

1. Navigate to the **Jobs** tab
2. Find your completed job (status badge shows "completed")
3. Look for the **"🤖 Send to SFE"** button (only visible for completed jobs with output files)
4. Click the button to manually dispatch the job

#### What Happens:

- UI shows "Sending to Self-Fixing Engineer..." progress message
- Job is validated (must be completed with output files)
- Dispatch request is sent to the backend
- Backend validates the job and dispatches to SFE via configured method (Kafka or HTTP webhook)
- Success/failure message is displayed with correlation ID for tracking

### 3. Manual Dispatch via API

You can also dispatch jobs programmatically using the REST API:

```bash
# Dispatch a completed job to SFE
curl -X POST http://localhost:8000/api/generator/{job_id}/dispatch-to-sfe \
  -H "Content-Type: application/json"
```

**Response (Success):**
```json
{
  "status": "dispatched",
  "job_id": "8183136e-86fe-42f9-8412-b8f03c7a3edf",
  "success": true,
  "correlation_id": "abc123def"
}
```

**Response (No Dispatch Methods Available):**
```json
{
  "status": "failed",
  "job_id": "8183136e-86fe-42f9-8412-b8f03c7a3edf",
  "success": false,
  "correlation_id": "abc123def",
  "message": "No dispatch methods available or all failed. Ensure KAFKA_ENABLED=true or SFE_WEBHOOK_URL is configured."
}
```

## Configuration

For SFE dispatch to work, you need at least one dispatch method configured:

### Option 1: Kafka (Recommended for Production)

```bash
# Enable Kafka dispatch
export KAFKA_ENABLED=true
export KAFKA_BOOTSTRAP_SERVERS=kafka:9092
export KAFKA_TOPIC=job-completed

# Optional: Security configuration
export KAFKA_SECURITY_PROTOCOL=SASL_SSL
export KAFKA_SASL_MECHANISM=PLAIN
export KAFKA_SASL_USERNAME=your_username
export KAFKA_SASL_PASSWORD=your_password
```

### Option 2: HTTP Webhook

```bash
# Configure SFE webhook URL
export SFE_WEBHOOK_URL=https://sfe.example.com/api/v1/jobs/completed

# Optional: Authentication
export SFE_WEBHOOK_TOKEN=your_bearer_token
```

### Option 3: Both (Kafka primary, webhook fallback)

Both methods can be configured simultaneously. The system will try Kafka first, then fall back to webhook if Kafka fails.

## API Endpoint Details

### `POST /api/generator/{job_id}/dispatch-to-sfe`

Manually trigger dispatch of a completed job to the Self-Fixing Engineer.

**Path Parameters:**
- `job_id` (required): UUID of the completed job

**Validation:**
- Job ID must be a valid UUID (RFC 4122)
- Job must exist in the database
- Job status must be `COMPLETED`
- Job must have output files

**Status Codes:**
- `200`: Dispatch succeeded or failed gracefully (check `success` field in response)
- `400`: Invalid job ID format, job not completed, or no output files
- `404`: Job not found
- `500`: Internal server error

**Response Fields:**
- `status`: "dispatched" or "failed"
- `job_id`: The job identifier
- `success`: Boolean indicating dispatch success
- `correlation_id`: Unique ID for tracking (use this when contacting support)
- `message`: (optional) Additional context on failure

**Idempotency:**
This operation is idempotent - calling it multiple times with the same job_id will produce the same result. The dispatch service tracks sent events and handles duplicates gracefully.

## Troubleshooting

### "No dispatch methods available" Error

**Cause**: Neither Kafka nor webhook is configured.

**Solution**: Configure at least one dispatch method using the environment variables above.

### "Job must be COMPLETED to dispatch" Error

**Cause**: Trying to dispatch a job that hasn't finished yet.

**Solution**: Wait for the job to complete, then try again.

### "Job has no output files" Error

**Cause**: The job completed but didn't generate any output files.

**Solution**: Check the job logs to understand why no files were generated. This usually indicates a problem during code generation.

### Dispatch Succeeds But SFE Doesn't Process

**Cause**: SFE may not be running or properly configured to receive events.

**Solution**:
1. Check SFE logs to verify it's receiving events
2. Verify Kafka consumers are running (if using Kafka)
3. Check webhook endpoint is accessible (if using webhooks)
4. Use the correlation ID from the dispatch response to track the event

## UI Features

### Auto-Refresh

The Jobs tab automatically refreshes:
- **Every 5 seconds** when there are running jobs (fast mode)
- **Every 15 seconds** when no jobs are running (slow mode)

This ensures the UI stays up-to-date without overwhelming the server.

### File Count Display

Completed jobs automatically fetch and display file counts:
- Shows both input and output file counts
- Fetched in parallel with concurrency limit (max 5 concurrent requests)
- Non-blocking - errors don't prevent UI from loading

### Button Visibility

The "Send to SFE" button only appears when:
- Job status is `COMPLETED`
- Job has output files (`outputCount > 0`)

## Best Practices

1. **Review Before Dispatch**: Always review the generated code before sending to SFE
2. **Check Configuration**: Ensure dispatch methods are configured before attempting manual dispatch
3. **Use Correlation IDs**: Save correlation IDs for support and debugging
4. **Monitor SFE**: Keep an eye on SFE logs after dispatching jobs
5. **Download Files**: Consider downloading generated files before dispatch for backup

## Security Considerations

- **UUID Validation**: Job IDs are validated to prevent injection attacks
- **No Sensitive Data**: Error messages never expose sensitive internal details
- **Correlation IDs**: Include correlation IDs in error messages for support tracking
- **Idempotent Operations**: Safe to retry without side effects
- **Structured Logging**: All dispatch attempts are logged with full context

## Related Documentation

- [ASE Web UI Guide](./ASE_WEB_UI_GUIDE.md) - Complete UI documentation
- [Server Integration](./SERVER_INTEGRATION.md) - API integration details
- [Deployment Guide](./DEPLOYMENT.md) - Production deployment configuration
