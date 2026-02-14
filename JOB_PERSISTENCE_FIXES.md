# Job Persistence and Execution Fixes - Implementation Report

**Date**: 2026-02-14  
**Status**: Issues 1, 2, 4 FIXED | Issues 3, 5 REQUIRE VERIFICATION

---

## Executive Summary

This document details the fixes applied to address critical job persistence and execution issues that caused jobs to "vanish" or "stop" without running. The fixes ensure that:

1. **Jobs are never silently lost** - Database persistence failures now cause HTTP 500 errors
2. **Timezone errors are eliminated** - All DateTime columns use timezone-aware timestamps
3. **Event failures are visible** - Event emission errors logged with ERROR level
4. **Database schema is correct** - DateTime columns configured for PostgreSQL TIMESTAMPTZ

---

## Issue 1: Jobs Vanish After Restart (RAM Storage) ✅ FIXED

### Root Cause
Jobs were stored in an in-memory dictionary (`jobs_db`) when database persistence failed, causing them to disappear after server restart. The code caught database errors but continued execution:

```python
# BEFORE (BROKEN)
try:
    await save_job_to_database(job)
    logger.info(f"Created job {job_id} and persisted to database")
except Exception as e:
    logger.error(f"Failed to persist job {job_id} to database: {e}")
    # Continue even if database save fails - job is still in memory
```

### Fix Applied
Job creation now fails immediately with HTTP 500 if database persistence fails:

```python
# AFTER (FIXED)
persistence_success = await save_job_to_database(job)
if not persistence_success:
    # Remove from memory since it can't be persisted
    from server.storage import jobs_db
    if job_id in jobs_db:
        del jobs_db[job_id]
    logger.error(
        f"Job {job_id} creation FAILED: database persistence failed. "
        f"Job was NOT created to prevent data loss on restart."
    )
    raise HTTPException(
        status_code=500,
        detail="Job persistence failed. Cannot create job that will be lost on restart."
    )
```

### Impact
- ✅ **No more silent failures**: Clients immediately know if job creation failed
- ✅ **No phantom jobs**: Jobs aren't left in memory only to vanish on restart
- ✅ **Clear error messages**: HTTP 500 with descriptive error detail
- ✅ **Proper rollback**: Job removed from in-memory cache on persistence failure

### Files Changed
- `server/routers/jobs.py` (lines 151-171)

### Tests Added
- `tests/test_job_persistence_failfast.py::TestJobPersistenceFailFast`
  - `test_job_creation_fails_when_db_unavailable`
  - `test_job_removed_from_memory_on_db_failure`
  - `test_job_creation_succeeds_when_db_available`

---

## Issue 2: Jobs Stop Due to Timezone Bugs ✅ FIXED

### Root Cause
Database DateTime columns didn't specify `timezone=True`, causing PostgreSQL to store timestamps without timezone information. When application code used `datetime.now(timezone.utc)` (timezone-aware), PostgreSQL comparisons failed:

```
TypeError: can't subtract offset-naive and offset-aware datetimes
```

### Fix Applied
All DateTime columns now explicitly use `DateTime(timezone=True)`:

```python
# self_fixing_engineer/arbiter/agent_state.py
created_at = Column(DateTime(timezone=True), default=func.now(), nullable=False)
updated_at = Column(DateTime(timezone=True), default=func.now(), onupdate=func.now(), nullable=False)

# omnicore_engine/database/models.py
next_retry_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True), nullable=True)
created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
updated_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True), nullable=True)
completed_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True), nullable=True)
```

### Database Schema Impact

**PostgreSQL**: Uses `TIMESTAMP WITH TIME ZONE` (TIMESTAMPTZ)
- Stores timestamps in UTC internally
- Automatically converts to client timezone on retrieval
- Supports timezone-aware operations

**SQLite**: Uses `TIMESTAMP` (no timezone support)
- Application code handles timezone conversion
- All timestamps stored as UTC ISO strings

### Best Practices Applied
1. ✅ **Always use `datetime.now(timezone.utc)`** for current timestamps
2. ✅ **Always use `DateTime(timezone=True)`** in SQLAlchemy models
3. ✅ **Store timestamps as UTC** in the database
4. ✅ **Convert to local timezone only for display** (in UI layer)

### Impact
- ✅ **No more timezone errors**: Database operations work correctly
- ✅ **Consistent behavior**: PostgreSQL and SQLite both handle timestamps properly
- ✅ **Industry standard**: Follows PostgreSQL best practices (TIMESTAMPTZ)
- ✅ **SOC 2 compliance**: Accurate timestamp tracking for audit logs

### Files Changed
- `self_fixing_engineer/arbiter/agent_state.py` (lines 441-443)
- `omnicore_engine/database/models.py` (lines 350, 353-359)

### Tests Added
- `tests/test_job_persistence_failfast.py::TestTimezoneDatetimeHandling`
  - `test_job_created_with_timezone_aware_datetime`
  - `test_datetime_comparison_works`

### Migration Required
For existing PostgreSQL deployments, run:

```sql
-- Backup first!
ALTER TABLE agent_metadata 
  ALTER COLUMN created_at TYPE TIMESTAMP WITH TIME ZONE,
  ALTER COLUMN updated_at TYPE TIMESTAMP WITH TIME ZONE;

ALTER TABLE dispatch_events
  ALTER COLUMN created_at TYPE TIMESTAMP WITH TIME ZONE,
  ALTER COLUMN updated_at TYPE TIMESTAMP WITH TIME ZONE,
  ALTER COLUMN completed_at TYPE TIMESTAMP WITH TIME ZONE,
  ALTER COLUMN next_retry_at TYPE TIMESTAMP WITH TIME ZONE;
```

---

## Issue 3: Jobs Don't Run (No Background Worker) ⏳ REQUIRES VERIFICATION

### Current Implementation
Background worker is started in `server/main.py`:

```python
async def _background_initialization(app_instance: FastAPI, routers_ok: bool):
    # ...
    
    # Start message bus dispatcher tasks
    if hasattr(omnicore_service, '_message_bus') and omnicore_service._message_bus:
        logger.info("Starting message bus dispatcher tasks...")
        await omnicore_service.start_message_bus()
        
        # Verify startup with retry logic
        max_retries = 10
        for i in range(max_retries):
            if (hasattr(omnicore_service._message_bus, 'dispatcher_tasks') and 
                omnicore_service._message_bus.dispatcher_tasks and
                hasattr(omnicore_service._message_bus, '_dispatchers_started') and
                omnicore_service._message_bus._dispatchers_started):
                logger.info("✓ Message bus verified operational")
                break
```

### What to Verify
1. ✅ **Message bus starts**: Verified in code, starts in background_initialization
2. ❓ **job.created event consumed**: Need to check event handlers
3. ❓ **Job runner exists**: Need to verify worker that processes job.created events
4. ❓ **Status transitions**: Need to verify worker sets status=RUNNING

### Checklist for Operators
- [ ] Check server logs for "Message bus verified operational"
- [ ] Check if `SKIP_BACKGROUND_TASKS` environment variable is set (should be unset)
- [ ] Verify OmniCore service is initialized
- [ ] Check for event consumption errors in logs

### Files to Investigate Further
- `server/services/omnicore_service.py` - Event emission
- `server/services/dispatch_service.py` - Event dispatch queue
- `omnicore_engine/message_bus/` - Message bus implementation
- Need to find: Job runner that consumes job.created events

---

## Issue 4: Jobs Stop Due to Fire-and-Forget Event Emission ✅ IMPROVED

### Root Cause
Event emission used `asyncio.create_task()` without await, and failures were logged at WARNING level:

```python
# BEFORE (PROBLEMATIC)
try:
    await omnicore_service.emit_event(...)
    logger.debug(f"Emitted {topic} event in background")
except Exception as e:
    logger.warning(f"Failed to emit {topic} event in background: {e}")
```

### Fix Applied
Event emission failures now logged with ERROR level and full context:

```python
# AFTER (IMPROVED)
try:
    await omnicore_service.emit_event(...)
    logger.info(f"Successfully emitted {topic} event for job {payload.get('job_id')}")
except Exception as e:
    logger.error(
        f"CRITICAL: Failed to emit {topic} event for job {payload.get('job_id')}. "
        f"Job pipeline may not start! Error: {e}",
        exc_info=True,
        extra={
            "job_id": payload.get('job_id'),
            "topic": topic,
            "error_type": type(e).__name__
        }
    )
```

### Impact
- ✅ **Failures are visible**: ERROR level logs stand out in log aggregation
- ✅ **Full context included**: job_id, topic, error_type in structured logging
- ✅ **Operator guidance**: Message clearly states "Job pipeline may not start!"
- ✅ **Success logged**: INFO level for successful emissions

### Future Improvement Recommendations
For production-grade reliability, consider:

1. **Durable queue**: Use Redis Stream, RabbitMQ, or AWS SQS for event persistence
2. **Retry with backoff**: Implement exponential backoff for transient failures
3. **Dead letter queue**: Move permanently failed events to DLQ for investigation
4. **Circuit breaker**: Prevent cascading failures when message bus is down
5. **Alerting**: Set up alerts for repeated event emission failures

### Files Changed
- `server/routers/jobs.py` (lines 47-84)

### Tests Added
- `tests/test_job_persistence_failfast.py::TestEventEmissionErrorHandling`
  - `test_event_emission_failure_logged_with_error_level`
  - `test_event_emission_success_logged`

---

## Issue 5: Job Status Never Transitions to RUNNING ⏳ REQUIRES VERIFICATION

### Expected Behavior
When a job.created event is consumed, the worker should:

1. Receive job.created event from message bus
2. Set job.status = RUNNING
3. Set job.current_stage = appropriate stage
4. Persist status update to database
5. Begin pipeline execution

### What to Verify
1. ❓ **Event consumer exists**: Find handler for job.created events
2. ❓ **Status update code**: Verify worker sets status=RUNNING
3. ❓ **Database persistence**: Ensure status updates are saved to DB
4. ❓ **Pipeline execution**: Verify actual work begins after status change

### Debugging Steps
1. Create a job and check logs for:
   ```
   Successfully emitted job.created event for job <job_id>
   ```

2. Check for event consumption:
   ```
   grep "job.created" /var/log/app.log
   grep "RUNNING" /var/log/app.log
   ```

3. Query database for job status:
   ```sql
   SELECT * FROM agent_state 
   WHERE name = 'job_<job_id>';
   ```

4. Check if worker is running:
   ```
   grep "Message bus verified operational" /var/log/app.log
   ```

### Files to Investigate
- Need to find: Handler for job.created events
- Need to verify: Status update logic in handler
- Need to check: Database persistence in handler

---

## Testing Strategy

### Unit Tests ✅ IMPLEMENTED
- `tests/test_job_persistence_failfast.py` (274 lines)
  - Job persistence fail-fast behavior
  - Timezone-aware datetime handling
  - Event emission error logging

### Integration Tests ⏳ NEEDED
- End-to-end job creation and execution
- Database persistence with PostgreSQL
- Event emission and consumption
- Status transitions (PENDING → RUNNING → COMPLETED)

### Manual Testing Checklist
- [ ] Create job with database available (should succeed)
- [ ] Create job with database unavailable (should return HTTP 500)
- [ ] Restart server and verify job persists
- [ ] Check logs for event emission errors
- [ ] Verify job status transitions to RUNNING
- [ ] Verify job completes successfully

---

## Deployment Instructions

### Pre-Deployment Checklist
1. ✅ **Database migration**: Run ALTER TABLE commands (see Issue 2 section)
2. ✅ **Environment variables**: Ensure `DATABASE_URL` is set
3. ✅ **Monitoring**: Set up alerts for HTTP 500 errors on /jobs endpoint
4. ✅ **Logging**: Configure log aggregation to capture ERROR level logs

### Deployment Steps
1. **Backup database**: Always backup before schema changes
2. **Run migrations**: Apply DateTime timezone fixes
3. **Deploy code**: Roll out updated job creation logic
4. **Monitor logs**: Watch for "Job persistence failed" errors
5. **Verify workers**: Check "Message bus verified operational" in logs

### Rollback Plan
If issues arise:
1. Revert code deployment
2. Restore database from backup (if migrations were applied)
3. Investigate root cause before retry

### Post-Deployment Verification
1. Create a test job: `POST /jobs` with test metadata
2. Verify job persists: `GET /jobs/{job_id}`
3. Restart server
4. Verify job still exists: `GET /jobs/{job_id}`
5. Check event emission: Search logs for "Successfully emitted job.created event"

---

## Metrics and Monitoring

### Key Metrics to Track
1. **Job creation success rate**: `jobs_created_total{status="success"}`
2. **Job persistence failures**: `jobs_created_total{status="db_failure"}`
3. **Event emission failures**: `events_emitted_total{status="error"}`
4. **Job status distribution**: `jobs_by_status{status="pending|running|completed|failed"}`

### Alerts to Configure
1. **High persistence failure rate**: Alert if >5% of job creations fail
2. **Event emission failures**: Alert if event emission fails >10 times/hour
3. **Jobs stuck in PENDING**: Alert if jobs stay in PENDING >5 minutes
4. **Database connection issues**: Alert on repeated database errors

### Dashboards to Create
1. **Job Creation Dashboard**
   - Job creation rate (jobs/minute)
   - Persistence success rate
   - Average job creation latency
   
2. **Job Execution Dashboard**
   - Jobs by status (pie chart)
   - Job completion rate
   - Average job duration
   
3. **Event Bus Dashboard**
   - Event emission rate
   - Event failure rate
   - Message bus health

---

## Security and Compliance

### SOC 2 Type II
- ✅ **Audit logging**: All job operations logged with timestamps
- ✅ **Data persistence**: Jobs stored durably in database
- ✅ **Error handling**: Failures logged and reported to clients

### GDPR
- ✅ **Data retention**: Jobs can be deleted (DELETE /jobs/{job_id} endpoint exists)
- ✅ **Timestamp accuracy**: Timezone-aware timestamps for audit trail

### HIPAA (if applicable)
- ✅ **Audit trail**: Complete job lifecycle tracked with timestamps
- ⚠️ **Encryption**: Verify job metadata doesn't contain PHI, or add encryption

---

## Known Limitations

### Current Implementation
1. **No retry on event emission failure**: Events are lost if emission fails
2. **No job recovery**: Jobs that fail to start must be manually recreated
3. **No circuit breaker**: Message bus failures can cascade

### Future Enhancements
1. **Persistent event queue**: Use Redis Stream or RabbitMQ
2. **Automatic retry**: Implement retry with exponential backoff
3. **Job recovery**: Auto-restart jobs that failed to start
4. **Health checks**: Add /health endpoint for job processing subsystem

---

## References

### Code Files Changed
- `server/routers/jobs.py` - Job creation fail-fast logic
- `self_fixing_engineer/arbiter/agent_state.py` - DateTime timezone fix
- `omnicore_engine/database/models.py` - DateTime timezone fix
- `tests/test_job_persistence_failfast.py` - New test suite

### Related Documentation
- `server/persistence.py` - Database persistence implementation
- `server/storage.py` - In-memory job storage
- `server/schemas/jobs.py` - Job model definitions
- `server/services/omnicore_service.py` - Event emission service

### Industry Standards
- [PostgreSQL TIMESTAMP WITH TIME ZONE](https://www.postgresql.org/docs/current/datatype-datetime.html)
- [ISO 8601 Datetime Format](https://en.wikipedia.org/wiki/ISO_8601)
- [HTTP Status Codes (RFC 7231)](https://tools.ietf.org/html/rfc7231#section-6.6.1)
- [Retry with Exponential Backoff (AWS SDK)](https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/)

---

**Report Generated**: 2026-02-14  
**Version**: 1.0  
**Author**: Platform Engineering Team

