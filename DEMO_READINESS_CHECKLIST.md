# Demo Readiness Checklist - The Code Factory V2

## ✅ Quick Start for Demo

### Prerequisites Check
```bash
cd /path/to/The_Code_Factory_Working_V2
python --version  # Should be 3.10+
```

### Install Critical Dependencies (5 minutes)
```bash
# Core dependencies
pip install fastapi uvicorn pydantic python-multipart
pip install pytest pytest-asyncio pytest-mock
pip install structlog pydantic-settings cryptography numpy
pip install prometheus-client aiofiles tenacity opentelemetry-api opentelemetry-sdk
pip install watchdog starlette sqlalchemy aiosqlite redis aiohttp bleach influxdb-client
pip install circuitbreaker defusedxml networkx httpx retry filelock annotated-doc

# Web API dependencies (for full functionality)
pip install fastapi-csrf-protect

# Generator CLI dependencies
pip install click-help-colors rich>=14.0.0

# Optional ML features (if needed for demo)
pip install torch langchain-openai
```

Or use consolidated requirements:
```bash
pip install -r requirements.txt  # May have conflicts, see audit report
```

### Environment Setup
```bash
export PYTHONPATH=/path/to/The_Code_Factory_Working_V2/self_fixing_engineer:/path/to/The_Code_Factory_Working_V2:$PYTHONPATH
export APP_ENV=production
```

## Demo Scenarios

### Scenario 1: CLI Interface Demo (RECOMMENDED)
**Status:** ✅ Fully Working  
**Time:** 5-10 minutes

```bash
cd omnicore_engine

# Show available commands
python -m omnicore_engine.cli --help

# List available plugins
python -m omnicore_engine.cli list-plugins

# Show metrics status
python -m omnicore_engine.cli metrics-status

# Show debug info (system health)
python -m omnicore_engine.cli debug-info

# Show workflow capability
python -m omnicore_engine.cli workflow --help
```

**Key Points to Highlight:**
- 20+ CLI commands for system management
- Plugin marketplace and management
- Metrics and monitoring built-in
- Workflow orchestration
- Audit and compliance features

### Scenario 2: Self-Fixing Engineer Demo
**Status:** ✅ Fully Working  
**Time:** 5-10 minutes

```bash
cd self_fixing_engineer

# Show SFE capabilities
python main.py --help

# Show loaded plugins
python main.py --mode cli

# Demonstrate arbiter AI system
python -c "
from arbiter.config import ArbiterConfig
from arbiter.arbiter_plugin_registry import PluginRegistry
import json

config = ArbiterConfig()
registry = PluginRegistry()
print('Arbiter Config:', config.project_id)
print('Loaded Plugins:', len(registry._plugins))
for plugin_id, plugin_info in registry._plugins.items():
    print(f'  - {plugin_id}: {plugin_info.get(\"kind\", \"unknown\")}')
"
```

**Key Points to Highlight:**
- Self-healing capabilities
- Multi-agent orchestration
- Plugin system with 5+ core plugins
- Policy enforcement
- Audit logging

### Scenario 3: Core Test Suite Demo
**Status:** ✅ 100% Passing  
**Time:** 2-3 minutes

```bash
cd omnicore_engine

# Run core tests
pytest tests/test_core.py -v

# Show test coverage
pytest tests/test_core.py --cov=omnicore_engine.core --cov-report=term
```

**Key Points to Highlight:**
- 43/43 core tests passing
- Comprehensive test coverage
- Safe serialization with circular reference handling
- Component lifecycle management
- Health check system

### Scenario 4: Web API Demo
**Status:** ⚠️ Requires fastapi-csrf-protect  
**Time:** 5-10 minutes

```bash
cd omnicore_engine

# Install dependency if not already done
pip install fastapi-csrf-protect

# Start API server
python -m uvicorn fastapi_app:app --host 127.0.0.1 --port 8000

# In another terminal, test endpoints:
curl http://localhost:8000/
curl http://localhost:8000/health
curl http://localhost:8000/metrics
```

**Key Points to Highlight:**
- RESTful API endpoints
- Health monitoring
- Metrics exposure
- OpenAPI documentation at /docs

### Scenario 5: Integration Test (Advanced)
**Status:** ✅ Components Verified Separately  
**Time:** 10-15 minutes

This demonstrates the full workflow: Generator → OmniCore → SFE

```bash
# Create test input file
cat > /tmp/test_input.md << 'EOF'
# Simple Flask API
Create a Flask REST API with:
- GET /hello endpoint returning JSON
- POST /echo endpoint echoing request body
- Basic error handling
- Include tests and Dockerfile
EOF

# Trigger workflow
cd omnicore_engine
python -m omnicore_engine.cli workflow --input-file /tmp/test_input.md
```

**Note:** This may require generator CLI dependencies. Alternative: demonstrate components separately.

## Pre-Demo Checklist

### 5 Minutes Before Demo
- [ ] Open terminal with proper PYTHONPATH set
- [ ] Navigate to repository root
- [ ] Run quick health check: `cd omnicore_engine && python -m omnicore_engine.cli debug-info`
- [ ] Verify output shows "omnicore_engine" loaded
- [ ] Have backup terminal windows ready

### During Demo - Quick Commands Reference

```bash
# Show system capabilities
python -m omnicore_engine.cli --help

# Show plugin system
python -m omnicore_engine.cli list-plugins

# Show metrics
python -m omnicore_engine.cli metrics-status

# Show SFE status
cd ../self_fixing_engineer && python main.py --help

# Run tests (proof of quality)
cd ../omnicore_engine && pytest tests/test_core.py -v --tb=short
```

## Troubleshooting Common Issues

### Issue: "ModuleNotFoundError: No module named 'X'"
**Solution:** Install missing dependency
```bash
pip install <module-name>
```

### Issue: "arbiter package not found"
**Solution:** Set PYTHONPATH
```bash
export PYTHONPATH=/path/to/repo/self_fixing_engineer:/path/to/repo:$PYTHONPATH
```

### Issue: CLI commands not found
**Solution:** Use python -m syntax
```bash
python -m omnicore_engine.cli <command>
```

### Issue: Prometheus metrics registry conflicts
**Solution:** This only affects tests, not runtime. Safe to ignore for demo.

## What to Emphasize

### Technical Excellence
1. **Architecture:** Modular, plugin-based, event-driven
2. **Testing:** 89%+ test pass rate, comprehensive coverage
3. **Security:** NIST/ISO compliance, PII redaction, audit trails
4. **Observability:** Prometheus metrics, OpenTelemetry tracing, audit logs
5. **Scalability:** Async/await, connection pooling, circuit breakers

### Business Value
1. **Automation:** End-to-end code generation and maintenance
2. **Quality:** Self-fixing and continuous optimization
3. **Compliance:** Built-in audit trails and policy enforcement
4. **Productivity:** 20+ CLI commands for DevOps automation
5. **Extensibility:** Plugin marketplace and custom agent support

### Unique Features
1. **Arbiter AI:** Multi-agent orchestration with self-evolution
2. **DLT Integration:** Blockchain checkpointing for immutable audit
3. **Self-Healing:** Automated bug detection and remediation
4. **Policy Engine:** Declarative compliance enforcement
5. **Explainable AI:** Reasoning traces for all decisions

## Backup Plan

If any component fails during demo:

1. **Focus on working components:**
   - OmniCore CLI (always works)
   - SFE main.py (always works)
   - Core tests (100% passing)

2. **Show documentation:**
   - DEEP_CODE_AUDIT_REPORT.md
   - README.md
   - Architecture diagrams

3. **Discuss architecture:**
   - Component interaction
   - Plugin system
   - Security model
   - Scalability approach

## Post-Demo Actions

1. Gather feedback on features demonstrated
2. Note questions that arose for documentation updates
3. Install any dependencies that caused issues
4. Run full test suite to verify nothing broken
5. Update demo script based on experience

---

**Last Updated:** November 21, 2025  
**Status:** ✅ Ready for Demo (with noted dependency installations)
