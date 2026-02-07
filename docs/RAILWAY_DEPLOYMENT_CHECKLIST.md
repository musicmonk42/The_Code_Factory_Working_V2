<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# Railway Deployment Validation Checklist

## Pre-Deployment Checklist

### ✅ Repository Setup
- [ ] Repository is pushed to GitHub
- [ ] All fixes are merged to main branch
- [ ] Latest code includes:
  - [ ] Event loop management fix
  - [ ] Config validation fix
  - [ ] Circular import fix
  - [ ] Clarify endpoint error handling
  - [ ] Docker configuration fixes

### ✅ Railway Configuration Files
- [ ] `railway.toml` - Complete with all environment variables
- [ ] `railway.json` - Build and deploy config
- [ ] `Procfile` - Correct startup command
- [ ] `Dockerfile` - TESTING=1 removed from runtime
- [ ] `RAILWAY_DEPLOYMENT.md` - Updated deployment guide

---

## Deployment Steps

### Step 1: Create Railway Project
- [ ] Go to [Railway](https://railway.app)
- [ ] Click "New Project"
- [ ] Select "Deploy from GitHub repo"
- [ ] Choose repository: musicmonk42/The_Code_Factory_Working_V2
- [ ] Railway creates service and starts initial build

### Step 2: Add Database Plugins
- [ ] Click "+ New" in Railway project
- [ ] Add "PostgreSQL" plugin
  - [ ] Verify `DATABASE_URL` is auto-injected
  - [ ] Check connection in service Variables tab
- [ ] Click "+ New" again
- [ ] Add "Redis" plugin
  - [ ] Verify `REDIS_URL` is auto-injected
  - [ ] Check connection in service Variables tab

### Step 3: Generate Security Keys

Run these commands locally and save outputs:

```bash
# 1. AGENTIC_AUDIT_HMAC_KEY (64 hex characters - CRITICAL)
openssl rand -hex 32
# Output: [SAVE THIS - 64 hex chars]

# 2. ENCRYPTION_KEY (Fernet key)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Output: [SAVE THIS - base64 string]

# 3. SECRET_KEY
python -c "import secrets; print(secrets.token_urlsafe(32))"
# Output: [SAVE THIS]

# 4. JWT_SECRET_KEY
python -c "import secrets; print(secrets.token_urlsafe(32))"
# Output: [SAVE THIS]
```

- [ ] All 4 keys generated and saved securely
- [ ] AGENTIC_AUDIT_HMAC_KEY is exactly 64 hex characters

### Step 4: Configure Environment Variables in Railway

Go to your service → **Variables** tab:

#### Critical Security Variables (REQUIRED)
- [ ] `AGENTIC_AUDIT_HMAC_KEY` = [64-char hex key from step 3]
- [ ] `ENCRYPTION_KEY` = [Fernet key from step 3]
- [ ] `SECRET_KEY` = [Secret from step 3]
- [ ] `JWT_SECRET_KEY` = [Secret from step 3]
- [ ] `OPENAI_API_KEY` = [Your OpenAI API key]

#### Verify Auto-Configured Variables (from railway.toml)
- [ ] `APP_ENV` = production
- [ ] `PRODUCTION_MODE` = 1
- [ ] `DEV_MODE` = 0
- [ ] `APP_STARTUP` = 1
- [ ] `SKIP_IMPORT_TIME_VALIDATION` = 1
- [ ] `MESSAGE_BUS_SHARD_COUNT` = 8
- [ ] `MESSAGE_BUS_WORKERS_PER_SHARD` = 4
- [ ] `ENABLE_MESSAGE_BUS_GUARDIAN` = 1
- [ ] `DB_POOL_SIZE` = 50
- [ ] `ENABLE_STRUCTURED_LOGGING` = 1

#### Verify Plugin-Injected Variables
- [ ] `DATABASE_URL` = postgresql://... (from PostgreSQL plugin)
- [ ] `REDIS_URL` = redis://... (from Redis plugin)

### Step 5: Trigger Deployment
- [ ] Click "Deploy" or push to GitHub to trigger build
- [ ] Monitor build logs in Railway dashboard
- [ ] Wait for build to complete (may take 5-10 minutes)

---

## Post-Deployment Validation

### Step 6: Check Deployment Status
- [ ] Deployment shows "Active" status in Railway
- [ ] No error badges in Railway dashboard
- [ ] Service URL is accessible (e.g., https://your-app.up.railway.app)

### Step 7: Verify Health Endpoints

```bash
# Get your Railway URL
RAILWAY_URL="https://your-app.up.railway.app"

# Test health endpoint
curl $RAILWAY_URL/health
# Expected: {"status":"healthy","timestamp":"..."}
```

- [ ] `/health` returns 200 OK with healthy status
- [ ] Response includes timestamp and version info

```bash
# Test readiness endpoint
curl $RAILWAY_URL/ready
# Expected: {"ready":true,"..."}
```

- [ ] `/ready` returns 200 OK with ready status
- [ ] All subsystems show as ready

```bash
# Test API docs
curl $RAILWAY_URL/docs
# Should return HTML
```

- [ ] `/docs` endpoint accessible
- [ ] OpenAPI documentation loads

### Step 8: Validate Critical Fixes in Logs

In Railway dashboard → **Logs** tab, search for these indicators:

#### ✅ Event Loop Management Fix
```bash
# Search logs for: "ShardedMessageBus initialized"
```
- [ ] Found: "ShardedMessageBus initialized" message
- [ ] No "RuntimeError: no running event loop" errors
- [ ] Message bus shows shard count and worker count

#### ✅ Config Validation Fix
```bash
# Search logs for: "PolicyEngine initialized"
```
- [ ] Found: "PolicyEngine initialized successfully" OR graceful fallback
- [ ] No "Config must be an instance of ArbiterConfig" errors
- [ ] Config validation shows proper type

#### ✅ Circular Import Fix
```bash
# Search logs for: "ImportError" or "circular"
```
- [ ] No circular import errors
- [ ] Clarifier modules load successfully
- [ ] Lazy loading working properly

#### ✅ Audit Logging
```bash
# Search logs for: "AGENTIC_AUDIT_HMAC_KEY"
```
- [ ] No "FATAL: log_audit_event" errors
- [ ] No "no signing key is configured" errors
- [ ] Audit logging initializes successfully

#### ✅ Startup Sequence
```bash
# Look for startup sequence in order:
```
- [ ] "Starting Code Factory API Server"
- [ ] "INITIALIZING PLATFORM CONFIGURATION"
- [ ] "STARTING SERVER WITH BACKGROUND AGENT LOADING"
- [ ] "API Server ready to accept connections"

### Step 9: Test API Functionality

```bash
RAILWAY_URL="https://your-app.up.railway.app"

# Test generator endpoint
curl -X POST $RAILWAY_URL/api/generator/upload \
  -H "Content-Type: multipart/form-data" \
  -F "files=@README.md"

# Should return job_id
```

- [ ] Upload endpoint works
- [ ] Returns valid job_id

```bash
# Check metrics endpoint
curl $RAILWAY_URL/metrics
# Should return Prometheus metrics
```

- [ ] Metrics endpoint accessible
- [ ] Returns valid Prometheus format
- [ ] Shows message_bus, database, and audit metrics

### Step 10: Performance Validation

```bash
# Check response times
time curl $RAILWAY_URL/health

# Should be < 500ms
```

- [ ] Health endpoint responds in < 500ms
- [ ] API endpoints respond in < 2s
- [ ] No timeout errors

```bash
# Check resource usage in Railway dashboard
```

- [ ] CPU usage reasonable (< 80% average)
- [ ] Memory usage stable (no leaks)
- [ ] No crash loops

---

## Security Validation

### Step 11: Security Checklist

#### Secrets Management
- [ ] All secrets stored in Railway Variables (not in code)
- [ ] `AGENTIC_AUDIT_HMAC_KEY` is strong random key
- [ ] `ENCRYPTION_KEY` is valid Fernet key
- [ ] API keys have appropriate permissions
- [ ] No secrets in logs

#### Production Settings
- [ ] `APP_ENV=production` (confirmed in logs)
- [ ] `DEBUG=false` (no debug output in logs)
- [ ] Structured logging enabled
- [ ] Error messages don't leak sensitive data

#### Database Security
- [ ] PostgreSQL connection uses SSL
- [ ] Database credentials not exposed
- [ ] Connection pooling configured
- [ ] Retry logic working

#### Audit Logging
- [ ] Audit events being logged
- [ ] HMAC signatures valid
- [ ] No audit errors in logs
- [ ] Audit trail is queryable

---

## Monitoring Setup

### Step 12: Configure Monitoring

#### Railway Built-in Monitoring
- [ ] Enable Railway metrics in dashboard
- [ ] Set up deployment notifications
- [ ] Configure log retention

#### Health Check Monitoring
```bash
# Set up external monitoring (e.g., UptimeRobot)
# Monitor these endpoints:
```
- [ ] `GET /health` - Every 5 minutes
- [ ] `GET /ready` - Every 5 minutes
- [ ] `GET /metrics` - Every 1 minute (for alerting)

#### Alert Configuration
- [ ] Alert on health check failures (2 consecutive)
- [ ] Alert on high error rate (> 5% over 5 min)
- [ ] Alert on high response time (> 5s)
- [ ] Alert on deployment failures

### Step 13: Set Up Dashboards

#### Key Metrics to Monitor
- [ ] Request rate (requests/second)
- [ ] Error rate (errors/total requests)
- [ ] Response time (p50, p95, p99)
- [ ] Message bus queue depth
- [ ] Database connection pool usage
- [ ] Event loop task count
- [ ] Audit log write rate
- [ ] Memory usage trend
- [ ] CPU usage trend

---

## Rollback Procedure

### If Deployment Fails

#### Option 1: Rollback via Railway UI
1. Go to Railway dashboard → Deployments
2. Find last successful deployment
3. Click "..." menu → "Redeploy"

#### Option 2: Rollback via Git
```bash
# Find last working commit
git log --oneline

# Revert to last working commit
git revert HEAD  # or specific commit

# Push to trigger new deployment
git push
```

#### Option 3: Emergency Disable
If app is crashing, disable temporarily:
```bash
# In Railway Variables, set:
APP_STARTUP=0  # Disable heavy initialization
# Or pause the service in Railway UI
```

---

## Common Issues and Solutions

### Issue: Build Fails

**Symptoms**: Red build status, error in build logs

**Check**:
- [ ] Dockerfile syntax is correct
- [ ] All dependencies in requirements.txt are installable
- [ ] No syntax errors in Python code

**Solutions**:
1. Check build logs for specific error
2. Test Dockerfile locally: `docker build -t test .`
3. Verify all imports are available

### Issue: Health Check Fails

**Symptoms**: Deployment shows unhealthy, restarts repeatedly

**Check**:
- [ ] `/health` endpoint responds with 200
- [ ] No startup crashes in logs
- [ ] All required secrets are set

**Solutions**:
1. Check logs for startup errors
2. Verify DATABASE_URL and REDIS_URL are set
3. Test health endpoint locally
4. Increase healthcheckTimeout if slow startup

### Issue: "No running event loop" Error

**Symptoms**: Crashes with RuntimeError in message bus

**Check**:
- [ ] `MESSAGE_BUS_*` variables are set correctly
- [ ] Latest code with event loop fix is deployed

**Solutions**:
1. Verify railway.toml has all MESSAGE_BUS variables
2. Check service Variables tab shows them
3. Redeploy with latest code

### Issue: "No signing key configured"

**Symptoms**: Crashes when trying to log audit events

**Check**:
- [ ] `AGENTIC_AUDIT_HMAC_KEY` is set
- [ ] Key is exactly 64 hex characters
- [ ] No typos or extra whitespace

**Solutions**:
1. Regenerate key: `openssl rand -hex 32`
2. Add to Railway Variables
3. Redeploy

### Issue: Database Connection Fails

**Symptoms**: Can't connect to PostgreSQL

**Check**:
- [ ] PostgreSQL plugin is added
- [ ] `DATABASE_URL` is injected
- [ ] Database is healthy

**Solutions**:
1. Restart PostgreSQL plugin
2. Check plugin health status
3. Verify connection string format
4. Check Railway service logs

---

## Performance Tuning

### If Response Times Are Slow

#### Vertical Scaling
- [ ] Upgrade Railway plan for more resources
- [ ] Increase memory allocation
- [ ] Add more CPU cores

#### Optimize Configuration
```bash
# Adjust in Railway Variables:
MESSAGE_BUS_SHARD_COUNT=16  # More parallelism
DB_POOL_SIZE=100  # More connections
WORKER_COUNT=8  # More workers
```

#### Database Optimization
- [ ] Add database indexes
- [ ] Enable connection pooling
- [ ] Use read replicas if needed

### If Memory Usage Is High

#### Check for Leaks
```bash
# Monitor memory over time in Railway dashboard
```

#### Optimization
- [ ] Reduce MESSAGE_BUS_SHARD_COUNT
- [ ] Reduce DB_POOL_SIZE
- [ ] Enable garbage collection logging
- [ ] Review large objects in memory

---

## Maintenance Tasks

### Regular Tasks

#### Weekly
- [ ] Check error logs for anomalies
- [ ] Review performance metrics
- [ ] Check disk usage
- [ ] Verify backup integrity

#### Monthly
- [ ] Rotate secrets (HMAC keys, etc.)
- [ ] Update dependencies
- [ ] Review and optimize database
- [ ] Load testing

#### Quarterly
- [ ] Security audit
- [ ] Disaster recovery drill
- [ ] Capacity planning review
- [ ] Documentation update

---

## Success Criteria

### ✅ Deployment is successful if:

- [x] All health checks pass
- [x] No critical errors in logs
- [x] All 4 critical fixes validated:
  - Event loop management working
  - Config validation working
  - No circular imports
  - Audit logging working
- [x] API endpoints responding correctly
- [x] Performance is acceptable (< 2s response time)
- [x] Security best practices followed
- [x] Monitoring and alerts configured

---

## Final Checklist

- [ ] All environment variables configured
- [ ] All security keys generated and set
- [ ] PostgreSQL and Redis plugins added
- [ ] Deployment is active and healthy
- [ ] Health endpoints return 200 OK
- [ ] All critical fixes validated in logs
- [ ] No errors or warnings in startup logs
- [ ] API functionality tested
- [ ] Performance is acceptable
- [ ] Monitoring configured
- [ ] Team notified of deployment
- [ ] Documentation updated
- [ ] Rollback procedure tested

**Deployment Date**: _______________

**Deployed By**: _______________

**Validation Completed By**: _______________

**Production URL**: _______________

---

## Support Contacts

- **Railway Support**: https://help.railway.app
- **GitHub Issues**: https://github.com/musicmonk42/The_Code_Factory_Working_V2/issues
- **Documentation**: See `RAILWAY_DEPLOYMENT.md`, `ENVIRONMENT_VARIABLES.md`, `STARTUP_RUNTIME_FIXES_IMPLEMENTATION.md`
