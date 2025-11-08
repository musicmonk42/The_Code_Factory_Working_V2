# CONTRIBUTING.md

## 🤝 How to Contribute

Thank you for considering a contribution to Self-Fixing Engineer!  
Whether you’re adding code, plugins, docs, or tests, here’s how to do it right.

---

### 🧩 Adding a New Plugin or Backend

- See [PLUGINS.md](./PLUGINS.md) for how to write a plugin (Python, WASM, gRPC) and create a correct manifest.
- See [BACKENDS.md](./BACKENDS.md) for how to add/configure a backend (S3, GCS, Azure, etcd, etc).
- Submit a PR with your plugin/backend code, manifest, and at least one test.

---

### 🧪 Running the Full Test Suite

- Run all tests (including coverage, async, and e2e):

    ```bash
    pytest
    ```

- To **skip cloud-dependent tests**, just do not set the required environment variables.  
  Tests will auto-skip if not configured (see BACKENDS.md).

---

### 📚 Contributing Docs

- Update or add to:
    - [README.md](./README.md) for new features or onboarding changes
    - [PLUGINS.md](./PLUGINS.md), [BACKENDS.md](./BACKENDS.md), [ONBOARDING.md](./ONBOARDING.md) for plugins, backends, onboarding flows
- Write in clear English. Use code blocks and tables where helpful.

---

### 🖋️ Style Guide

- Follow [PEP8](https://pep8.org/) for Python code.
- Use [Black](https://github.com/psf/black) for auto-formatting.
- Docstrings required for all public classes and functions.
- Write tests for any new logic, plugin, or backend.
- Use semantic commit messages (`feat:`, `fix:`, `docs:`, etc).

---

### 🚦 Submitting PRs

- Fork, create a branch, make your changes.
- Run all tests and lint (`pytest`, `black`, `flake8`).
- Open a PR with a clear description of your changes.
- Tag reviewers if you know who should review.

---

### 👥 Code of Conduct

All contributors must abide by our [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md).

---

Thanks for making Self-Fixing Engineer better!