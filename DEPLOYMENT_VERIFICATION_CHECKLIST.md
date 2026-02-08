# Deployment Verification Checklist

Use this checklist to verify the WebSocket/SSE bug fixes after deployment.

## Pre-Deployment ✅

- [x] Code changes reviewed and approved
- [x] Security analysis completed (SECURITY_SUMMARY.md)
- [x] Infrastructure impact assessed (INFRASTRUCTURE_IMPACT_ASSESSMENT.md)
- [x] All tests passing
- [x] Python syntax validation passed
- [x] No new dependencies introduced
- [x] Imports verified (all stdlib modules)

## Deployment Process

### Option 1: Docker Compose

```bash
# Pull latest changes
git pull origin main

# Rebuild images (build process unchanged)
docker-compose build

# Deploy with rolling restart
docker-compose up -d

# Verify services are healthy
docker-compose ps
```

### Option 2: Kubernetes (kubectl)

```bash
# Apply unchanged manifests
kubectl apply -k k8s/overlays/production

# Watch rollout status
kubectl rollout status deployment/codefactory -n codefactory-production

# Verify pods are running
kubectl get pods -n codefactory-production
```

### Option 3: Helm

```bash
# Update with existing values
helm upgrade codefactory ./helm/codefactory \
  -n codefactory-production \
  --reuse-values

# Check status
helm status codefactory -n codefactory-production
```

## Post-Deployment Verification ✅

### 1. Health Check
```bash
# Verify service is up
curl http://your-domain/health

# Expected: {"status": "healthy", ...}
```

### 2. WebSocket Connection Test
```bash
# Test WebSocket endpoint
# Using websocat (install: cargo install websocat)
websocat ws://your-domain/api/events/ws

# Or using wscat (install: npm install -g wscat)
wscat -c ws://your-domain/api/events/ws

# Expected: Connection established, welcome message received
```

### 3. SSE Stream Test
```bash
# Test SSE endpoint
curl -N http://your-domain/api/events/sse

# Expected: Stream of events in SSE format
# data: {...}
# 
```

### 4. API Documentation
```bash
# Verify API docs are accessible
curl http://your-domain/docs

# Expected: OpenAPI/Swagger UI HTML
```

### 5. Check Logs

#### Docker Compose
```bash
docker-compose logs -f codefactory | grep -E "WebSocket|event_handler|unsubscribed"
```

#### Kubernetes
```bash
kubectl logs -f deployment/codefactory -n codefactory-production | grep -E "WebSocket|event_handler|unsubscribed"
```

**Look for:**
- ✅ "WebSocket connection accepted"
- ✅ "Subscribed to topic: ..."
- ✅ "Unsubscribed from topic: ..." (on disconnect)
- ✅ "WebSocket connection closed" (with duration)
- ❌ No "WebSocket closed. Code: 1006" errors
- ❌ No "Event queue full, dropping event" warnings for closed connections

### 6. Memory Monitoring

Monitor for memory stability (fixes prevent memory leaks):

```bash
# Docker
docker stats codefactory-platform --no-stream

# Kubernetes
kubectl top pod -n codefactory-production
```

**Expected:**
- Stable memory usage over time
- No continuous growth (indicating leak)

### 7. Connection Tracking

Test connection cleanup:

```bash
# Connect multiple WebSocket clients
for i in {1..5}; do
  wscat -c ws://your-domain/api/events/ws &
done

# Close all connections (Ctrl+C)
# Check logs for proper cleanup

# Expected in logs:
# - 5 "WebSocket connection accepted" messages
# - 5 "Unsubscribed from topic" messages per connection
# - 5 "WebSocket connection closed" messages
```

### 8. Load Test (Optional)

```bash
# Simple load test with multiple connections
for i in {1..10}; do
  curl -N http://your-domain/api/events/sse &
done

# Monitor resource usage
# Verify no memory leaks or connection issues
```

## Rollback Plan (If Needed)

If issues are detected:

### Docker Compose
```bash
# Rollback to previous image
docker-compose down
docker-compose up -d --force-recreate
```

### Kubernetes
```bash
# Rollback deployment
kubectl rollout undo deployment/codefactory -n codefactory-production
```

### Helm
```bash
# Rollback to previous release
helm rollback codefactory -n codefactory-production
```

## Success Criteria ✅

- [ ] Health endpoint responds with 200 OK
- [ ] WebSocket connections can be established
- [ ] SSE streams work correctly
- [ ] API documentation is accessible
- [ ] No 1006 WebSocket errors in logs
- [ ] Proper unsubscription messages on disconnect
- [ ] Memory usage is stable
- [ ] Connection cleanup logs show proper cleanup
- [ ] No ghost subscriber warnings
- [ ] Performance is stable or improved

## Known Issues (None)

No known issues. The fixes are backward compatible and don't change any APIs.

## Support

If you encounter any issues:

1. Check logs for error messages
2. Review WEBSOCKET_FIXES_VERIFICATION.md
3. Review INFRASTRUCTURE_IMPACT_ASSESSMENT.md
4. Review SECURITY_SUMMARY.md
5. Contact support@novatraxlabs.com

---

**Last Updated**: 2026-02-08  
**Version**: 1.0.0  
**Status**: Ready for deployment
