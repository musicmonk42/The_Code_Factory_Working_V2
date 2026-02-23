<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# CI Troubleshooting Guide

This guide covers the most common CI/CD failures in this repository and how to resolve them.

---

## Table of Contents

- [LLM_API_KEY Must Be Set in Repository Secrets](#llm_api_key-must-be-set-in-repository-secrets)
- [Exit Code 143 (SIGTERM) in GitHub Actions](#exit-code-143-sigterm-in-github-actions)
- [Best Practices for Stable CI](#best-practices-for-stable-ci)

---

## LLM_API_KEY Must Be Set in Repository Secrets

### Why It Is Required

The Code Factory platform validates LLM provider configuration at import time using Pydantic.
If the `LLM_API_KEY` environment variable is absent or empty, Pydantic's config validation
raises an error **during pytest test discovery**, before any test runs. This produces confusing
errors such as:

```
pydantic_core._pydantic_core.ValidationError: 1 validation error for Settings
LLM_API_KEY
  Field required [type=missing, ...]
```

or cascading `ImportError` / `ModuleNotFoundError` messages that obscure the true root cause.

### How to Fix It

Add `LLM_API_KEY` as a repository secret so all CI workflow runs have access to it:

1. Navigate to your repository on GitHub.
2. Go to **Settings → Secrets and variables → Actions**.
3. Click **New repository secret**.
4. Set:
   - **Name**: `LLM_API_KEY`
   - **Value**: A valid API key for any supported LLM provider (xAI Grok, OpenAI, Google Gemini,
     Anthropic Claude, or an Ollama base URL).
5. Click **Add secret**.

The secret is automatically injected into the job environment by the workflow:

```yaml
# .github/workflows/pytest-all.yml — line 86
LLM_API_KEY: "${{ secrets.LLM_API_KEY }}"
```

### Early-Failure Guard

`.github/workflows/pytest-all.yml` includes a **Verify LLM_API_KEY secret is configured** step
(immediately after `Checkout code`) that checks for the variable and exits with a clear error
message before any expensive dependency installation or test collection begins:

```yaml
- name: Verify LLM_API_KEY secret is configured
  run: |
    if [ -z "${LLM_API_KEY}" ]; then
      echo "ERROR: LLM_API_KEY secret is not set or is empty."
      ...
      exit 1
    fi
    echo "✓ LLM_API_KEY is set"
```

This prevents all downstream confusion caused by missing Pydantic config validation.

---

## Exit Code 143 (SIGTERM) in GitHub Actions

### What It Means

Exit code **143** = process terminated by **SIGTERM** (signal 15).  
In the context of GitHub Actions it is **not** a test failure — it means the runner process was
stopped externally before it could finish.

### Common Causes

| Cause | Description |
|-------|-------------|
| **Workflow timeout** | The job exceeded its `timeout-minutes` limit (default: 180 min for `test-matrix`). GitHub Actions sends SIGTERM, waits 30 s, then SIGKILL. |
| **Concurrency cancellation** | A newer push to the same branch triggered the [`concurrency`](../.github/workflows/pytest-all.yml) group. Non-main branches have `cancel-in-progress: true`, so the in-progress run is cancelled with SIGTERM. This is **expected and intentional**. |
| **Runner eviction / pre-emption** | The hosted runner was reclaimed by GitHub infrastructure (rare). |
| **Manual cancellation** | A maintainer clicked **Cancel** in the GitHub Actions UI. |

### How to Diagnose

1. Open the failed workflow run in GitHub Actions.
2. Expand the failing step — look for `exit code 143` in the step summary.
3. The workflow already prints diagnostic context on exit code 143:

   ```
   ERROR: Process terminated (SIGTERM - exit code 143)
   This typically indicates:
     - Timeout or resource exhaustion
     - Runner service shutdown
     - Memory pressure from test suite
   ```

4. Check whether a **newer run for the same branch** completed successfully — if so, this
   cancellation was caused by `cancel-in-progress` and can be safely ignored.

### What Maintainers Should Do

| Scenario | Action |
|----------|--------|
| Cancellation due to `cancel-in-progress` | No action needed — the newer run is the authoritative result. |
| Timeout (`timeout-minutes` exceeded) | Investigate slow tests. Use `--durations=20` output from the test log to identify hotspots. Consider increasing `timeout-minutes` or splitting the matrix. |
| Runner eviction | Re-run the failed jobs from the GitHub Actions UI (**Re-run failed jobs**). |
| Persistent exit code 143 without a newer run | Increase `timeout-minutes`, reduce test parallelism (`-n 1`), or add swap (already done for `self_fixing_engineer` module). |

### How to Avoid It

The workflow already implements several best practices to minimise spurious exit code 143
occurrences:

```yaml
# .github/workflows/pytest-all.yml
timeout-minutes: 180          # generous job-level timeout

concurrency:
  group: pytest-all-${{ github.ref }}
  cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}
  # ^ only cancels non-main branches; main branch runs are never cancelled

# Each test batch uses a wall-clock guard:
timeout --signal=TERM --kill-after=30s 60m python -m pytest ...
```

Additional recommendations:

- **Keep PRs small**: smaller diffs mean shorter test runs, fewer timeout risks.
- **Mark slow tests**: use `@pytest.mark.slow` and ensure CI runs `pytest -m "not heavy"`.
- **Monitor memory**: the workflow logs memory before/after each test batch. OOM pressure can
  trigger SIGTERM indirectly via the kernel OOM killer.
- **Use `make ci-local` before pushing**: catches most failures locally before they consume
  CI minutes.

---

## Best Practices for Stable CI

1. **Always set `LLM_API_KEY` in repository secrets** before running CI for the first time.
   See [LLM_API_KEY Must Be Set in Repository Secrets](#llm_api_key-must-be-set-in-repository-secrets).

2. **Never commit secrets** to source code. Use `.env` files locally (they are `.gitignore`d)
   and GitHub Actions secrets for CI.

3. **Run `make ci-local` before pushing** to catch linting, type-check, and test failures early:
   ```bash
   make ci-local
   ```

4. **Re-run flaky jobs** instead of closing and reopening a PR. GitHub Actions supports
   **Re-run failed jobs** which retries only the jobs that failed.

5. **Check the exit code** in the step log before assuming a test is broken:
   - `0` — success
   - `1` — test failures (actual failures to fix)
   - `4` — pytest internal error (usually import/collection error — check `LLM_API_KEY`)
   - `124` — wall-clock timeout (increase `timeout-minutes` or reduce test scope)
   - `137` — OOM kill (reduce parallelism or add swap)
   - `143` — SIGTERM (see [Exit Code 143](#exit-code-143-sigterm-in-github-actions))
   - `152` — SIGXCPU CPU time limit (set `ulimit -t unlimited` — already done in the workflow)

6. **Reference the workflow file** for authoritative configuration:
   - Relevant file: `.github/workflows/pytest-all.yml`
   - `LLM_API_KEY` env injection: line 86
   - Concurrency / cancel-in-progress: lines 30–35
   - Per-job timeout: line 42 (`timeout-minutes: 180`)
   - Exit code handlers: lines 736–780 (non-SFE tests), lines 934–965 (Arbiter tests)

---

## Additional Resources

- [TROUBLESHOOTING.md](./TROUBLESHOOTING.md) — General platform troubleshooting
- [CI_CD_GUIDE.md](./CI_CD_GUIDE.md) — CI/CD pipeline documentation
- [DEPENDENCY_GUIDE.md](./DEPENDENCY_GUIDE.md) — Dependency management details
- [DEPLOYMENT.md](./DEPLOYMENT.md) — Production deployment guide
