# Generator Docker Build - DEPRECATED

**⚠️ IMPORTANT: This module's standalone Dockerfile has been deprecated.**

## Unified Platform Build

The Code Factory platform is built as a **unified system** where all three modules (Generator, OmniCore Engine, and Self-Fixing Engineer) are integrated and deployed together.

### Building the Entire Platform

Use the **root Dockerfile** to build the complete platform:

```bash
# From the repository root
docker build -t code-factory:latest -f Dockerfile .

# Or use the Makefile
make docker-build
```

### Running the Platform

Use Docker Compose to run all services:

```bash
# Start all services
make docker-up

# Or directly with docker-compose
docker compose up -d
```

### Why Unified Build?

1. **Shared Dependencies**: All modules share the same Python dependencies
2. **Integrated Architecture**: Modules communicate via shared message bus
3. **Simplified Deployment**: One image, consistent versioning
4. **Reduced Build Time**: Single build process with layer caching
5. **Easier Maintenance**: Single Dockerfile to maintain

### Legacy Standalone Build

The previous standalone generator Dockerfile has been removed as it is no longer needed. All builds should use the unified platform Dockerfile at the repository root.

For any questions, refer to:
- [Main README](../README.md)
- [Deployment Guide](../DEPLOYMENT.md)
- [Quick Start Guide](../QUICKSTART.md)
