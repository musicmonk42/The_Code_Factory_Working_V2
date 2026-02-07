<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

================================================================================
SECURITY AUDIT REPORT
================================================================================

Total Findings: 69
  Critical: 12
  High: 39
  Medium: 18
  Low: 0
  Info: 0


================================================================================
CRITICAL SEVERITY FINDINGS
================================================================================

1. Hardcoded token
   File: intent_capture/cli.py
   Line: 185
   Context: token=')[1] if '

2. Hardcoded token
   File: intent_capture/cli.py
   Line: 185
   Context: token=' in path else '

3. Hardcoded secret
   File: plugins/grpc_runner.py
   Line: 165
   Context: SECRET = "GRPC_TLS_CERT_PATH"

4. Hardcoded secret
   File: plugins/grpc_runner.py
   Line: 166
   Context: SECRET  = "GRPC_TLS_KEY_PATH"

5. Hardcoded secret
   File: plugins/grpc_runner.py
   Line: 167
   Context: SECRET   = "GRPC_TLS_CA_PATH"

6. Hardcoded secret
   File: plugins/grpc_runner.py
   Line: 168
   Context: SECRET = "GRPC_ENDPOINT_ALLOWLIST"

7. Hardcoded token
   File: arbiter/explainable_reasoner/explainable_reasoner.py
   Line: 1372
   Context: token="dummy-token-for-testing"

8. Hardcoded password
   File: self_healing_import_fixer/analyzer/core_security.py
   Line: 486
   Context: password = 'mysecretpassword'

9. Hardcoded password
   File: self_healing_import_fixer/import_fixer/fixer_validate.py
   Line: 1007
   Context: password = 'mysecretpassword'

10. Hardcoded password
   File: self_healing_import_fixer/import_fixer/fixer_validate.py
   Line: 1062
   Context: password = 'hardcoded'

   ... and 2 more findings


================================================================================
HIGH SEVERITY FINDINGS
================================================================================

1. Dangerous function __import__ usage
   File: test_engine_integration.py
   Line: 1719
   Context: __import__(

2. Dangerous function eval usage
   File: security_audit.py
   Line: 202
   Context: eval(

3. Dangerous function exec usage
   File: security_audit.py
   Line: 203
   Context: exec(

4. Dangerous function __import__ usage
   File: security_audit.py
   Line: 203
   Context: __import__(

5. Permissive CORS configuration
   File: security_audit.py
   Line: 257
   Context: allow_origins=["*"]

6. Dangerous function eval usage
   File: arbiter/explorer.py
   Line: 720
   Context: eval(

7. Permissive CORS configuration
   File: intent_capture/api.py
   Line: 231
   Context: allow_origins=["*"]

8. Dangerous function eval usage
   File: test_generation/utils.py
   Line: 11251
   Context: eval(

9. Dangerous function exec usage
   File: test_generation/utils.py
   Line: 9035
   Context: exec(

10. Dangerous function exec usage
   File: test_generation/backends.py
   Line: 2164
   Context: exec(

   ... and 29 more findings


================================================================================
MEDIUM SEVERITY FINDINGS
================================================================================

1. API routes may lack input validation
   File: main.py
   Context: Review route handlers

2. Debug mode enabled
   File: security_audit.py
   Line: 143
   Context: DEBUG=True

3. Weak hash algorithm MD5
   File: arbiter/otel_config.py
   Line: 519
   Context: hashlib.md5

4. Weak hash algorithm MD5
   File: arbiter/arbiter.py
   Line: 2021
   Context: hashlib.md5

5. Weak hash algorithm MD5
   File: arbiter/logging_utils.py
   Line: 179
   Context: hashlib.md5

6. Weak hash algorithm MD5
   File: arbiter/feedback.py
   Line: 191
   Context: hashlib.md5

7. Weak hash algorithm MD5
   File: arbiter/feedback.py
   Line: 366
   Context: hashlib.md5

8. Weak hash algorithm MD5
   File: simulation/quantum.py
   Line: 406
   Context: hashlib.md5

9. Weak hash algorithm MD5
   File: envs/evolution.py
   Line: 194
   Context: hashlib.md5

10. Weak hash algorithm MD5
   File: arbiter/policy/core.py
   Line: 166
   Context: hashlib.md5

   ... and 8 more findings


================================================================================
RECOMMENDATIONS
================================================================================

1. Remove all hardcoded secrets and use environment variables or secret managers
2. Implement parameterized queries to prevent SQL injection
3. Add input validation to all API endpoints
4. Configure proper CORS policies
5. Review and strengthen authentication mechanisms
6. Update vulnerable dependencies
7. Disable debug mode in production
8. Use strong cryptographic algorithms (SHA-256+, AES-256)
9. Implement rate limiting on all public APIs
10. Add security headers (CSP, X-Frame-Options, etc.)
        