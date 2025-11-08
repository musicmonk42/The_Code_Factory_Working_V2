# 🔌 Self Fixing Engineer™ – Plugin System Guide  
*Confidential & Proprietary. All Rights Reserved.*

---

## Table of Contents

1. [Overview](#overview)
2. [Plugin Philosophy & Design Goals](#plugin-philosophy--design-goals)
3. [Plugin Types Supported](#plugin-types-supported)
4. [Manifest Schema & Best Practices](#manifest-schema--best-practices)
5. [Secure Plugin Lifecycle: Build → Test → Approve → Deploy](#secure-plugin-lifecycle-build--test--approve--deploy)
6. [Permissioning, Capabilities & Policy](#permissioning-capabilities--policy)
7. [Python Plugin Blueprint](#python-plugin-blueprint)
8. [WASM Plugin Blueprint](#wasm-plugin-blueprint)
9. [gRPC Plugin Blueprint](#grpc-plugin-blueprint)
10. [Onboarding & Health Checks](#onboarding--health-checks)
11. [Local & CI/CD Testing](#local--cicd-testing)
12. [Security, Sandboxing & Auditability](#security-sandboxing--auditability)
13. [Versioning, Gallery & Marketplace](#versioning-gallery--marketplace)
14. [Troubleshooting & Support](#troubleshooting--support)
15. [Appendix: Reference Templates](#appendix-reference-templates)
16. [Contacts, Contribution & Responsible Disclosure](#contacts-contribution--responsible-disclosure)

---

## 1. Overview

The Self Fixing Engineer™ platform offers a robust, contract-driven plugin architecture supporting Python, WASM, and gRPC extensions.  
All plugins are sandboxed, auditable, and policy-governed to ensure zero-trust extensibility at every integration surface.

---

## 2. Plugin Philosophy & Design Goals

- **Zero Trust by Default:** Every plugin operates with least privilege, isolated from platform core and other plugins.
- **Declarative Manifest-First:** All capabilities, permissions, and entrypoints are explicitly declared and reviewed.
- **Multi-Runtime Flexibility:** Native support for Python, WASM, and gRPC enables diverse, polyglot plugin development.
- **Auditability & Traceability:** Every plugin action is logged, signed, and traceable via the audit mesh.
- **Hot-Swap, Hot-Upgrade, and Policy-Driven Lifecycle:** Plugins may be loaded, unloaded, and upgraded without platform downtime.

---

## 3. Plugin Types Supported

- **Python:** Fastest for rapid prototyping, analysis, and custom logic.
- **WASM:** Near-native speed and security, for compute/memory-intensive or cross-language plugins.
- **gRPC:** Ideal for polyglot/microservice plugins or connecting to external services (internal or SaaS).

---

## 4. Manifest Schema & Best Practices

Every plugin must include a manifest (JSON or YAML), with the following fields:

| Field         | Required | Description                                         |
|---------------|----------|-----------------------------------------------------|
| name          | Yes      | Unique plugin name (lower_snake_case)               |
| type          | Yes      | python, wasm, or grpc                               |
| entrypoint    | Yes      | Main callable (module:function for Python, export for WASM, service/method for gRPC) |
| health_check  | Yes      | Health check callable (same rules as entrypoint)    |
| permissions   | Yes      | Array of allowed capabilities (see §6)              |
| version       | No       | Semantic version (update with every change)         |
| wasm_path     | WASM     | Path to .wasm binary                                |
| grpc_proto    | gRPC     | Path to .proto spec                                 |
| grpc_endpoint | gRPC     | Host:port of gRPC service                           |
| description   | No       | One-line human summary                              |
| author        | No       | Author or company                                   |
| tags          | No       | Keywords for search/gallery                         |
| capabilities  | No       | Advanced: for dashboard/analytics (e.g., ["metrics"])|

**Best Practices:**

- Always set strict permissions and document any external access.
- Version and checksum your manifest; validate during CI and onboarding.
- Keep manifest and code/proto in sync—mismatches may be rejected by onboarding.

---

## 5. Secure Plugin Lifecycle: Build → Test → Approve → Deploy

- **Build:** Develop plugin and manifest in a feature branch or isolated environment.
- **Test:** Local health checks (`python plugin_manager.py --health-check ...`), unit/integration tests.
- **Approve:** All plugins are reviewed (automated + human-in-the-loop) for manifest, code, and permission compliance.
- **Deploy:** Onboarded plugins are loaded, sandboxed, and health-checked at runtime.

Every deploy/upgrade/downgrade is audit-logged and traceable.

---

## 6. Permissioning, Capabilities & Policy

- **Required:** All permissions must be explicitly listed in permissions—nothing is granted implicitly.

**Supported Permissions:**  
`"read"`, `"write"`, `"filesystem"`, `"network"`, `"compute"`, `"secrets"`, `"metrics"`, `"audit"`, `"cloud"` (granular custom perms allowed).

- Sensitive actions (`filesystem`, `secrets`, `network`) require additional review and human approval.
- Plugins requesting `"secrets"` or `"cloud"` access must use platform APIs; direct credential access is forbidden.
- Policy engine validates each action at runtime; violations auto-disable the plugin and trigger alerts.

---

## 7. Python Plugin Blueprint

**plugin.py**
```python
def main(request):
    # Request: dict, e.g., {"input": 42}
    return {"output": request.get("input", 0) + 1}

def health():
    return True
```

**manifest.json**
```json
{
  "name": "example_python_plugin",
  "type": "python",
  "entrypoint": "plugin:main",
  "health_check": "plugin:health",
  "permissions": ["read", "write"]
}
```

---

## 8. WASM Plugin Blueprint

**manifest.json**
```json
{
  "name": "example_wasm_plugin",
  "type": "wasm",
  "entrypoint": "run",
  "health_check": "health",
  "permissions": ["compute"],
  "wasm_path": "plugins/example.wasm"
}
```

**WASM Notes:**

- Write in Rust, AssemblyScript, or C/C++.
- Export a `run(input_ptr, input_len) -> output_ptr` and `health() -> int`.
- Use wasmtime or pyodide for validation and sandboxing.

---

## 9. gRPC Plugin Blueprint

**manifest.json**
```json
{
  "name": "example_grpc_plugin",
  "type": "grpc",
  "entrypoint": "EchoService.Run",
  "health_check": "EchoService.Ping",
  "permissions": ["compute"],
  "grpc_proto": "plugins/echo.proto",
  "grpc_endpoint": "localhost:50051"
}
```

**echo.proto excerpt:**
```proto
syntax = "proto3";
service EchoService {
  rpc Run (EchoRequest) returns (EchoReply) {}
  rpc Ping (HealthRequest) returns (HealthReply) {}
}
message EchoRequest { int32 input = 1; }
message EchoReply { int32 output = 1; }
message HealthRequest {}
message HealthReply { bool healthy = 1; }
```

---

## 10. Onboarding & Health Checks

- Plugins are discovered by scanning the `plugins/` directory for manifests.
- Each plugin must pass a health check:
```bash
python plugin_manager.py --health-check <plugin_name>
```
- Failing health checks or permission mismatches will block onboarding.
- Onboarding wizard available for step-by-step plugin addition, review, and approval.

---

## 11. Local & CI/CD Testing

- Use provided test suites and `test_plugin_manager_and_example_plugin.py`.
- All plugins must pass:
  - Health check
  - Manifest validation
  - Permission enforcement
  - Sample input/output roundtrip
- CI pipeline runs:
  - Static analysis (Bandit, pylint, flake8)
  - Dynamic permission/policy simulation
  - Security and compatibility scans

---

## 12. Security, Sandboxing & Auditability

- **Python:** Restricted interpreter, whitelisted imports, CPU/memory/time quotas.
- **WASM:** Run in dedicated VM/process, with syscall/memory limits.
- **gRPC:** Network-restricted, firewall or container-isolated.

- **Audit:** All plugin actions logged (who, what, when, result, policy decision).
- Any privilege escalation or violation disables plugin and triggers incident workflow.
- Never hardcode credentials, secrets, or environment-specific values.

---

## 13. Versioning, Gallery & Marketplace

- Plugins should use semantic versioning in manifests.
- Plugin Gallery/Marketplace is available for enterprise customers to discover, approve, and deploy vetted plugins.
- Deprecated/unsupported plugins are automatically quarantined.

---

## 14. Troubleshooting & Support

- Use onboarding wizard and CLI health check for debugging.
- Logs for all plugin actions are stored in the audit mesh.
- For plugin failures:
  - Review logs and audit chain
  - Re-run health checks
  - Revert or quarantine via onboarding wizard

---

## 15. Appendix: Reference Templates

- Full example plugin manifests (Python, WASM, gRPC)
- Sample onboarding wizard session output
- Policy contract schema for custom permissions

---

## 16. Contacts, Contribution & Responsible Disclosure

- **Plugin Gallery, Documentation & SDKs:** [https://yourcompany.com/self-fixing-engineer/plugins]
- **Support:** [support@yourcompany.com]
- **Security/Disclosure:** [security@yourcompany.com] (see [SECURITY.md] for PGP key)
- **Custom plugin requests:** Contact customer success or solutions engineering

---

*This guide is a living, audit-backed policy—adherence is mandatory for all integrations.*  
*Your plugin = your contract.*