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

### 1. Main CI Workflow (`.github/workflows/ci.yml`)

**Triggers:**
- Push to `main`, `develop`, or `feature/**` branches
- Pull requests to `main` or `develop`
- Manual workflow dispatch

**Jobs:**

1. **Lint** - Code quality checks
   - Black formatter
   - Ruff linter
   - Flake8 syntax checks

2. **Test Generator** - Generator component tests
   - Unit tests with pytest
   - Coverage reporting

3. **Test OmniCore** - OmniCore Engine tests
   - Integration tests with Redis
   - Coverage reporting

4. **Test SFE** - Self-Fixing Engineer tests
   - Component tests
   - Coverage reporting

5. **Integration Tests** - End-to-end tests
   - Full platform integration
   - All services running

6. **Build Docker** - Container image builds
   - Multi-component builds
   - Image validation

7. **Status Check** - Final status aggregation

**Usage:**
```bash
# Runs automatically on push/PR
# Or manually trigger:
gh workflow run ci.yml
```

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

### Generator CI (`generator/.github/workflows/generator-ci.yml`)

Runs tests and builds for changes to the Generator component only.

### OmniCore CI (`omnicore_engine/.github/workflows/omnicore-ci.yml`)

Runs tests and builds for changes to the OmniCore Engine only.

### SFE CI (`self_fixing_engineer/.github/workflows/sfe-ci.yml`)

Runs tests and builds for changes to the Self-Fixing Engineer only.

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
# Run all CI checks
make ci-local

# Individual checks
make lint
make type-check
make security-scan
make test
make docker-build
```

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

1. **Always run `make ci-local` before pushing**
2. **Keep secrets in GitHub Secrets, never in code**
3. **Use `.env.example` for configuration templates**
4. **Write tests for all new features**
5. **Review security scan results regularly**
6. **Keep dependencies up to date**
7. **Use semantic versioning for releases**
8. **Document breaking changes**

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
**Version:** 1.0.0
