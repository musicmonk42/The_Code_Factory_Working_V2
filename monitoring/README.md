# Monitoring Configuration - Code Factory Platform

This directory contains the monitoring and alerting configuration for the Code Factory Platform.

## Directory Structure

```
monitoring/
├── README.md                 # This file
├── prometheus.yml            # Prometheus scrape configuration
├── alertmanager.yml          # Alertmanager routing and receivers
├── alerts.yml                # Prometheus alerting rules
└── grafana/
    ├── dashboards/
    │   ├── dashboard.yml     # Dashboard provisioning config
    │   └── business-metrics.json  # Business metrics dashboard
    └── datasources/
        └── prometheus.yml    # Prometheus datasource config
```

## Services Overview

The monitoring stack integrates with the following services defined in `docker-compose.yml`:

| Service | Port | Description |
|---------|------|-------------|
| codefactory | 8000 (API), 8001 (metrics) | Unified platform (generator + omnicore + SFE) |
| redis | 6379 | Message bus and caching |
| prometheus | 9090 | Metrics collection and alerting |
| grafana | 3000 | Visualization and dashboards |

## Quick Start

### Starting the Monitoring Stack

```bash
# Start all services including monitoring
docker-compose up -d

# Or start only specific services
docker-compose up -d prometheus grafana
```

### Accessing Dashboards

- **Grafana**: http://localhost:3000 (default: admin/admin)
- **Prometheus**: http://localhost:9090

## Prometheus Configuration

The `prometheus.yml` file defines scrape targets:

- `codefactory:8001` - Main application metrics
- `redis-exporter:9121` - Redis metrics (requires separate exporter)

### Adding Metrics Exporters

For Redis and PostgreSQL metrics, deploy the official exporters:

```yaml
# Add to docker-compose.yml for Redis metrics
redis-exporter:
  image: oliver006/redis_exporter:latest
  container_name: codefactory-redis-exporter
  environment:
    REDIS_ADDR: redis://redis:6379
  ports:
    - "9121:9121"
  depends_on:
    - redis
```

## Alertmanager Configuration

The `alertmanager.yml` defines notification routing:

- **Critical alerts**: PagerDuty + Slack + Email
- **Warning alerts**: Slack + Email
- **Info alerts**: Slack only
- **Security alerts**: Security team channel

### Required Environment Variables

Configure these variables for alert delivery:

```bash
# Email/SMTP
SMTP_SERVER=smtp.example.com
SMTP_PORT=587
SMTP_FROM_EMAIL=alerts@codefactory.example.com
SMTP_USERNAME=your-smtp-user
SMTP_PASSWORD=your-smtp-password
ALERT_EMAIL=team@example.com

# Slack
ALERT_SLACK_WEBHOOK=https://hooks.slack.com/services/xxx
SECURITY_SLACK_WEBHOOK=https://hooks.slack.com/services/xxx

# PagerDuty
PAGERDUTY_SERVICE_KEY=your-pagerduty-key
```

## Alert Rules

The `alerts.yml` file contains alerting rules organized by category:

1. **Application Health**: ServiceDown, HighErrorRate, HighLatency
2. **Resource Utilization**: HighCPUUsage, HighMemoryUsage, DiskSpaceLow
3. **Database Health**: DatabaseDown, HighDatabaseConnections, SlowQueries
4. **Redis Health**: RedisDown, RedisHighMemoryUsage
5. **Security**: HighFailedLoginAttempts, UnauthorizedAccessAttempt
6. **LLM Services**: LLMAPIFailureRate, LLMAPIRateLimitApproaching
7. **Self-Fixing Engineer**: SFECheckpointFailure, SFEAnalysisBacklog

## Grafana Dashboards

Pre-configured dashboards are automatically provisioned from `grafana/dashboards/`.

### Adding Custom Dashboards

1. Create or export a dashboard JSON file
2. Place it in `monitoring/grafana/dashboards/`
3. The dashboard will be automatically loaded on Grafana startup

## Troubleshooting

### Prometheus Not Scraping Targets

```bash
# Check target status in Prometheus UI
# Go to http://localhost:9090/targets

# Verify service connectivity
docker exec codefactory-prometheus wget -q -O- http://codefactory:8001/metrics
```

### Grafana Not Showing Data

1. Verify Prometheus datasource is configured correctly
2. Check that the time range includes data
3. Ensure Prometheus is scraping the targets

### Alerts Not Firing

1. Check alert rules in Prometheus: http://localhost:9090/alerts
2. Verify Alertmanager is receiving alerts: http://localhost:9093
3. Check Alertmanager routing configuration

## Integration with CI/CD

The monitoring stack is not included in CI tests by default. For production deployments:

1. Deploy the monitoring stack alongside the main application
2. Configure external Alertmanager receivers
3. Set up Grafana with persistent storage

See [CI_CD_GUIDE.md](../CI_CD_GUIDE.md) for deployment workflow details.
