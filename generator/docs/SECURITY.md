# Security Policy

The **AI README-to-App Code Generator** prioritizes security and compliance to deliver safe, reliable, and trustworthy deployment configuration generation. This document outlines our security posture, features, supported versions, and the process for responsible vulnerability disclosure as of **July 28, 2025**.

---

## 🛡️ Security Features

- **Prompt Scrubbing:**  
  All components (e.g., `deploy_llm_call.py`, `deploy_prompt.py`) aggressively remove sensitive data (API keys, PII) from prompts using advanced regex patterns.

- **Security Scanning:**  
  Integrates [Trivy](https://github.com/aquasecurity/trivy) for automated vulnerability scanning of generated configs (`deploy_response_handler.py`, `deploy_validator.py`).

- **Compliance Tagging:**  
  All outputs are tagged with GDPR, CCPA, and HIPAA metadata for regulatory compliance (`file_utils.py`).

- **Audit Logging:**  
  Tamper-evident logs are generated, secured with cryptographic signatures (`audit_crypto.py`), enabling trustworthy audits and forensics.

- **Encryption:**  
  Supports file encryption using Fernet and AES standards (`security_utils.py`) for at-rest security.

---

## 📦 Supported Versions

| Version   | Supported Until   |
|-----------|------------------|
| 1.0.0     | Active (July 2025) |
| < 1.0.0   | Unsupported      |

---

## 🕵️‍♂️ Reporting a Vulnerability

If you discover a security vulnerability, please **report it responsibly**:

1. **Contact Us:**  
   Email [security@x.ai](mailto:security@x.ai) with:
   - Description of the vulnerability  
   - Steps to reproduce  
   - Impact assessment  
   - Your contact information

2. **Response Time:**  
   - Acknowledgment within **48 hours**  
   - Resolution timeline within **7 days**

3. **Confidentiality:**  
   - Please do not publicly disclose vulnerabilities until we have resolved them.

---

## 🛠 Security Updates

- Critical vulnerabilities are patched promptly.
- Monitor [`CHANGELOG.md`](CHANGELOG.md) for security-related updates.
- Subscribe to the [X community](https://x.ai/community) for announcements.

---

## ✅ Best Practices for Users

- **Secure API Keys:**  
  Store keys (e.g., `GROK_API_KEY`, `OPENAI_API_KEY`) in environment variables or a secure vault; never commit them to source control.

- **Local LLM Option:**  
  Use `local_provider.py` with [Ollama](https://ollama.com) for fully offline operation, minimizing external risk.

- **Robust Validation:**  
  Enable Trivy and Hadolint for comprehensive config security and linting.

- **Observability:**  
  Routinely monitor logs and metrics for suspicious or anomalous activity.

---

We are committed to maintaining a secure and robust platform, and we appreciate your help in keeping it safe for all users!