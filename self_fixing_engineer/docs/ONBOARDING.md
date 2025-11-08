# 🚀 Self-Fixing Engineer Onboarding Guide

Welcome!  
This guide walks you—from **first git clone to live demo**—through setup, health checks, plugin/backends, and troubleshooting.  
Whether you’re a developer, researcher, or a non-dev user, you’ll get up and running in minutes.

---

## 🟢 1. Clone & Install

```bash
git clone https://github.com/your-repo/self-fixing-engineer.git
cd self-fixing-engineer
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

---

## 🧙 2. Run the Onboarding Wizard

```bash
python onboard.py
```

You'll be guided step-by-step through:

- **Choosing and configuring storage/checkpoint backends**  
  (S3, GCS, Azure Blob, etcd, local FS, etc)
- **Selecting and configuring mesh/pubsub backends**  
  (Redis, Kafka, NATS, GCS PubSub, RabbitMQ, etc)
- **Selecting and configuring policy/config backends**  
  (etcd, AppMesh, Anthos, local)
- **Selecting and enabling plugin types**  
  (Python, WASM, gRPC—run native, sandboxed, cloud, or remote)
- **Live health checks**  
  Every choice is health-checked; any missing config is auto-detected and prompts you for a fix.

The wizard creates a ready-to-run config and demo plugins for all selected types.

---

## 🔌 3. Supported Plugins & Backends (Fully Wired-Up!)

- **Python Plugins:**  
  Standard entrypoints, run and health-checked natively.
- **WASM Plugins:**  
  Upload or select a `.wasm` file, set entrypoint (e.g., `run`), configure sandbox/permissions.
- **gRPC Plugins:**  
  Supply proto and endpoint, select entrypoint (e.g., `EchoService.Run`), runtime health check.
- **All Major Backends:**  
  S3, GCS, Azure Blob, etcd, Redis, Kafka, NATS, RabbitMQ, and more—storage, checkpoint, pubsub, and policy mesh.

---

## 🩺 4. Health Checks

Automatic health checks run **for every backend and plugin** during onboarding.

- ✔️ **Green Check:** backend or plugin is ready.
- ❌ **Red X:** missing config, credentials, or connection—wizard will prompt you to fix.

**To manually re-run health checks anytime:**

```bash
python onboard.py --health-check
```

---

## 🛠️ 5. Troubleshooting

| Error Message                    | What It Means                        | How To Fix                                                          |
| -------------------------------- | ------------------------------------ | ------------------------------------------------------------------- |
| S3 credentials not found         | Missing AWS creds/env                | Set `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` in `.env`/env.  |
| etcd connection refused          | etcd not running or wrong config     | Check `ETCD_HOST`/`ETCD_PORT`, start etcd, check firewall.          |
| WASM plugin failed health check  | Entrypoint missing, permissions, or error | Check manifest entrypoint/permissions, verify `.wasm` file.     |
| gRPC plugin endpoint not reachable | Bad endpoint or proto mismatch     | Double-check endpoint, check proto matches server.                  |
| Plugin permissions denied        | Plugin lacks required permissions    | Edit manifest, rerun onboarding to approve.                         |

**General Fixes:**
- Use `.env` for all sensitive config (see BACKENDS.md).
- Rerun onboarding anytime to update/repair config.
- For Docker/cloud, ensure all ports/services are reachable.
- **Still stuck?** Check the logs printed by the wizard for actionable errors.

---

## 👀 6. (Optional) Screenshot/GIF

> _[Insert a screenshot or GIF of the onboarding wizard or dashboard here]_

---

## 🧪 7. Run the Demo

After onboarding completes and all checks pass:

```bash
python main_sim_runner.py --session demo_spec --agentic --dashboard
```

- This launches agent(s), demo plugins (Python, WASM, gRPC), and live self-healing workflows.

Progress and results can be viewed in the dashboard:

```bash
streamlit run dashboard.py
```

Visit [http://localhost:8501](http://localhost:8501) for a full UI with live metrics, plugin gallery, onboarding help, and more.

---

## 📖 Need More?

- **Plugins:**  
  See [PLUGINS.md](PLUGINS.md) for advanced plugin examples, manifest structure, and troubleshooting.

- **Backends:**  
  See [BACKENDS.md](BACKENDS.md) for deep-dive backend config, cloud integration, and troubleshooting.

- **Security:**  
  [SECURITY.md](SECURITY.md)

- **Contributing:**  
  [CONTRIBUTING.md](CONTRIBUTING.md)

---

Now you’re ready.  
**From zero to live self-fixing code, in minutes!**