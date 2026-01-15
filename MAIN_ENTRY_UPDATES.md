# Main Entry Subsystem - Important Updates

## Critical Fixes Implemented

This document outlines important updates to the Main Entry Subsystem that affect deployment and usage.

### ⚠️ Breaking Changes

#### 1. sys.path Manipulation Removed

**What Changed:** The `PROJECT_ROOT` sys.path manipulation has been removed from `generator/main/main.py`.

**Impact:** Scripts that relied on running `python main.py` directly from the `generator/main/` directory will no longer work.

**Migration:**
```bash
# OLD (No longer works):
cd generator/main
python main.py

# NEW (Use module invocation):
cd /repo/root
python -m generator.main.main
```

**Reason:** Follows Python packaging best practices (PEP 420, PEP 517/518) and enables proper pip installation.

### 🔒 New Security Features

#### 2. Bootstrap Admin User Creation

**What's New:** Added CLI command to create initial admin user securely.

**Usage:**
```bash
# See full guide in BOOTSTRAP_ADMIN.md
export BOOTSTRAP_API_KEY=$(openssl rand -hex 32)
python -m generator.main.cli admin create-user
```

**Why:** Resolves the "lockout" scenario where no admin user exists after fresh deployment.

**Security:** Requires `BOOTSTRAP_API_KEY` environment variable to prevent unauthorized user creation.

### 🚀 Reliability Improvements

#### 3. Process Isolation for "All" Mode

**What Changed:** The "all" interface mode (API + GUI) now uses improved process isolation.

**Benefits:**
- Prevents event loop conflicts
- Better process lifecycle management
- Graceful shutdown with SIGTERM/SIGKILL escalation
- Health checks with exponential backoff

**Configuration:**
```bash
export API_TARGET_PORT=8000
export API_READINESS_TIMEOUT_SECONDS=120
export API_READINESS_POLL_INTERVAL_SECONDS=0.5
```

#### 4. Enhanced Config Validation

**What Changed:** Configuration reload now performs deep semantic validation.

**Benefits:**
- Validates critical keys (backend, framework, API keys)
- Checks environment variables
- Prevents incomplete configs from breaking running services
- Comprehensive error reporting

**Impact:** Invalid configs will be rejected during reload, keeping the system running with the previous valid configuration.

## Quick Reference

### Running the Application

```bash
# CLI mode
python -m generator.main.main --interface cli

# API mode
python -m generator.main.main --interface api

# GUI mode (TUI)
python -m generator.main.main --interface gui

# All mode (API + GUI in separate processes)
python -m generator.main.main --interface all
```

### Bootstrap Checklist

For new deployments:

- [ ] Generate bootstrap key: `export BOOTSTRAP_API_KEY=$(openssl rand -hex 32)`
- [ ] Start API server: `python -m generator.main.main --interface api &`
- [ ] Create admin user: `python -m generator.main.cli admin create-user`
- [ ] Test login: `curl -X POST http://localhost:8000/api/v1/token -d "username=admin&password=..."`
- [ ] Secure bootstrap key: `unset BOOTSTRAP_API_KEY`

### Troubleshooting

#### "Module not found" errors
```bash
# Ensure running from repo root
cd /path/to/The_Code_Factory_Working_V2
python -m generator.main.main

# Or add to PYTHONPATH
export PYTHONPATH=/path/to/The_Code_Factory_Working_V2:$PYTHONPATH
```

#### "BOOTSTRAP_API_KEY not set"
```bash
export BOOTSTRAP_API_KEY=$(openssl rand -hex 32)
```

#### "Port already in use"
```bash
export API_TARGET_PORT=8001
python -m generator.main.main --interface api
```

#### Config validation failures
```bash
# Check current config
python -m generator.main.cli config show

# View recent errors
python -m generator.main.cli logs --query "validation failed" --limit 10
```

## Documentation

- **Detailed Fixes:** [MAIN_ENTRY_FIXES.md](./MAIN_ENTRY_FIXES.md) - Complete technical documentation
- **Bootstrap Guide:** [BOOTSTRAP_ADMIN.md](./BOOTSTRAP_ADMIN.md) - Step-by-step admin user creation
- **Security Guide:** [SECURITY_DEPLOYMENT_GUIDE.md](./SECURITY_DEPLOYMENT_GUIDE.md) - Production security practices
- **Deployment Guide:** [DEPLOYMENT.md](./DEPLOYMENT.md) - General deployment instructions

## Industry Standards

All fixes comply with:
- ✅ OWASP ASVS 4.0 (Application Security)
- ✅ NIST SP 800-63B (Authentication)
- ✅ PCI DSS 3.2.1 (Security Standards)
- ✅ PEP 420, 517, 518 (Python Packaging)
- ✅ POSIX Signal Handling (Process Management)
- ✅ Docker/Kubernetes Health Check Patterns

## Support

For questions or issues:
- **Documentation:** Check the detailed guides above
- **Logs:** `python -m generator.main.cli logs --limit 50 --query error`
- **Health:** `python -m generator.main.cli health`
- **Issues:** Report at enterprise repository

---

Last Updated: 2026-01-15
Version: 1.0.0
