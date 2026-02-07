<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

\# RUNBOOK.md



\## Common Issues \& Resolution



\### Issue: Test Generation Timeout

\*\*Symptoms\*\*: Jobs stuck in PENDING state >5 minutes

\*\*Resolution\*\*:

1\. Check backend service health: `kubectl get pods -l app=test-backend`

2\. Review resource consumption: `kubectl top pods`

3\. Scale if needed: `kubectl scale deployment test-backend --replicas=5`



\### Issue: High Quarantine Rate

\*\*Alert\*\*: quarantine\_rate > 30%

\*\*Investigation\*\*:

1\. Check recent policy changes

2\. Review language-specific failure patterns

3\. Validate backend configurations

