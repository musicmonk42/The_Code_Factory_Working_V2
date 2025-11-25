# Code Factory Platform - Quick Start Guide

Get up and running with the Code Factory Platform in minutes!

## Table of Contents

- [Prerequisites](#prerequisites)
- [Quick Installation](#quick-installation)
- [Running with Docker](#running-with-docker)
- [Running Locally](#running-locally)
- [First Steps](#first-steps)
- [Common Commands](#common-commands)
- [Troubleshooting](#troubleshooting)
- [Next Steps](#next-steps)

## Prerequisites

### Required

- **Python 3.11+** - [Download](https://www.python.org/downloads/) (Python 3.10 and below are not supported)
- **Git** - [Download](https://git-scm.com/downloads)
- **Docker & Docker Compose** (for containerized setup) - [Download](https://www.docker.com/get-started)

> **Important**: Python 3.11 or higher is required. Earlier versions are not supported due to dependency requirements.

### Optional

- **Make** - For simplified commands (included on Linux/macOS, [Windows instructions](http://gnuwin32.sourceforge.net/packages/make.htm))
- **Redis** - For message bus (or use Docker)
- **PostgreSQL** - For production database (or use Docker)

### API Keys (at least one required)

Get API keys from one or more of these providers:

- **xAI Grok** - [Get API Key](https://console.x.ai/)
- **OpenAI** - [Get API Key](https://platform.openai.com/api-keys)
- **Google Gemini** - [Get API Key](https://makersuite.google.com/app/apikey)
- **Anthropic Claude** - [Get API Key](https://console.anthropic.com/)
- **Local LLM** - Run Ollama locally (no API key needed)

## Quick Installation

### Option 1: Using Make (Recommended)

```bash
# Clone the repository
git clone https://github.com/musicmonk42/The_Code_Factory_Working_V2.git
cd The_Code_Factory_Working_V2

# Run the setup command
make setup

# Edit .env file with your API keys
nano .env  # or use your favorite editor

# Start services
make docker-up
```

### Option 2: Manual Setup

```bash
# Clone the repository
git clone https://github.com/musicmonk42/The_Code_Factory_Working_V2.git
cd The_Code_Factory_Working_V2

# Copy environment template
cp .env.example .env

# Edit .env file with your API keys
nano .env

# Install dependencies for the unified platform
pip install --upgrade pip
pip install -r requirements.txt

# Start with Docker
docker-compose up -d
```

## Running with Docker

Docker is the easiest way to get started:

```bash
# Start all services
make docker-up
# or
docker-compose up -d

# Check service status
docker-compose ps

# View logs
make docker-logs
# or
docker-compose logs -f

# Stop all services
make docker-down
# or
docker-compose down
```

### Access the Services

Once started, you can access:

- **Generator API**: http://localhost:8000
- **Generator Docs**: http://localhost:8000/docs
- **OmniCore API**: http://localhost:8001
- **OmniCore Docs**: http://localhost:8001/docs
- **Prometheus**: http://localhost:9090
- **Grafana**: http://localhost:3000 (admin/admin)

## Running Locally

For development without Docker:

### 1. Start Redis (required)

```bash
# Using Docker
docker run -d -p 6379:6379 redis:7-alpine

# Or install Redis locally
# macOS: brew install redis && brew services start redis
# Ubuntu: sudo apt-get install redis-server && sudo service redis-server start
```

### 2. Start Generator

```bash
# Terminal 1
make run-generator
# or from the generator directory:
cd generator
python -m main.main --interface api

# For Git Bash on Windows, use:
cd generator
python -m main.main --interface api
```

#### Running Multiple Interfaces (Git Bash)

To run both main.py and the API simultaneously in Git Bash:

```bash
# Terminal 1 - Start the API server
cd generator
python -m main.main --interface api

# Terminal 2 - Start with all interfaces (CLI, GUI, API)
cd generator
python -m main.main --interface all

# Or run specific interfaces:
python -m main.main --interface cli   # CLI only
python -m main.main --interface gui   # GUI only
```

### 3. Start OmniCore Engine

```bash
# Terminal 2
make run-omnicore
# or
cd omnicore_engine
python -m uvicorn fastapi_app:app --host 0.0.0.0 --port 8001 --reload
```

## First Steps

### 1. Verify Installation

```bash
# Check health
make health-check
# or
python health_check.py

# Run tests
make test
```

### 2. Generate Your First Application

Using the Generator API:

```bash
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{
    "requirements": "Create a Flask REST API with /hello endpoint",
    "output_format": "docker"
  }'
```

Using the OmniCore CLI:

```bash
cd omnicore_engine
python -m omnicore_engine.cli --code-factory-workflow --input-file ../input_readme.md
```

### 3. Try the Demo

```bash
cd generator
python demo_investor.py
```

This will generate a sample Dockerfile based on a demo description.

## Common Commands

### Development

```bash
make help              # Show all available commands
make install-dev       # Install with dev dependencies
make test              # Run all tests
make lint              # Run code linters
make format            # Format code
make clean             # Clean up generated files
```

### Docker

```bash
make docker-build      # Build Docker images
make docker-up         # Start services
make docker-down       # Stop services
make docker-logs       # View logs
make docker-clean      # Clean up Docker resources
```

### Testing

```bash
make test              # Run all tests
make test-generator    # Test Generator only
make test-omnicore     # Test OmniCore only
make test-sfe          # Test Self-Fixing Engineer only
make test-coverage     # Run with coverage report
```

### Code Quality

```bash
make lint              # Run linters (strict - will fail on errors)
make format            # Format code with Black
make type-check        # Run type checking (strict)
make security-scan     # Run security scans (strict)
make ci-local          # Run all CI checks locally (strict)
```

> **Note**: All code quality checks now enforce strict checking. Errors will cause command failures instead of being suppressed, ensuring code quality standards are met.

## Troubleshooting

### Common Issues

#### 1. Port Already in Use

```bash
# Find process using port 8000
lsof -i :8000  # Linux/macOS
netstat -ano | findstr :8000  # Windows

# Kill the process or change the port in docker-compose.yml
```

#### 2. Import Errors

```bash
# Reinstall dependencies
make clean
make install-dev
```

**Common Import Error Fixes:**

- **"No module named 'runner.alerting'"**: This module re-exports `send_alert` from `runner_logging`. Make sure you're running from the `generator` directory.

- **"attempted relative import with no known parent package"**: Use module syntax instead of direct execution:
  ```bash
  # Wrong:
  python main/main.py
  
  # Correct:
  cd generator
  python -m main.main --interface api
  ```

- **Missing dependencies**: Install all required packages:
  ```bash
  pip install -r requirements.txt
  # Or install specific packages:
  pip install python-dotenv pydantic prometheus_client aiohttp aiofiles opentelemetry-api opentelemetry-sdk
  ```

#### 3. Redis Connection Error

```bash
# Check if Redis is running
docker ps | grep redis

# Start Redis if not running
docker run -d -p 6379:6379 redis:7-alpine
```

#### 4. API Key Not Working

```bash
# Verify .env file
cat .env | grep API_KEY

# Make sure .env is in the root directory
# Restart services after updating .env
make docker-down
make docker-up
```

#### 5. Docker Build Fails

```bash
# Clean Docker cache
make docker-clean

# Rebuild from scratch
docker-compose build --no-cache
```

### Getting Help

- **Documentation**: See [README.md](./README.md) and [docs/](./omnicore_engine/docs/)
- **Health Check**: Run `python health_check.py`
- **Logs**: Check service logs with `make docker-logs`
- **Issues**: Report issues on GitHub

## Next Steps

### Learn More

1. **Read the Documentation**
   - [README.md](./README.md) - Complete overview
   - [DEPLOYMENT.md](./DEPLOYMENT.md) - Deployment guide
   - [Generator README](./generator/README.md) - Generator details
   - [OmniCore README](./omnicore_engine/README.md) - OmniCore details

2. **Explore Components**
   - Generator: AI-powered code generation
   - OmniCore Engine: Orchestration and coordination
   - Self-Fixing Engineer: Automated maintenance and healing

3. **Customize Configuration**
   - Edit `.env` for environment variables
   - Update `generator/config.yaml` for Generator settings
   - Update `omnicore_engine/config.yaml` for OmniCore settings
   - Update `self_fixing_engineer/crew_config.yaml` for SFE settings

4. **Set Up Monitoring**
   - Configure Prometheus metrics
   - Set up Grafana dashboards
   - Enable OpenTelemetry tracing

5. **Deploy to Production**
   - See [DEPLOYMENT.md](./DEPLOYMENT.md)
   - Set up CI/CD pipelines
   - Configure cloud services

### Development Workflow

1. Create a feature branch: `git checkout -b feature/my-feature`
2. Make changes and test: `make test`
3. Run quality checks: `make ci-local` (runs strict linting, type checking, security scans, and tests)
4. Commit and push: `git commit -am "Add feature" && git push`
5. Create a pull request

> **Tip**: Always run `make ci-local` before pushing to catch issues early. All checks run with strict error checking.

### Contributing

See [Contribution Guidelines](./README.md#contribution-guidelines) in the main README.

---

**Ready to build production-ready applications with AI?** 🚀

For detailed documentation, visit the [OmniCore Engine docs](./omnicore_engine/docs/) or check out the [Generator README](./generator/README.md).
