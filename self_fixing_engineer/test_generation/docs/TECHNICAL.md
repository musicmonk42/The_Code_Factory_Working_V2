# Technical Reference

## Table of Contents
- [Quick Start](#quick-start)
- [API Reference](#api-reference)
- [Integration Patterns](#integration-patterns)
- [Configuration](#configuration)
- [Performance Targets](#performance-targets)
- [Last updated](#last-updated)
- [Owner/Contact](#ownercontact)

---

## Quick Start

- **API Endpoint:** `https://platform.company.com/api/v1/test-generation`
- **Auth:** Bearer token from `/auth/token`
- **Rate limit:** 100 req/min

---

## API Reference

### Generate Tests

**POST** `/api/v1/test-generation/generate`

```json
{
  "language": "python",
  "module": "src/analyzer.py",
  "framework": "pytest"
}
```

**Response:**  
Returns a job object with status and a link to test artifacts.

---

## Integration Patterns

### Direct API

```python
from test_gen_client import TestGenClient
import os

client = TestGenClient(token=os.environ["API_TOKEN"])
job = await client.generate_tests("src/module.py")
```

### Event Bus

- **Subscribe to:** `test.generated`, `test.quarantined`
- **Publish to:** `coverage.updated`

---

## Configuration

### Required Environment

- `AWS_REGION`: us-east-1
- `REDIS_HOST`: redis.internal
- `OPENAI_API_KEY`: (from Secrets Manager)

### Quality Thresholds

```yaml
python:
  min_coverage: 80
  min_mutation_score: 60
  max_complexity: 10
```

---

## Performance Targets

| Operation         | P50   | P95   | SLA    |
|-------------------|-------|-------|--------|
| Test generation   | 2s    | 10s   | <30s   |
| Quality scoring   | 200ms | 500ms | <1s    |

---

## Last updated

2025-09-01

---

## Owner/Contact

- **Owner:** Platform Team
- **Contact:** platform-team@enterprise.com

---