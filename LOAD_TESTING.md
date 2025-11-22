# Load Testing Guide

This guide provides comprehensive load testing procedures for the Code Factory platform.

## Table of Contents

- [Overview](#overview)
- [Load Testing Tools](#load-testing-tools)
- [Test Scenarios](#test-scenarios)
- [Running Load Tests](#running-load-tests)
- [Performance Targets](#performance-targets)
- [Analyzing Results](#analyzing-results)
- [Troubleshooting](#troubleshooting)

## Overview

### Objectives

1. Validate system can handle expected production load
2. Identify performance bottlenecks
3. Establish baseline performance metrics
4. Verify auto-scaling behavior
5. Test system resilience under stress

### Load Testing Strategy

- **Baseline Test:** Normal expected load
- **Stress Test:** Beyond normal capacity
- **Spike Test:** Sudden traffic increases
- **Soak Test:** Extended duration at normal load
- **Chaos Test:** Failures during load

## Load Testing Tools

### Locust (Recommended)

**Installation:**
```bash
pip install locust
```

**Advantages:**
- Python-based, easy to extend
- Web UI for monitoring
- Distributed load generation
- Good for API testing

### k6 (Alternative)

**Installation:**
```bash
# macOS
brew install k6

# Docker
docker pull grafana/k6
```

**Advantages:**
- JavaScript-based
- Built-in metrics and thresholds
- Cloud execution support
- Grafana integration

### Artillery (Alternative)

**Installation:**
```bash
npm install -g artillery
```

**Advantages:**
- YAML configuration
- Scenario-based testing
- WebSocket support
- Good for complex workflows

## Test Scenarios

### Scenario 1: API Endpoint Load Test

**File: `load_tests/locustfile_api.py`**

```python
from locust import HttpUser, task, between
import random
import json

class CodeFactoryUser(HttpUser):
    wait_time = between(1, 3)
    
    def on_start(self):
        """Login and get auth token"""
        response = self.client.post("/auth/login", json={
            "username": "test_user",
            "password": "test_password"
        })
        if response.status_code == 200:
            self.token = response.json()["token"]
        else:
            self.token = None
    
    @task(3)
    def health_check(self):
        """Health check endpoint"""
        self.client.get("/health", name="/health")
    
    @task(10)
    def list_generations(self):
        """List code generations"""
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        self.client.get("/api/v1/generations", headers=headers, name="/api/v1/generations")
    
    @task(5)
    def create_generation(self):
        """Create new code generation"""
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        payload = {
            "requirements": f"Create a Flask app with endpoint /test_{random.randint(1, 1000)}",
            "language": "python",
            "framework": "flask"
        }
        with self.client.post(
            "/api/v1/generate",
            json=payload,
            headers=headers,
            catch_response=True,
            name="/api/v1/generate"
        ) as response:
            if response.status_code == 202:
                response.success()
            elif response.status_code == 429:
                response.failure("Rate limited")
            else:
                response.failure(f"Failed with status {response.status_code}")
    
    @task(2)
    def get_generation_status(self):
        """Check generation status"""
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        gen_id = random.randint(1, 100)
        self.client.get(
            f"/api/v1/generations/{gen_id}",
            headers=headers,
            name="/api/v1/generations/[id]"
        )
    
    @task(1)
    def metrics(self):
        """Scrape metrics"""
        self.client.get("/metrics", name="/metrics")
```

### Scenario 2: Code Generation Workflow

**File: `load_tests/locustfile_workflow.py`**

```python
from locust import HttpUser, task, between, SequentialTaskSet
import time

class CodeGenerationWorkflow(SequentialTaskSet):
    """Simulate complete code generation workflow"""
    
    @task
    def step1_submit_request(self):
        """Submit code generation request"""
        response = self.client.post("/api/v1/generate", json={
            "requirements": "Create a REST API for todo management",
            "language": "python",
            "framework": "fastapi"
        })
        if response.status_code == 202:
            self.generation_id = response.json()["id"]
        else:
            self.interrupt()
    
    @task
    def step2_poll_status(self):
        """Poll until generation is complete"""
        max_attempts = 30
        for _ in range(max_attempts):
            response = self.client.get(f"/api/v1/generations/{self.generation_id}")
            if response.status_code == 200:
                data = response.json()
                if data["status"] == "completed":
                    break
                elif data["status"] == "failed":
                    self.interrupt()
            time.sleep(2)
    
    @task
    def step3_download_code(self):
        """Download generated code"""
        self.client.get(f"/api/v1/generations/{self.generation_id}/download")
    
    @task
    def step4_get_metrics(self):
        """Get generation metrics"""
        self.client.get(f"/api/v1/generations/{self.generation_id}/metrics")

class WorkflowUser(HttpUser):
    wait_time = between(5, 15)
    tasks = [CodeGenerationWorkflow]
```

### Scenario 3: k6 Script

**File: `load_tests/k6_test.js`**

```javascript
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');

// Test configuration
export const options = {
  stages: [
    { duration: '2m', target: 50 },   // Ramp up to 50 users
    { duration: '5m', target: 50 },   // Stay at 50 users
    { duration: '2m', target: 100 },  // Ramp up to 100 users
    { duration: '5m', target: 100 },  // Stay at 100 users
    { duration: '2m', target: 0 },    // Ramp down
  ],
  thresholds: {
    'http_req_duration': ['p(95)<500', 'p(99)<1000'],  // 95% < 500ms, 99% < 1s
    'http_req_failed': ['rate<0.05'],  // Error rate < 5%
    'errors': ['rate<0.1'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

export function setup() {
  // Login and get token
  const loginRes = http.post(`${BASE_URL}/auth/login`, JSON.stringify({
    username: 'test_user',
    password: 'test_password',
  }), {
    headers: { 'Content-Type': 'application/json' },
  });
  
  return { token: loginRes.json('token') };
}

export default function(data) {
  const headers = {
    'Authorization': `Bearer ${data.token}`,
    'Content-Type': 'application/json',
  };
  
  // Test 1: Health check
  let res = http.get(`${BASE_URL}/health`);
  check(res, { 'health check OK': (r) => r.status === 200 });
  errorRate.add(res.status !== 200);
  
  sleep(1);
  
  // Test 2: List generations
  res = http.get(`${BASE_URL}/api/v1/generations`, { headers });
  check(res, { 'list generations OK': (r) => r.status === 200 });
  errorRate.add(res.status !== 200);
  
  sleep(1);
  
  // Test 3: Create generation
  res = http.post(`${BASE_URL}/api/v1/generate`, JSON.stringify({
    requirements: 'Create a simple Flask app',
    language: 'python',
    framework: 'flask',
  }), { headers });
  
  check(res, {
    'create generation accepted': (r) => r.status === 202,
    'response has ID': (r) => r.json('id') !== undefined,
  });
  errorRate.add(res.status !== 202);
  
  sleep(2);
}
```

### Scenario 4: Artillery Configuration

**File: `load_tests/artillery.yml`**

```yaml
config:
  target: "http://localhost:8000"
  phases:
    - duration: 60
      arrivalRate: 10
      name: "Warm up"
    - duration: 300
      arrivalRate: 50
      name: "Sustained load"
    - duration: 60
      arrivalRate: 100
      name: "Spike"
  processor: "./load_tests/helpers.js"
  
scenarios:
  - name: "API Endpoints"
    weight: 60
    flow:
      - get:
          url: "/health"
          capture:
            - json: "$.status"
              as: "health_status"
      - think: 2
      - post:
          url: "/api/v1/generate"
          json:
            requirements: "Create a REST API"
            language: "python"
          capture:
            - json: "$.id"
              as: "generation_id"
      - think: 3
      - get:
          url: "/api/v1/generations/{{ generation_id }}"
          
  - name: "Metrics Scraping"
    weight: 20
    flow:
      - get:
          url: "/metrics"
          
  - name: "Static Assets"
    weight: 20
    flow:
      - get:
          url: "/docs"
```

## Running Load Tests

### Locust

**Basic run:**
```bash
cd load_tests
locust -f locustfile_api.py --host=http://localhost:8000
```

Access web UI at http://localhost:8089

**Headless mode:**
```bash
locust -f locustfile_api.py \
  --host=http://localhost:8000 \
  --users 100 \
  --spawn-rate 10 \
  --run-time 10m \
  --headless
```

**Distributed mode:**
```bash
# Start master
locust -f locustfile_api.py --master --expect-workers=4

# Start workers (on separate machines)
locust -f locustfile_api.py --worker --master-host=<master-ip>
```

### k6

**Basic run:**
```bash
k6 run load_tests/k6_test.js
```

**With environment variables:**
```bash
k6 run --env BASE_URL=https://api.example.com load_tests/k6_test.js
```

**Output to InfluxDB/Grafana:**
```bash
k6 run --out influxdb=http://localhost:8086/k6 load_tests/k6_test.js
```

### Artillery

**Basic run:**
```bash
artillery run load_tests/artillery.yml
```

**Generate report:**
```bash
artillery run --output report.json load_tests/artillery.yml
artillery report report.json
```

## Performance Targets

### Response Time Targets

| Endpoint | p50 | p95 | p99 | Max |
|----------|-----|-----|-----|-----|
| /health | <50ms | <100ms | <200ms | <500ms |
| /api/v1/generations (list) | <100ms | <300ms | <500ms | <1s |
| /api/v1/generate | <200ms | <500ms | <1s | <2s |
| /metrics | <100ms | <200ms | <300ms | <500ms |

### Throughput Targets

- **Normal Load:** 100 req/s
- **Peak Load:** 500 req/s
- **Sustained Load:** 200 req/s for 1 hour

### Resource Utilization

- **CPU:** < 70% average, < 85% peak
- **Memory:** < 80% average, < 90% peak
- **Disk I/O:** < 60% utilization
- **Network:** < 50% bandwidth utilization

### Error Rates

- **4xx errors:** < 1% (excluding 429 rate limits)
- **5xx errors:** < 0.1%
- **Timeout errors:** < 0.5%

## Analyzing Results

### Locust Report

Locust generates HTML reports with:
- Request statistics (RPS, response times)
- Distribution charts
- Failure analysis

**Generate HTML report:**
```bash
locust -f locustfile_api.py --html=report.html --headless --run-time 10m
```

### k6 Summary

k6 provides detailed summary with:
- HTTP metrics
- Check pass rates
- Custom metrics
- Threshold violations

**Example output:**
```
http_req_duration..............: avg=234ms min=45ms med=198ms max=1.2s p(90)=389ms p(95)=489ms
http_req_failed................: 0.45% ✓ 45 ✗ 9955
http_reqs......................: 10000 166.67/s
iteration_duration.............: avg=1.2s min=1.1s med=1.2s max=1.5s
iterations.....................: 10000 166.67/s
vus............................: 100 min=0 max=100
```

### Key Metrics to Monitor

1. **Response Time Percentiles**
   - p50 (median) - typical user experience
   - p95 - most users' experience
   - p99 - worst case for most users
   - max - absolute worst case

2. **Throughput**
   - Requests per second
   - Data transferred per second

3. **Error Rate**
   - HTTP status codes
   - Connection errors
   - Timeouts

4. **System Resources**
   - CPU usage
   - Memory usage
   - Disk I/O
   - Network bandwidth

### Grafana Dashboards

Import load testing dashboard:
```bash
# Import k6 dashboard
curl -X POST http://admin:admin@localhost:3000/api/dashboards/db \
  -H "Content-Type: application/json" \
  -d @load_tests/grafana-dashboard.json
```

## Test Scenarios by Priority

### 1. Critical Path Testing (Must Pass)

**Scenario:** Normal expected load
- **Users:** 50 concurrent
- **Duration:** 30 minutes
- **RPS:** 100
- **Pass Criteria:**
  - p95 < 500ms
  - Error rate < 1%
  - No memory leaks

### 2. Stress Testing

**Scenario:** Beyond capacity
- **Users:** 200 concurrent
- **Duration:** 15 minutes
- **RPS:** 500+
- **Goals:**
  - Find breaking point
  - Verify graceful degradation
  - Test auto-scaling

### 3. Spike Testing

**Scenario:** Sudden traffic spike
- **Pattern:** 10 → 100 → 10 users in 5 minutes
- **Goals:**
  - Verify auto-scaling response
  - Check for service disruption
  - Monitor recovery time

### 4. Soak Testing (Endurance)

**Scenario:** Extended normal load
- **Users:** 50 concurrent
- **Duration:** 4 hours
- **Goals:**
  - Detect memory leaks
  - Check database connection pools
  - Monitor disk space growth

### 5. Chaos Testing

**Scenario:** Load + random failures
- **Load:** 50 concurrent users
- **Duration:** 30 minutes
- **Chaos:**
  - Kill random pods
  - Network latency injection
  - Database slowdowns
- **Goals:**
  - Verify resilience
  - Test circuit breakers
  - Check alerting

## Troubleshooting

### High Response Times

**Possible causes:**
1. Database slow queries
2. N+1 query problem
3. External API latency
4. Insufficient resources

**Investigation:**
```bash
# Check database slow queries
docker exec postgres psql -U codefactory -c "SELECT * FROM pg_stat_statements ORDER BY mean_time DESC LIMIT 10"

# Check pod resources
kubectl top pods

# Check application metrics
curl http://localhost:8001/metrics | grep http_request_duration
```

### High Error Rates

**Possible causes:**
1. Rate limiting triggered
2. Database connection exhaustion
3. OOM kills
4. Application bugs

**Investigation:**
```bash
# Check logs
kubectl logs -l app=code-factory --tail=100

# Check pod events
kubectl describe pod <pod-name>

# Check database connections
docker exec postgres psql -U postgres -c "SELECT count(*) FROM pg_stat_activity"
```

### Memory Leaks

**Signs:**
- Memory usage growing over time
- OOMKilled pods
- Swap usage increasing

**Investigation:**
```bash
# Python memory profiling
pip install memory-profiler
python -m memory_profiler your_script.py

# Check for reference cycles
import gc
print(gc.garbage)
```

### Database Bottlenecks

**Investigation:**
```sql
-- Check active queries
SELECT pid, query, state, wait_event_type, wait_event
FROM pg_stat_activity
WHERE state != 'idle'
ORDER BY query_start;

-- Check table sizes
SELECT schemaname, tablename, pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename))
FROM pg_tables
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC
LIMIT 10;

-- Check missing indexes
SELECT schemaname, tablename, attname, n_distinct, correlation
FROM pg_stats
WHERE schemaname = 'public'
ORDER BY n_distinct DESC;
```

## Best Practices

1. **Start Small:** Begin with low load and gradually increase
2. **Test Production-Like:** Use production data volumes and configurations
3. **Monitor Everything:** CPU, memory, disk, network, database
4. **Automate:** Run load tests in CI/CD pipeline
5. **Test Regularly:** Monthly load tests to catch regressions
6. **Document Results:** Keep historical performance data
7. **Fix Issues:** Don't ignore performance problems
8. **Retest:** Verify fixes with load tests

## CI/CD Integration

**GitHub Actions workflow:**
```yaml
# .github/workflows/load-test.yml
name: Load Testing

on:
  schedule:
    - cron: '0 2 * * 0'  # Weekly on Sunday
  workflow_dispatch:

jobs:
  load-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          
      - name: Install dependencies
        run: pip install locust
        
      - name: Run load test
        run: |
          locust -f load_tests/locustfile_api.py \
            --host=${{ secrets.STAGING_URL }} \
            --users 50 \
            --spawn-rate 5 \
            --run-time 10m \
            --headless \
            --html=report.html
            
      - name: Upload report
        uses: actions/upload-artifact@v4
        with:
          name: load-test-report
          path: report.html
          
      - name: Check thresholds
        run: |
          python load_tests/check_thresholds.py report.json
```

---

**Document Version:** 1.0.0  
**Last Updated:** 2025-11-22  
**Owner:** Performance Engineering Team
