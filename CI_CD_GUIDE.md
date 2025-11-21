# CI/CD Pipeline Guide - Code Factory Platform

This guide explains the CI/CD pipeline infrastructure for the Code Factory Platform.

## Overview

The Code Factory Platform uses GitHub Actions for continuous integration and deployment, with comprehensive automation for testing, security scanning, and deployment workflows.

## Pipeline Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Developer Workflow                       │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
              ┌────────────────┐
              │  Push to Branch │
              └────────┬───────┘
                       │
        ┌──────────────┼──────────────┐
        │              │              │
        ▼              ▼              ▼
  ┌─────────┐    ┌─────────┐   ┌─────────┐
  │   Lint  │    │  Test   │   │Security │
  │  Checks │    │  Suite  │   │  Scan   │
  └────┬────┘    └────┬────┘   └────┬────┘
       │              │              │
       └──────────────┼──────────────┘
                      │
                      ▼
              ┌───────────────┐
              │ Docker Build  │
              └───────┬───────┘
                      │
                      ▼
              ┌───────────────┐
              │ Merge to Main │
              └───────┬───────┘
                      │
        ┌─────────────┼─────────────┐
        │             │             │
        ▼             ▼             ▼
   ┌────────┐   ┌─────────┐   ┌──────────┐
   │ Build  │   │ Deploy  │   │  Deploy  │
   │ Images │   │ Staging │   │Production│
   └────────┘   └─────────┘   └──────────┘
```

## Workflow Files

### 1. Consolidated CI Workflow (`.github/workflows/ci.yml`)

**Triggers:**
- Push to `main`, `develop`, or `feature/**` branches
- Pull requests to `main` or `develop`
- Manual workflow dispatch

**Architecture:**

The CI workflow now uses intelligent path-based filtering to run only relevant jobs when files change, significantly reducing CI time and resource usage.

**Jobs:**

1. **Changes Detection** - Determines which components have changed
   - Uses `tj-actions/changed-files@v44`
   - Detects changes in: generator/, omnicore_engine/, self_fixing_engineer/, .github/workflows/
   - Outputs: generator, omnicore, sfe, workflows

2. **Lint** - Code quality checks (runs if any component changed)
   - Black formatter check (strict - no error suppression)
   - Ruff linter (strict - no error suppression)
   - Flake8 syntax checks (strict - no error suppression)
   - Only runs when relevant files have changed

3. **Test Generator** - Generator component tests
   - **Conditional**: Only runs if generator/ or .github/workflows/ changed
   - Unit tests with pytest
   - Coverage reporting
   - **Fails if tests directory is missing** (no longer silently passes)

4. **Test OmniCore** - OmniCore Engine tests
   - **Conditional**: Only runs if omnicore_engine/ or .github/workflows/ changed
   - Integration tests with Redis service
   - Coverage reporting
   - **Fails if tests directory is missing** (no longer silently passes)

5. **Test SFE** - Self-Fixing Engineer tests
   - **Conditional**: Only runs if self_fixing_engineer/ or .github/workflows/ changed
   - Component tests
   - Coverage reporting
   - **Fails if tests directory is missing** (no longer silently passes)

6. **Integration Tests** - End-to-end tests
   - **Conditional**: Runs if any component changed
   - Full platform integration
   - All services running
   - Strict error checking (no error suppression)

7. **Build Docker** - Container image builds
   - **Conditional**: Runs if any component changed
   - Multi-component builds
   - Image validation

8. **Status Check** - Final status aggregation
   - Depends on all previous jobs
   - Provides overall CI status

**Usage:**
```bash
# Runs automatically on push/PR
# Jobs run conditionally based on changed files
# Or manually trigger all jobs:
gh workflow run ci.yml
```

**Benefits:**
- **Reduced CI Time**: Jobs skip when their paths haven't changed
- **Resource Efficiency**: Only runs necessary tests and builds
- **Clear Feedback**: Know exactly which components were tested
- **Strict Quality Control**: No error suppression - all issues must be fixed

### 2. Security Scanning (`.github/workflows/security.yml`)

**Triggers:**
- Push to `main` or `develop`
- Pull requests
- Daily at 2 AM UTC (scheduled)
- Manual dispatch

**Jobs:**

1. **Dependency Check**
   - Safety vulnerability scanner
   - pip-audit for Python packages

2. **Secret Scan**
   - TruffleHog for exposed secrets
   - Scans entire git history

3. **CodeQL Analysis**
   - Static code analysis
   - Security vulnerability detection
   - SARIF report generation

4. **Docker Security**
   - Trivy container scanning
   - Critical and high severity focus

5. **SAST Analysis**
   - Bandit security linter
   - Python-specific checks

6. **License Check**
   - pip-licenses compliance
   - License compatibility verification

**Usage:**
```bash
# Runs automatically on schedule and push
# Or manually:
gh workflow run security.yml
```

### 3. Continuous Deployment (`.github/workflows/cd.yml`)

**Triggers:**
- Push to `main` branch
- Version tags (v*.*.*)
- Manual dispatch with environment selection

**Jobs:**

1. **Build and Push** - Container registry publication
   - Builds Docker images
   - Pushes to GHCR (GitHub Container Registry)
   - Multi-component support

2. **Deploy Staging** - Staging environment deployment
   - Automatic on main branch
   - Smoke tests
   - Environment URL: staging.codefactory.example.com

3. **Deploy Production** - Production deployment
   - Triggered by version tags
   - Requires staging success
   - Environment URL: codefactory.example.com

4. **Rollback** - Automated rollback on failure

5. **Notify** - Deployment notifications

**Usage:**
```bash
# Automatic deployment on push to main
# Or manual deployment:
gh workflow run cd.yml -f environment=staging

# Production deployment via tag:
git tag v1.0.0
git push origin v1.0.0
```

### 4. Dependency Updates (`.github/workflows/dependency-updates.yml`)

**Triggers:**
- Weekly on Monday at 3 AM UTC
- Manual dispatch

**Jobs:**

1. **Update Dependencies**
   - Runs pip-compile for all components
   - Creates PR with updates

2. **Check Outdated**
   - Lists outdated packages
   - Generates report artifact

**Usage:**
```bash
# Runs automatically weekly
# Or manually:
gh workflow run dependency-updates.yml
```

## Component-Specific Workflows

**Note**: Component-specific workflows have been consolidated into the main CI workflow (`.github/workflows/ci.yml`) for better efficiency and maintainability.

The main CI workflow now uses path-based filtering to automatically run only relevant jobs:
- Changes to `generator/` trigger Generator tests
- Changes to `omnicore_engine/` trigger OmniCore tests  
- Changes to `self_fixing_engineer/` trigger SFE tests
- Changes to `.github/workflows/` trigger all jobs

This consolidation provides:
- **Single source of truth** for CI configuration
- **Better resource utilization** by running only necessary jobs
- **Easier maintenance** with one workflow file instead of multiple
- **Consistent behavior** across all components

## Required Secrets

Configure these secrets in your GitHub repository settings:

### API Keys
- `GROK_API_KEY` - xAI Grok API key
- `OPENAI_API_KEY` - OpenAI API key
- `GOOGLE_API_KEY` - Google Gemini API key
- `ANTHROPIC_API_KEY` - Anthropic Claude API key

### Container Registry
- `GITHUB_TOKEN` - Automatically provided by GitHub Actions

### Deployment (Optional)
- `KUBE_CONFIG` - Kubernetes configuration (base64 encoded)
- `AWS_ACCESS_KEY_ID` - AWS credentials
- `AWS_SECRET_ACCESS_KEY` - AWS credentials

## Local CI Testing

Run CI checks locally before pushing:

```bash
# Run all CI checks (recommended before every commit)
make ci-local

# Individual checks (all run with strict error checking)
make lint              # Black, Ruff, Flake8 - STRICT (no || true)
make type-check        # mypy - STRICT (no || true)  
make security-scan     # Bandit, Safety - STRICT (no || true)
make test              # All tests - STRICT (no || true)
make docker-build      # Build containers
```

> **Important**: All commands now use strict error checking. Failures will stop execution and must be fixed. This matches CI behavior and prevents surprises in CI runs.

## Workflow Status Badges

Add these badges to your README:

```markdown
![CI](https://github.com/musicmonk42/The_Code_Factory_Working_V2/workflows/CI/badge.svg)
![Security](https://github.com/musicmonk42/The_Code_Factory_Working_V2/workflows/Security/badge.svg)
![CD](https://github.com/musicmonk42/The_Code_Factory_Working_V2/workflows/CD/badge.svg)
```

## Debugging Failed Workflows

### View logs
```bash
gh run list
gh run view <run-id>
gh run view <run-id> --log
```

### Re-run failed jobs
```bash
gh run rerun <run-id>
gh run rerun <run-id> --failed
```

### Download artifacts
```bash
gh run download <run-id>
```

## Best Practices

1. **Always run `make ci-local` before pushing** - Catches issues early with strict checking
2. **Keep secrets in GitHub Secrets, never in code**
3. **Use `.env.example` for configuration templates**
4. **Write tests for all new features** - Missing tests now fail CI
5. **Review security scan results regularly**
6. **Keep dependencies up to date** - Use `master_requirements.txt` for consistency
7. **Use semantic versioning for releases**
8. **Document breaking changes**
9. **Fix all linting errors** - No error suppression means all issues must be resolved
10. **Ensure test directories exist** - Missing test directories now fail CI instead of passing silently

## Monitoring and Alerts

### GitHub Actions Insights
- View workflow run history
- Track success/failure rates
- Monitor execution times

### Integration with External Services
- **Slack** - Configure webhook for notifications
- **Email** - GitHub notifications
- **PagerDuty** - For production alerts

## Performance Optimization

### Path-Based Filtering (New)
The consolidated CI workflow uses intelligent path-based filtering to run only necessary jobs:

```yaml
# Change detection job
changes:
  outputs:
    generator: ${{ steps.changes.outputs.generator }}
    omnicore: ${{ steps.changes.outputs.omnicore }}
    sfe: ${{ steps.changes.outputs.sfe }}

# Conditional job execution
test-generator:
  needs: changes
  if: needs.changes.outputs.generator == 'true'
```

**Benefits:**
- Reduces CI time by 50-70% for component-specific changes
- Saves GitHub Actions minutes
- Faster feedback for developers
- Only runs relevant tests

### Caching
- Python dependencies cached automatically
- Docker layer caching enabled
- Test result caching

### Parallelization
- Jobs run in parallel when possible
- Matrix builds for multiple versions
- Component-specific workflows

### Resource Limits
- Ubuntu runners (2-core, 7GB RAM)
- 6 hours max execution time
- 500MB artifact storage per workflow

## Troubleshooting

### Common Issues

**1. Tests failing locally but passing in CI**
- Check Python version compatibility
- Verify all dependencies installed
- Review environment differences

**2. Docker build failures**
- Check Dockerfile syntax
- Verify base image availability
- Review build logs for errors

**3. Security scan false positives**
- Review vulnerability details
- Update dependencies if needed
- Add exceptions if truly false positive

**4. Deployment failures**
- Verify secrets are configured
- Check target environment health
- Review deployment logs

## Further Reading

- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [QUICKSTART.md](./QUICKSTART.md) - Getting started guide
- [DEPLOYMENT.md](./DEPLOYMENT.md) - Deployment instructions
- [README.md](./README.md) - Main documentation

## Support

For issues with CI/CD pipelines:
1. Check workflow logs
2. Review this guide
3. Contact DevOps team
4. Open an issue on GitHub

---

**Last Updated:** 2025-11-21  
**Version:** 2.0.0 - Consolidated CI with path-based filtering

## Recent Changes (v2.0.0)

### CI Workflow Consolidation
- **Merged** component-specific workflows into single consolidated workflow
- **Removed** redundant `generator/.github/workflows/generator-ci.yml`
- **Removed** redundant `omnicore_engine/.github/workflows/omnicore-ci.yml`
- **Removed** redundant `self_fixing_engineer/.github/workflows/sfe-ci.yml`
- **Added** intelligent path-based filtering using `tj-actions/changed-files`

### Error Suppression Removal
- **Removed** all `|| true` from linting commands (Black, Ruff, Flake8)
- **Removed** all `|| true` from test commands
- **Changed** missing test directories to fail (exit 1) instead of pass (exit 0)
- **Result**: Strict error checking throughout CI and Makefile

### Dependency Fixes
- **Removed** `backports.asyncio.runner==1.2.0` (incompatible with Python 3.11)
- **Fixed** conflicting `importlib-metadata` versions
- **Requires** Python 3.11+ (3.10 and below no longer supported)

### Makefile Improvements
- **Updated** `install-hooks` to append instead of overwrite pre-commit hooks
- **Removed** error suppression from lint, type-check, and security-scan targets
- **Ensures** failures are properly reported
