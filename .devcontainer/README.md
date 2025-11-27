# Development Container Configuration - Code Factory Platform

This directory contains the configuration for VS Code Dev Containers, providing a consistent development environment.

## Directory Structure

```
.devcontainer/
├── README.md           # This file
├── Dockerfile          # Development container image
├── devcontainer.json   # VS Code Dev Container configuration
└── post-create.sh      # Post-creation setup script
```

## Quick Start

### Prerequisites

- [VS Code](https://code.visualstudio.com/)
- [Remote - Containers extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers)
- [Docker Desktop](https://www.docker.com/products/docker-desktop)

### Opening in Dev Container

1. Open the repository in VS Code
2. When prompted, click "Reopen in Container"
3. Or use Command Palette: `Remote-Containers: Reopen in Container`

## What's Included

### Development Tools

The container includes:

- **Python 3.11** with virtual environment
- **Linting**: black, ruff, flake8, mypy
- **Testing**: pytest, pytest-cov, pytest-asyncio
- **Security**: bandit, safety, pip-audit
- **Debugging**: ipython, ipdb

### VS Code Extensions

Pre-installed extensions:

- Python language support and Pylance
- Black formatter and isort
- Ruff linter
- Docker tools
- YAML/TOML support
- GitLens
- GitHub Copilot

### Services

The development environment includes:

| Service | Port | Purpose |
|---------|------|---------|
| Redis | 6379 | Message bus and caching |
| PostgreSQL | 5432 | Database (codefactory_dev) |

## Configuration Files

### devcontainer.json

Main configuration file defining:

- Docker Compose file to use
- Workspace folder location
- Port forwarding
- VS Code settings and extensions
- Environment variables

### Dockerfile

Builds the development container with:

- Python development tools
- System dependencies
- Security scanning tools

### post-create.sh

Runs after container creation:

- Installs project dependencies
- Sets up pre-commit hooks
- Configures git

## Environment Variables

The container sets these environment variables:

```bash
PYTHONUNBUFFERED=1
PYTHONDONTWRITEBYTECODE=1
APP_ENV=development
DEBUG=true
```

## Forwarded Ports

| Port | Service |
|------|---------|
| 8000 | Generator API |
| 8001 | OmniCore API |
| 8002 | SFE API |
| 3000 | Grafana |
| 9090 | Prometheus |
| 6379 | Redis |
| 5432 | PostgreSQL |

## Database Access

PostgreSQL credentials for development:

```
Host: localhost (or postgres from within container)
Port: 5432
User: codefactory
Password: devpassword
Database: codefactory_dev
```

## Common Tasks

### Running Tests

```bash
# Run all tests
make test

# Run specific component tests
make test-generator
make test-omnicore
make test-sfe
```

### Linting and Formatting

```bash
# Run all linters
make lint

# Format code
make format

# Type checking
make type-check
```

### Building Docker Images

```bash
# Build all images
make docker-build

# Start production-like services
make docker-up
```

## Troubleshooting

### Container Build Fails

1. Ensure Docker has enough resources (CPU, memory)
2. Try rebuilding without cache:
   ```bash
   docker-compose -f docker-compose.dev.yml build --no-cache
   ```

### Port Conflicts

If ports are already in use:

1. Stop conflicting services
2. Or modify port mappings in `docker-compose.dev.yml`

### Slow File Operations

For better performance on macOS/Windows:

1. Use `:cached` volume mount mode (already configured)
2. Consider using a named volume for node_modules if using Node.js

## Related Documentation

- [CI_CD_GUIDE.md](../CI_CD_GUIDE.md) - CI/CD pipeline documentation
- [QUICKSTART.md](../QUICKSTART.md) - Getting started guide
- [Makefile](../Makefile) - Available make commands
