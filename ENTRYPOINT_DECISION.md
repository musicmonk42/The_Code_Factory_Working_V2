# Entrypoint Decision Summary

## Analysis Completed: November 24, 2025

### Question
Should the three main entrypoints of the Code Factory Platform be consolidated into a single unified entrypoint or kept as three separate entrypoints?

### Answer: **Keep Three Separate Entrypoints** ✓

## Rationale

After comprehensive analysis of the repository structure, architecture, and deployment patterns, the recommendation is to **maintain three separate entrypoints** for the following reasons:

### 1. **Architectural Clarity**
Each module has a distinct, well-defined purpose:
- **Generator** (`generator/main/main.py`): Code generation from natural language
- **OmniCore** (`omnicore_engine/cli.py` + `fastapi_app.py`): Message bus and orchestration
- **SFE** (`self_fixing_engineer/main.py`): Self-healing and maintenance

### 2. **Deployment Flexibility**
Separate entrypoints enable:
- Independent scaling of components
- Selective deployment based on needs
- Different resource allocation per module
- Targeted updates and rollbacks

### 3. **Resource Optimization**
Running only needed modules:
- Code generation service → Generator only
- Maintenance workflows → SFE only
- Orchestration hub → OmniCore only
- Full platform → All three

### 4. **Microservices Best Practices**
Aligns with industry standards:
- Loose coupling
- High cohesion
- Independent deployability
- Failure isolation

### 5. **Development Agility**
Teams can:
- Work on modules independently
- Test in isolation
- Deploy without affecting others
- Scale development efforts

## Enhancement: Unified Launcher

To maintain flexibility while improving developer experience, we've added an optional **unified launcher** (`launch.py`):

### Quick Start - All Modules
```bash
python launch.py --all
```

### Selective Launch
```bash
# Generator only
python launch.py --generator

# OmniCore + SFE
python launch.py --omnicore --sfe
```

### Development Mode
```bash
# With auto-reload
python launch.py --all --dev
```

### Using Make
```bash
# Launch all modules
make run-all

# Individual modules
make run-generator  # Port 8000
make run-omnicore   # Port 8001
make run-sfe        # Port 8002
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    launch.py (Optional)                      │
│                   Convenience Launcher                       │
└────────────────────────┬────────────────────────────────────┘
                         │
         ┌───────────────┼───────────────┐
         │               │               │
         ▼               ▼               ▼
┌────────────────┐ ┌────────────┐ ┌──────────────┐
│   Generator    │ │  OmniCore  │ │     SFE      │
│   main.py      │ │   cli.py   │ │   main.py    │
│  (Port 8000)   │ │ (Port 8001)│ │ (Port 8002)  │
└────────────────┘ └────────────┘ └──────────────┘
       │                 │                │
       └─────────────────┼────────────────┘
                         │
                         ▼
                 ShardedMessageBus
                    (via Redis)
```

## Module Details

### Generator Module
**Files**: 171 Python files  
**Entry**: `generator/main/main.py`  
**Port**: 8000 (default)  
**Purpose**: README-to-App code generation  
**Interfaces**: CLI, GUI, API, ALL

### OmniCore Engine
**Files**: 77 Python files  
**Entry**: `omnicore_engine/cli.py` or `fastapi_app.py`  
**Port**: 8001 (default)  
**Purpose**: Central orchestration and message routing  
**Interfaces**: CLI, API

### Self-Fixing Engineer
**Files**: 552 Python files  
**Entry**: `self_fixing_engineer/main.py`  
**Port**: 8002 (default)  
**Purpose**: AI-powered self-healing and maintenance  
**Interfaces**: CLI, API, WEB

## Use Case Guide

### When to Use Generator Only
```bash
python launch.py --generator
```
- Quick code generation tasks
- CI/CD pipeline integration
- Batch code generation
- Minimal resource usage

### When to Use OmniCore Only
```bash
python launch.py --omnicore
```
- Message bus debugging
- Plugin management
- Workflow orchestration
- Integration testing

### When to Use SFE Only
```bash
python launch.py --sfe
```
- Code maintenance workflows
- Security scanning
- Performance optimization
- Compliance checking

### When to Use All Three
```bash
python launch.py --all
```
- Complete platform functionality
- Full README → Production pipeline
- Self-healing production deployments
- Comprehensive testing

## Documentation

For detailed analysis, see:
- **[ENTRYPOINT_ANALYSIS.md](./ENTRYPOINT_ANALYSIS.md)** - Complete analysis with comparisons
- **[REPOSITORY_CAPABILITIES.md](./REPOSITORY_CAPABILITIES.md)** - Platform architecture
- **[QUICKSTART.md](./QUICKSTART.md)** - Getting started guide
- **[DEPLOYMENT.md](./DEPLOYMENT.md)** - Production deployment

## Summary

✅ **Three separate entrypoints maintained**  
✅ **Architectural flexibility preserved**  
✅ **Unified launcher added for convenience**  
✅ **Makefile updated with new targets**  
✅ **Documentation clarified**

This approach provides the **best of both worlds**: architectural separation for production deployments and simplified launching for development.

---

**Version**: 1.0.0  
**Date**: November 24, 2025  
**Status**: Implemented
