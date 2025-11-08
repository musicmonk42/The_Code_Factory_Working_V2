# CHANGELOG.md

## [Unreleased]

- (Keep this section updated with PRs and WIP features.)

---

## [v1.0.0] – Batteries Wired Up (2025-07-19)

### 🚀 Major Features

- **WASM and gRPC plugin support fully implemented and tested**
- **All major backends “batteries-included and wired up”:**
    - S3, GCS, Azure, etcd, Redis, Kafka, NATS
- **Onboarding wizard** for configuring all plugins and backends
- **Streamlit dashboard** for live result/metrics/panel
- **Provable audit mesh** (signed, tamper-evident logs)
- **Parallel/Quantum runner** (Ray, Dask, Qiskit, D-Wave, DEAP)
- **CI runs static analysis, security checks, full coverage**
- **Extensible plugin architecture:** Python, WASM, gRPC, sample plugins provided

### 🛠️ Improvements

- Parametric tests for every backend and plugin type
- All onboarding and dashboard flows documented and tested
- Security hardening, RBAC, health check, auto-rollback on failure

### 🐞 Bugfixes

- Dashboard panel rendering edge cases
- e2e onboarding/chaos edge-case handling

---

## [Older Releases]

- Pre-v1.0.0: Prototype and closed beta (2025-03 to 2025-07)

---

**Full details, migration, and upgrade notes are in docs/ and in issue tracker.**