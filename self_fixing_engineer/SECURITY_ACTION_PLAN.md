# Security Remediation Action Plan

**Priority:** IMMEDIATE  
**Timeline:** This Week  
**Status:** IN PROGRESS  

## Critical Security Issues (12 Total)

### 1. Hardcoded Secrets in Code

#### Issue 1.1: intent_capture/cli.py (Line 185)
- **Severity:** CRITICAL
- **Issue:** Hardcoded token in path parsing
- **Action Required:**
  ```python
  # Replace hardcoded token with environment variable
  token = os.getenv('INTENT_CAPTURE_TOKEN')
  if not token:
      raise ValueError("INTENT_CAPTURE_TOKEN environment variable required")
  ```
- **Assigned:** Security Team
- **Due Date:** Day 1
- **Status:** [ ] TODO

#### Issue 1.2: plugins/grpc_runner.py (Lines 165-168)
- **Severity:** CRITICAL  
- **Issue:** Hardcoded TLS paths
- **Action Required:**
  ```python
  # Lines 165-168 - Use environment variables
  GRPC_TLS_CERT_PATH = os.getenv('GRPC_TLS_CERT_PATH')
  GRPC_TLS_KEY_PATH = os.getenv('GRPC_TLS_KEY_PATH')
  GRPC_TLS_CA_PATH = os.getenv('GRPC_TLS_CA_PATH')
  GRPC_ENDPOINT_ALLOWLIST = os.getenv('GRPC_ENDPOINT_ALLOWLIST', '').split(',')
  ```
- **Assigned:** Platform Team
- **Due Date:** Day 1
- **Status:** [ ] TODO

#### Issue 1.3: arbiter/explainable_reasoner/explainable_reasoner.py (Line 1372)
- **Severity:** CRITICAL (Testing Code)
- **Issue:** Dummy token in test code
- **Action Required:** Use TEST_AUTH_TOKEN environment variable
- **Assigned:** Testing Team
- **Due Date:** Day 1
- **Status:** [x] COMPLETED

#### Issue 1.4: self_healing_import_fixer/analyzer/core_security.py (Line 486)
- **Severity:** CRITICAL
- **Issue:** Hardcoded password 'mysecretpassword'
- **Action Required:**
  ```python
  password = os.getenv('DB_PASSWORD')
  if not password:
      raise ValueError("DB_PASSWORD environment variable required")
  ```
- **Assigned:** Backend Team
- **Due Date:** Day 1
- **Status:** [ ] TODO

#### Issue 1.5: self_healing_import_fixer/import_fixer/fixer_validate.py (Lines 1007, 1062)
- **Severity:** CRITICAL
- **Issue:** Multiple hardcoded passwords
- **Action Required:** Replace both instances with environment variables
- **Assigned:** Backend Team
- **Due Date:** Day 1
- **Status:** [ ] TODO

---

## High-Severity Issues (39 Total)

### 2. Dangerous Function Usage

#### Issue 2.1: eval() Usage
- **Severity:** HIGH
- **Files Affected:**
  - arbiter/explorer.py (Line 720)
  - test_generation/utils.py (Line 11251)
  - security_audit.py (Line 202)
- **Action Required:**
  - Replace eval() with ast.literal_eval() for safe evaluation
  - Use safer alternatives like json.loads() where appropriate
  - If dynamic execution required, use sandboxed environment
- **Assigned:** Development Team
- **Due Date:** Week 1
- **Status:** [ ] TODO

#### Issue 2.2: exec() Usage  
- **Severity:** HIGH
- **Files Affected:**
  - test_generation/utils.py (Line 9035)
  - test_generation/backends.py (Line 2164)
  - security_audit.py (Line 203)
- **Action Required:**
  - Replace exec() with safer alternatives
  - Use subprocess.run() with proper sandboxing if needed
  - Validate and sanitize all inputs before any dynamic execution
- **Assigned:** Development Team
- **Due Date:** Week 1
- **Status:** [ ] TODO

#### Issue 2.3: __import__() Usage
- **Severity:** HIGH
- **Files Affected:**
  - test_engine_integration.py (Line 1719)
  - security_audit.py (Line 203)
- **Action Required:**
  - Use importlib.import_module() instead
  - Validate module names against allowlist
  - Never pass user input directly to import functions
- **Assigned:** Development Team
- **Due Date:** Week 1
- **Status:** [ ] TODO

### 3. CORS Misconfigurations

#### Issue 3.1: Permissive CORS (allow_origins=["*"])
- **Severity:** HIGH
- **Files Affected:**
  - intent_capture/api.py (Line 231)
  - main.py
- **Action Required:**
  ```python
  # Replace wildcard with specific origins
  app.add_middleware(
      CORSMiddleware,
      allow_origins=os.getenv('API_CORS_ORIGINS', 'http://localhost:3000').split(','),
      allow_credentials=True,
      allow_methods=["GET", "POST", "PUT", "DELETE"],
      allow_headers=["*"],
  )
  ```
- **Assigned:** Backend Team
- **Due Date:** Week 1
- **Status:** [ ] TODO

### 4. SQL Injection Risks

#### Issue 4.1: String Formatting in SQL Queries
- **Severity:** HIGH
- **Action Required:**
  - Audit all database query constructions
  - Ensure parameterized queries everywhere
  - Use SQLAlchemy ORM properly
- **Files to Audit:**
  - All files using .execute() methods
  - Search for f-strings or % formatting in SQL contexts
- **Assigned:** Database Team
- **Due Date:** Week 1
- **Status:** [ ] TODO

---

## Medium-Severity Issues (18 Total)

### 5. Weak Cryptographic Algorithms

#### Issue 5.1: MD5 Hash Usage
- **Severity:** MEDIUM
- **Files Affected (10 files):**
  - arbiter/otel_config.py (Line 519)
  - arbiter/arbiter.py (Line 2021)
  - arbiter/logging_utils.py (Line 179)
  - arbiter/feedback.py (Lines 191, 366)
  - simulation/quantum.py (Line 406)
  - envs/evolution.py (Line 194)
  - arbiter/policy/core.py (Line 166)
  - And 2 more...
- **Action Required:**
  ```python
  # Replace MD5 with SHA-256
  import hashlib
  
  # Before:
  hash_value = hashlib.md5(data).hexdigest()
  
  # After:
  hash_value = hashlib.sha256(data).hexdigest()
  ```
- **Assigned:** Development Team
- **Due Date:** Week 2
- **Status:** [ ] TODO

### 6. Debug Mode in Production

#### Issue 6.1: Debug Mode Enabled
- **Severity:** MEDIUM
- **Files Affected:**
  - Various configuration files
- **Action Required:**
  - Ensure DEBUG=False in production
  - Add environment checks: `DEBUG = os.getenv('DEBUG', 'false').lower() == 'true'`
  - Remove any hardcoded DEBUG=True
- **Assigned:** DevOps Team
- **Due Date:** Week 1
- **Status:** [ ] TODO

### 7. Missing Input Validation

#### Issue 7.1: API Routes Without Validation
- **Severity:** MEDIUM
- **Files Affected:**
  - main.py
  - Various API endpoint files
- **Action Required:**
  - Add Pydantic models for all request bodies
  - Use FastAPI Depends() for validation
  - Add input sanitization for all string inputs
  - Validate all numeric ranges
- **Assigned:** Backend Team
- **Due Date:** Week 2
- **Status:** [ ] TODO

---

## Implementation Checklist

### Day 1 (Critical Secrets)
- [ ] Create .env.example file ✓ (COMPLETED)
- [ ] Fix Issue 1.1: intent_capture/cli.py token
- [ ] Fix Issue 1.2: plugins/grpc_runner.py TLS paths
- [x] Fix Issue 1.3: explainable_reasoner.py test token (COMPLETED)
- [ ] Fix Issue 1.4: core_security.py password
- [ ] Fix Issue 1.5: fixer_validate.py passwords
- [ ] Test all fixes locally
- [ ] Update documentation

### Week 1 (High-Severity)
- [ ] Replace all eval() usage (Issue 2.1)
- [ ] Replace all exec() usage (Issue 2.2)
- [ ] Fix __import__() usage (Issue 2.3)
- [ ] Fix CORS configurations (Issue 3.1)
- [ ] Audit SQL queries (Issue 4.1)
- [ ] Disable debug mode (Issue 6.1)
- [ ] Add comprehensive tests for security fixes
- [ ] Code review all changes
- [ ] Update security documentation

### Week 2 (Medium-Severity)
- [ ] Replace MD5 with SHA-256 (Issue 5.1)
- [ ] Add input validation to all APIs (Issue 7.1)
- [ ] Add authentication to unprotected endpoints
- [ ] Implement rate limiting
- [ ] Add security headers
- [ ] Run security audit again
- [ ] Update SECURITY_AUDIT_REPORT.md

---

## Testing Requirements

### Security Testing Checklist
- [ ] Test all environment variable loading
- [ ] Verify secrets are not logged
- [ ] Test authentication on all endpoints
- [ ] Test input validation with malicious payloads
- [ ] Test CORS policies
- [ ] Verify no debug output in production mode
- [ ] Run automated security scanner
- [ ] Perform manual penetration testing

### Integration Testing
- [ ] Test with all dependencies installed
- [ ] Run full test suite
- [ ] Verify no regressions
- [ ] Test in Docker environment
- [ ] Load testing
- [ ] Performance benchmarking

---

## Deployment Checklist

### Pre-Deployment
- [ ] All critical issues fixed
- [ ] All high-severity issues fixed
- [ ] Security audit shows no critical/high issues
- [ ] All tests passing
- [ ] Code reviewed and approved
- [ ] Documentation updated
- [ ] .env configured for production
- [ ] Secrets in secret manager (not .env)

### Production Configuration
- [ ] Use PostgreSQL (not SQLite)
- [ ] Redis cluster configured
- [ ] HTTPS/TLS enabled
- [ ] Firewall rules configured
- [ ] Rate limiting enabled
- [ ] Monitoring and alerting configured
- [ ] Backup and recovery tested
- [ ] Rollback plan documented

### Post-Deployment
- [ ] Monitor logs for errors
- [ ] Check security alerts
- [ ] Verify metrics
- [ ] Test critical paths
- [ ] Document lessons learned
- [ ] Schedule follow-up security audit

---

## Dependencies Installation

### Immediate
```bash
cd self_fixing_engineer
pip install -r requirements.txt
```

### Verify Installation
```bash
python -c "import httpx, aiofiles, structlog, circuitbreaker; print('Core deps OK')"
```

### Test All Engines
```bash
pytest test_engine_integration.py -v
```

---

## Documentation Updates Required

- [ ] Update README.md with security best practices
- [ ] Create SECURITY.md with vulnerability reporting process
- [ ] Update API documentation with authentication requirements
- [ ] Document all environment variables
- [ ] Create deployment guide
- [ ] Update CONTRIBUTING.md with security guidelines

---

## Success Criteria

### Week 1 Completion
- ✓ All 12 critical secrets removed
- ✓ .env.example created and documented
- ✓ All high-severity issues fixed or mitigated
- ✓ Security tests added and passing
- ✓ Documentation updated

### Week 2 Completion
- ✓ All medium-severity issues fixed
- ✓ Complete integration test suite
- ✓ Performance baseline established
- ✓ Production deployment plan ready

### Month 1 Completion
- ✓ Production deployment successful
- ✓ Load testing completed
- ✓ Monitoring and alerting operational
- ✓ Zero critical security issues
- ✓ All documentation complete

---

**Last Updated:** 2025-11-21  
**Next Review:** 2025-11-22  
**Owner:** Security Team Lead
