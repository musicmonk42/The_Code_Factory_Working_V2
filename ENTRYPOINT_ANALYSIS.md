# Code Factory Platform - Entrypoint Analysis & Recommendation

## Executive Summary

**Recommendation: Maintain Three Separate Entrypoints**

The Code Factory Platform currently has three distinct entrypoints that should **remain separate** for the following strategic reasons:

1. **Architectural Clarity**: Each module serves a distinct purpose in the software lifecycle
2. **Deployment Flexibility**: Allows independent scaling and deployment of components
3. **Development Agility**: Teams can work on modules independently without conflicts
4. **Resource Optimization**: Components can be deployed based on specific needs

However, we recommend creating a **unified launcher script** for simplified local development while preserving the architectural separation.

---

## Current Architecture

### Three Main Entrypoints

#### 1. Generator Module (`generator/main/main.py`)
- **Purpose**: README-to-App Code Generator (RCG)
- **Lines**: ~1,190 lines in main.py
- **Components**: 171 Python files
- **Responsibilities**:
  - Natural language → production code transformation
  - Code generation (codegen_agent)
  - Test generation (testgen_agent)
  - Documentation generation (docgen_agent)
  - Deployment config generation (deploy_agent)
  - Security critique (critique_agent)
- **Interfaces**: CLI, GUI, API, ALL
- **Default Port**: 8000

#### 2. OmniCore Engine (`omnicore_engine/cli.py` + `omnicore_engine/fastapi_app.py`)
- **Purpose**: Central Orchestration Hub
- **Components**: 77 Python files
- **Responsibilities**:
  - Message bus coordination (ShardedMessageBus)
  - Plugin registry management
  - State persistence (Database)
  - Workflow orchestration
  - Event routing between Generator ↔ SFE
  - Audit logging (ExplainAudit)
- **Interfaces**: CLI, API
- **Default Port**: 8000 (API)

#### 3. Self-Fixing Engineer (`self_fixing_engineer/main.py`)
- **Purpose**: Autonomous AI Maintenance System
- **Lines**: ~1,102 lines in main.py
- **Components**: 552 Python files (including 26,626+ lines in Arbiter)
- **Responsibilities**:
  - Code analysis (CodebaseAnalyzer)
  - Self-healing (Bug Manager)
  - Compliance enforcement (Guardrails)
  - Continuous optimization (Reinforcement Learning)
  - Knowledge graph management
  - Arena system for agent competition
- **Interfaces**: CLI, API, WEB
- **Default Port**: 8000 (API)

---

## Integration Analysis

### Current Integration Patterns

```
┌─────────────────┐         ┌──────────────────┐         ┌───────────────────┐
│    Generator    │────────▶│  OmniCore Engine │────────▶│ Self-Fixing Eng.  │
│   main.py       │         │  cli.py / app.py │         │    main.py        │
└─────────────────┘         └──────────────────┘         └───────────────────┘
        │                            │                             │
        │                            │                             │
        ▼                            ▼                             ▼
   Port 8000                    Message Bus                   Port 8000
   (configurable)              ShardedMessageBus              (configurable)
```

### Dependency Flow
- **Generator** → Independent (no imports from other modules)
- **OmniCore** → Imports from SFE (`self_fixing_engineer.simulation`, `crew_manager`)
- **SFE** → Independent (no imports from other modules)

### Communication Method
- **Primary**: Message Bus (ShardedMessageBus via Redis)
- **Secondary**: REST API calls (optional)
- **Data Sharing**: Shared database (PostgreSQL/SQLite)

---

## Deployment Configurations

### Current Docker Deployment
From `docker-compose.yml`:
```yaml
# Single unified service
codefactory:
  build: .
  ports:
    - "8000:8000"
  command: python -m uvicorn omnicore_engine.fastapi_app:app --host 0.0.0.0 --port 8000
```

**Observation**: Despite being a "unified platform", only ONE entrypoint is started by default (OmniCore API).

### From Dockerfile
```dockerfile
# Note: All three modules (generator, omnicore_engine, self_fixing_engineer) are part
# of a single unified platform. Dependencies are installed from the root requirements.txt
# which includes all necessary packages for the entire platform.
```

### From QUICKSTART.md
```bash
# Generator: http://localhost:8000
# OmniCore API: http://localhost:8001  # ← Different ports!
```

**Inconsistency Detected**: Documentation shows separate ports, but Docker Compose uses single port.

---

## Use Cases Analysis

### When to Use Each Entrypoint Separately

#### Generator Only
**Use Case**: Code generation as a service
```bash
python generator/main/main.py --interface api
```
**Benefits**:
- Lightweight deployment
- Focused on code generation tasks
- No overhead from other modules
- Ideal for CI/CD pipelines

#### OmniCore Only
**Use Case**: Message bus and orchestration without generation/fixing
```bash
python omnicore_engine/cli.py serve
```
**Benefits**:
- Central coordination point
- Message routing and plugin management
- Minimal resource footprint
- Great for debugging message flows

#### SFE Only
**Use Case**: Code maintenance and optimization for existing codebases
```bash
python self_fixing_engineer/main.py --mode api
```
**Benefits**:
- Focus on self-healing capabilities
- Arbiter AI for code analysis
- No generation overhead
- Suitable for maintenance-focused deployments

### When to Use All Together
**Use Case**: Complete platform functionality
```bash
# Terminal 1: Generator
python generator/main/main.py --interface api --port 8000

# Terminal 2: OmniCore
python omnicore_engine/cli.py serve --port 8001

# Terminal 3: SFE
python self_fixing_engineer/main.py --mode api --port 8002
```
**Benefits**:
- Full README → Production workflow
- Self-healing maintenance
- Complete observability

---

## Comparison: Separate vs. Unified

### Option A: Keep Three Separate Entrypoints (RECOMMENDED)

#### Pros ✅
1. **Architectural Clarity**: Each module's purpose is explicit
2. **Independent Scaling**: Scale only what you need
   - Heavy code generation? Scale Generator
   - Complex workflows? Scale OmniCore
   - Large codebases? Scale SFE
3. **Selective Deployment**: Deploy only required modules
4. **Easier Testing**: Test modules in isolation
5. **Team Autonomy**: Different teams can own different modules
6. **Resource Optimization**: Don't run what you don't need
7. **Failure Isolation**: One module failure doesn't crash others
8. **Follows Microservices Best Practices**

#### Cons ❌
1. **Configuration Complexity**: Three separate configs
2. **Startup Overhead**: Need to start multiple processes
3. **Port Management**: Need to assign different ports
4. **Learning Curve**: Users need to understand which module to use

### Option B: Create Single Unified Entrypoint

#### Pros ✅
1. **Simplified Startup**: One command to start everything
2. **Single Configuration**: One place for all settings
3. **Easier for Beginners**: Less to understand initially
4. **Consistent Port Management**: Single entry point

#### Cons ❌
1. **Resource Waste**: Always runs all modules even if not needed
2. **Harder to Scale**: Can't scale components independently
3. **Monolithic Deployment**: Against microservices principles
4. **Debugging Complexity**: Harder to isolate issues
5. **Conflicts**: Potential for module interactions to cause problems
6. **Loss of Flexibility**: Can't deploy subsets of functionality

---

## Recommendation: Hybrid Approach

### Primary Recommendation: Keep Separate Entrypoints + Add Unified Launcher

**Create a new `launch.py` script at the repository root** that:

1. **Preserves architectural separation** (three modules remain independent)
2. **Provides convenience** for local development
3. **Allows selective module launch** via flags
4. **Manages ports automatically**
5. **Handles graceful shutdown**
6. **Shows unified status dashboard**

### Proposed Implementation

#### New File: `/launch.py`
```python
#!/usr/bin/env python3
"""
Code Factory Platform - Unified Launcher
Launch one, two, or all three modules with a single command.

Usage:
    python launch.py --all                    # Launch all modules
    python launch.py --generator              # Generator only
    python launch.py --omnicore               # OmniCore only
    python launch.py --sfe                    # SFE only
    python launch.py --generator --sfe        # Generator + SFE only
    python launch.py --all --ports 8000,8001,8002  # Custom ports
"""
```

**Features**:
- Launch any combination of modules
- Automatic port assignment (8000, 8001, 8002)
- Health checks before declaring ready
- Unified logging output with module tags
- Graceful shutdown of all processes
- Status dashboard showing running modules

### Architecture with Launcher

```
                           ┌─────────────────────┐
                           │    launch.py        │
                           │  (New Convenience)  │
                           └──────────┬──────────┘
                                      │
                    ┌─────────────────┼─────────────────┐
                    │                 │                 │
                    ▼                 ▼                 ▼
          ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
          │  Generator   │  │  OmniCore    │  │     SFE      │
          │  main.py     │  │  cli.py      │  │  main.py     │
          │  (Port 8000) │  │  (Port 8001) │  │  (Port 8002) │
          └──────────────┘  └──────────────┘  └──────────────┘
```

---

## Implementation Plan

### Phase 1: Keep Current Entrypoints (No Changes)
**Why**: They work well and follow best practices
**Action**: Document their proper usage clearly

### Phase 2: Create Unified Launcher (Optional Enhancement)
**Why**: Improves developer experience
**Files to Create**:
- `/launch.py` - Unified launcher script
- `/launch_config.yaml` - Configuration for launcher

### Phase 3: Update Documentation
**Files to Update**:
- `README.md` - Add launcher instructions
- `QUICKSTART.md` - Show both approaches
- `DEPLOYMENT.md` - Clarify production deployment patterns

### Phase 4: Fix Inconsistencies
**Issues Found**:
1. Port conflicts in docker-compose.yml vs documentation
2. QUICKSTART shows ports 8000 and 8001, Docker uses only 8000
3. Missing example of running all three together

---

## Specific Issues to Address

### 1. Port Configuration Inconsistency
**Problem**: 
- QUICKSTART.md says Generator on 8000, OmniCore on 8001
- docker-compose.yml only exposes 8000 and runs OmniCore

**Fix**:
```yaml
# docker-compose.yml (proposed changes)
services:
  generator:
    ports: ["8000:8000"]
    command: python generator/main/main.py --interface api
  
  omnicore:
    ports: ["8001:8001"]
    command: python omnicore_engine/cli.py serve
  
  sfe:
    ports: ["8002:8002"]
    command: python self_fixing_engineer/main.py --mode api
```

### 2. Missing Clear Startup Instructions
**Problem**: Users don't know which entrypoint to use when

**Fix**: Create decision tree in QUICKSTART.md
```
Need code generation? → Use Generator
Need orchestration? → Use OmniCore
Need self-healing? → Use SFE
Need everything? → Use all three (or launcher)
```

### 3. Makefile Targets Don't Show All Options
**Problem**: `make run-generator` and `make run-omnicore` exist, but no `make run-sfe`

**Fix**: Add missing targets and a `make run-all` target

---

## Conclusion

### Final Recommendation: **THREE SEPARATE ENTRYPOINTS**

**Rationale**:
1. ✅ Follows microservices architecture best practices
2. ✅ Provides maximum deployment flexibility
3. ✅ Enables independent scaling
4. ✅ Reduces resource waste
5. ✅ Improves failure isolation
6. ✅ Aligns with the modular design philosophy stated in REPOSITORY_CAPABILITIES.md

### Enhancement: **ADD UNIFIED LAUNCHER (OPTIONAL)**

**Rationale**:
1. 🎯 Improves developer experience
2. 🎯 Simplifies local development
3. 🎯 Doesn't compromise architectural benefits
4. 🎯 Provides both simplicity AND flexibility

### What NOT to Do: ❌

1. ❌ **Don't merge into single monolithic entrypoint**
   - Loses flexibility
   - Against stated architecture
   - Reduces deployment options

2. ❌ **Don't force all modules to run together**
   - Wastes resources
   - Complicates debugging
   - Removes selective deployment capability

---

## Implementation Priority

### Must Do Now:
1. ✅ **Fix documentation inconsistencies** (ports, startup instructions)
2. ✅ **Add decision tree** to help users choose entrypoint
3. ✅ **Fix docker-compose.yml** to match documented architecture

### Should Do Soon:
1. 🔜 **Create unified launcher** (`launch.py`) for convenience
2. 🔜 **Add `make run-sfe`** to Makefile
3. 🔜 **Add `make run-all`** to Makefile

### Nice to Have:
1. 💡 **Create visual dashboard** showing running modules
2. 💡 **Add health check aggregator** across all modules
3. 💡 **Create docker-compose profiles** for different deployment scenarios

---

## Questions for Stakeholders

Before finalizing, consider:

1. **What is the primary deployment scenario?**
   - Local development → Launcher is valuable
   - Kubernetes → Separate services preferred
   - Single server → Consider unified approach

2. **What is the typical use case?**
   - Always need all three? → Consider unified
   - Often need subsets? → Keep separate

3. **What is the scaling strategy?**
   - Independent scaling? → Keep separate
   - Scale together? → Could unify

4. **What is the team structure?**
   - Different teams per module? → Keep separate
   - Single team? → Could unify

---

## Appendix: Current Entrypoint Details

### Generator Main.py Capabilities
- Interfaces: CLI, GUI, API, ALL
- Config hot-reload support
- OpenTelemetry tracing
- Prometheus metrics
- Health checks
- Signal handling
- Provenance tracking

### OmniCore CLI.py Capabilities
- Commands: serve, simulate, list-plugins, benchmark, optimize
- Message bus operations
- Plugin management
- Audit queries
- Debug info
- REPL mode

### SFE Main.py Capabilities
- Modes: CLI, API, WEB
- Simulation module integration
- Test generation orchestrator
- Arbiter AI engine
- Health/readiness endpoints
- Optional uvloop
- Sentry integration

---

**Document Version**: 1.0  
**Date**: November 24, 2025  
**Author**: AI Code Analysis Agent  
**Status**: Draft for Review
