<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

\# Patent Disclosure for `runner\_utils.py`



---



\## 1. \*\*Module Title and Scope\*\*



\*\*Runner Utility Module for Secure Redaction, Encryption, Digital Signing, Provenance, and Atomic File Operations in Automated Software Systems\*\*



This module is a core utility component in the `runner` system, designed to provide pluggable, extensible, and audit-grade data protection and provenance for software automation and testing frameworks.



---



\## 2. \*\*Inventive Concepts and Key Features\*\*

\# Patent Disclosure for `runner\_utils.py`



---



\## 1. \*\*Module Title and Scope\*\*



\*\*Runner Utility Module for Secure Redaction, Encryption, Digital Signing, Provenance, and Atomic File Operations in Automated Software Systems\*\*



This module is a core utility component in the `runner` system, designed to provide pluggable, extensible, and audit-grade data protection and provenance for software automation and testing frameworks.



---



\## 2. \*\*Inventive Concepts and Key Features\*\*



\### 2.1. \*\*Pluggable Registries\*\*



\- \*\*Redactors\*\* (`REDACTORS`): Map of redaction methods (e.g., regex, AI/NLP).

\- \*\*Encryptors/Decryptors\*\* (`ENCRYPTORS`, `DECRYPTORS`): Map algorithm string to encrypt/decrypt function.

\- \*\*Signers\*\* (`SIGNERS`): Map signature algorithm string to function (HMAC, RSA, ECDSA, etc).

\- \*\*Provenance Generators\*\* (`PROVENANCE\_GENERATORS`): Map provenance generation strategies.



\*\*Inventive Step:\*\* All sensitive operations are registry-driven, allowing runtime or project-level extension and override, which is not common in legacy utility modules.



\### 2.2. \*\*Multi-Method Data Redaction\*\*



\- \*\*`PII\_PATTERNS`\*\*: Comprehensive regexes for emails, SSNs, credit cards, IPs, tokens, phone numbers, UUIDs, etc (expandable).

\- \*\*Recursive Redaction\*\*: Handles nested structures (`str`, `dict`, `list`) with full depth.

\- \*\*AI/NLP Hook\*\*: Support for ML-driven entity recognition as a drop-in redactor.

\- \*\*Custom Patterns\*\*: Accepts user/project-supplied regexes for additional or override redaction.

\- \*\*Default and Fallbacks\*\*: System falls back to regex if AI/NLP redactor is unavailable.



\### 2.3. \*\*Encryption and Decryption\*\*



\- \*\*Symmetric Encryption\*\*: Fernet/AES (CBC) supported, with base64 output.

\- \*\*Key Policy\*\*: Refuses to operate without key in production; generates one with strong warning in development.

\- \*\*Atomicity\*\*: No partial writes; all encrypt/decrypt operations are safe and error-propagating.

\- \*\*Extensibility\*\*: Registry allows custom cryptosystems (e.g., GCP KMS, AWS KMS, custom hardware module).



\### 2.4. \*\*Digital Signing\*\*



\- \*\*Multi-Algorithm\*\*: HMAC (with pluggable hash), RSA-PSS (prehashed), ECDSA (if library available).

\- \*\*Canonicalization\*\*: Dicts are JSON-canonicalized before signing, ensuring deterministic signatures.

\- \*\*Key Handling\*\*: Accepts bytes, PEM, or library objects as appropriate.

\- \*\*Output\*\*: All signatures are base64 strings, ready for API transport and audit logs.



\### 2.5. \*\*Provenance Generation\*\*



\- \*\*Hash Chain\*\*: Every provenance record contains hash of its data and the previous record (blockchain-like, but purpose-built).

\- \*\*Signature\*\*: Optionally signs both the content and the record itself (algorithm of choice).

\- \*\*Document Provenance\*\*: Handles content-based provenance for documentation/artifacts, including doc-specific content hash and signature.

\- \*\*Extensibility\*\*: Registry supports new provenance strategies (e.g., Merkle trees, ZK proofs, org-specific).



\### 2.6. \*\*Atomic File Operations with Backup/Recovery\*\*



\- \*\*`save\_files\_to\_output()`\*\*: Atomically writes files using temp file + `os.replace`.

\- \*\*Backup\*\*: Creates a timestamped backup `output\_dir.backup\_<timestamp>` if files exist.

\- \*\*Recovery\*\*: On failure, removes partial data and restores from backup; raises structured error.

\- \*\*Per-File Provenance\*\*: Each file write can generate provenance including content hash and signature.



\### 2.7. \*\*Structured Error Handling\*\*



\- \*\*All failures\*\*: Raise structured (Pydantic-style) error objects with error codes, human-readable detail, file path, and cause.

\- \*\*Error Codes\*\*: Central registry (`ERROR\_CODE\_REGISTRY`) for all code paths, ensuring uniqueness and traceability.



---



\## 3. \*\*Compliance and Security Design\*\*



\- \*\*Redaction\*\*: Prevents accidental leakage of PII/secrets in logs, outputs, and provenance.

\- \*\*Key Management\*\*: Fails fast without keys in prod; never uses hardcoded keys.

\- \*\*Auditability\*\*: All provenance and signatures are tamper-evident and traceable.

\- \*\*Extensible\*\*: Designed for future regulatory/standard compliance (e.g., GDPR, SOC2, HIPAA, PCI).



---



\## 4. \*\*Novelty and Prior Art Distinction\*\*



\- No known open-source or commercial runner/test utility offers \*\*all\*\*:

&nbsp; - Pluggable multi-algorithm redaction, encryption, and signing

&nbsp; - Hash-chained, signed provenance for code, tests, docs, and artifacts

&nbsp; - Atomic file write with backup/recovery and provenance

&nbsp; - Structured, registry-driven error propagation

&nbsp; - AI/ML-ready redaction hooks

&nbsp; - Extensible pluggable registries for all above



---



\## 5. \*\*API/Interface Summary\*\*



\### 5.1. \*\*Redaction\*\*



```python

redact\_secrets(data, method='regex', patterns=None) -> Any

\# Recursively redacts sensitive data in nested structures.

```



\### 5.2. \*\*Encryption\*\*



```python

encrypt\_log(log\_data\_str, algorithm='fernet', key=None) -> str

\# Encrypts string, returns base64 ciphertext

decrypt\_log(encrypted\_str, algorithm='fernet', key) -> str

\# Decrypts base64 ciphertext

```



\### 5.3. \*\*Signing\*\*



```python

sign\_data(data, key, algorithm='hmac', hash\_algo='sha256') -> str

\# Signs data (str/dict/bytes), returns base64 signature

```



\### 5.4. \*\*Provenance\*\*



```python

generate\_provenance(entry\_data, prev\_hash, history\_chain, method='basic', signing\_key=None, signing\_algo='hmac') -> dict

\# Generates a hash-chained, optionally signed provenance record

```



\### 5.5. \*\*Atomic File Saving\*\*



```python

save\_files\_to\_output(files, output\_dir, backup=True, recover=True, provenance\_metadata=None, signing\_key=None, signing\_algo='hmac') -> None

\# Writes files atomically with backup/recovery and provenance

```



---



\## 6. \*\*Security/Compliance Notes\*\*



\- \*\*Production/Dev Key Policy\*\*: In production, encryption/signing will not proceed without explicit key. In dev, a random key is generated with a warning, never persisted.

\- \*\*Provenance Chain\*\*: Ensures non-repudiation, tamper-evidence, and auditability for all critical file and artifact writes.

\- \*\*Redaction\*\*: Only as strong as supplied patterns/AI logic; registry allows for organization-specific PII/secret definitions.



---



\## 7. \*\*Extensibility and Customization\*\*



\- \*\*Plug-in Functions\*\*: All major functions (`REDACTORS`, `ENCRYPTORS`, `SIGNERS`, `PROVENANCE\_GENERATORS`) are registry-controlled.

\- \*\*Custom Redaction\*\*: Org/project can register NLP, ML, or external redactors.

\- \*\*Custom Crypto\*\*: Supports hardware modules, KMS, cloud encryption by extending ENCRYPTORS/DECRYPTORS.

\- \*\*Custom Provenance\*\*: Org/project can implement Merkle-chain, ZK-proof, or multi-party provenance.



---



\## 8. \*\*Potential Patent Claims\*\*



1\. \*\*A utility module for automated software systems providing recursive, pluggable redaction (regex and AI/NLP), multi-algorithm encryption/decryption, digital signing, and hash-chained provenance for any data/artifact.\*\*

2\. \*\*A method of atomic file writing with backup/recovery and per-file signed provenance, as described above.\*\*

3\. \*\*A system wherein all sensitive operations (redaction, encryption, signing, provenance) are registry-driven and extensible at runtime or project scope.\*\*

4\. \*\*A utility that fails safe in production when encryption keys are missing, with secure error handling and warnings in development.\*\*

5\. \*\*A provenance system supporting both workflow and artifact/document-level hash chaining, signing, and audit logging.\*\*



---



\## 9. \*\*Attachments and Supporting Evidence\*\*



\- \*\*Full Source Code\*\*: See `runner\_utils.py`

\- \*\*References to Usage\*\*: See calls to `redact\_secrets`, `encrypt\_log`, `generate\_provenance`, `save\_files\_to\_output` in core runner modules.

\- \*\*Tests\*\*: See test suite for atomic file save, redaction, encryption, and provenance chain integrity.

\- \*\*Error Handling\*\*: All exceptions use structured error classes, with error code registry.

\- \*\*Security Review\*\*: See documentation/comments on key management and redaction patterns.



---



\### 2.1. \*\*Pluggable Registries\*\*



\- \*\*Redactors\*\* (`REDACTORS`): Map of redaction methods (e.g., regex, AI/NLP).

\- \*\*Encryptors/Decryptors\*\* (`ENCRYPTORS`, `DECRYPTORS`): Map algorithm string to encrypt/decrypt function.

\- \*\*Signers\*\* (`SIGNERS`): Map signature algorithm string to function (HMAC, RSA, ECDSA, etc).

\- \*\*Provenance Generators\*\* (`PROVENANCE\_GENERATORS`): Map provenance generation strategies.



\*\*Inventive Step:\*\* All sensitive operations are registry-driven, allowing runtime or project-level extension and override, which is not common in legacy utility modules.



\### 2.2. \*\*Multi-Method Data Redaction\*\*



\- \*\*`PII\_PATTERNS`\*\*: Comprehensive regexes for emails, SSNs, credit cards, IPs, tokens, phone numbers, UUIDs, etc (expandable).

\- \*\*Recursive Redaction\*\*: Handles nested structures (`str`, `dict`, `list`) with full depth.

\- \*\*AI/NLP Hook\*\*: Support for ML-driven entity recognition as a drop-in redactor.

\- \*\*Custom Patterns\*\*: Accepts user/project-supplied regexes for additional or override redaction.

\- \*\*Default and Fallbacks\*\*: System falls back to regex if AI/NLP redactor is unavailable.



\### 2.3. \*\*Encryption and Decryption\*\*



\- \*\*Symmetric Encryption\*\*: Fernet/AES (CBC) supported, with base64 output.

\- \*\*Key Policy\*\*: Refuses to operate without key in production; generates one with strong warning in development.

\- \*\*Atomicity\*\*: No partial writes; all encrypt/decrypt operations are safe and error-propagating.

\- \*\*Extensibility\*\*: Registry allows custom cryptosystems (e.g., GCP KMS, AWS KMS, custom hardware module).



\### 2.4. \*\*Digital Signing\*\*



\- \*\*Multi-Algorithm\*\*: HMAC (with pluggable hash), RSA-PSS (prehashed), ECDSA (if library available).

\- \*\*Canonicalization\*\*: Dicts are JSON-canonicalized before signing, ensuring deterministic signatures.

\- \*\*Key Handling\*\*: Accepts bytes, PEM, or library objects as appropriate.

\- \*\*Output\*\*: All signatures are base64 strings, ready for API transport and audit logs.



\### 2.5. \*\*Provenance Generation\*\*



\- \*\*Hash Chain\*\*: Every provenance record contains hash of its data and the previous record (blockchain-like, but purpose-built).

\- \*\*Signature\*\*: Optionally signs both the content and the record itself (algorithm of choice).

\- \*\*Document Provenance\*\*: Handles content-based provenance for documentation/artifacts, including doc-specific content hash and signature.

\- \*\*Extensibility\*\*: Registry supports new provenance strategies (e.g., Merkle trees, ZK proofs, org-specific).



\### 2.6. \*\*Atomic File Operations with Backup/Recovery\*\*



\- \*\*`save\_files\_to\_output()`\*\*: Atomically writes files using temp file + `os.replace`.

\- \*\*Backup\*\*: Creates a timestamped backup `output\_dir.backup\_<timestamp>` if files exist.

\- \*\*Recovery\*\*: On failure, removes partial data and restores from backup; raises structured error.

\- \*\*Per-File Provenance\*\*: Each file write can generate provenance including content hash and signature.



\### 2.7. \*\*Structured Error Handling\*\*



\- \*\*All failures\*\*: Raise structured (Pydantic-style) error objects with error codes, human-readable detail, file path, and cause.

\- \*\*Error Codes\*\*: Central registry (`ERROR\_CODE\_REGISTRY`) for all code paths, ensuring uniqueness and traceability.



---



\## 3. \*\*Compliance and Security Design\*\*



\- \*\*Redaction\*\*: Prevents accidental leakage of PII/secrets in logs, outputs, and provenance.

\- \*\*Key Management\*\*: Fails fast without keys in prod; never uses hardcoded keys.

\- \*\*Auditability\*\*: All provenance and signatures are tamper-evident and traceable.

\- \*\*Extensible\*\*: Designed for future regulatory/standard compliance (e.g., GDPR, SOC2, HIPAA, PCI).



---



\## 4. \*\*Novelty and Prior Art Distinction\*\*



\- No known open-source or commercial runner/test utility offers \*\*all\*\*:

&nbsp; - Pluggable multi-algorithm redaction, encryption, and signing

&nbsp; - Hash-chained, signed provenance for code, tests, docs, and artifacts

&nbsp; - Atomic file write with backup/recovery and provenance

&nbsp; - Structured, registry-driven error propagation

&nbsp; - AI/ML-ready redaction hooks

&nbsp; - Extensible pluggable registries for all above



---



\## 5. \*\*API/Interface Summary\*\*



\### 5.1. \*\*Redaction\*\*



```python

redact\_secrets(data, method='regex', patterns=None) -> Any

\# Recursively redacts sensitive data in nested structures.

```



\### 5.2. \*\*Encryption\*\*



```python

encrypt\_log(log\_data\_str, algorithm='fernet', key=None) -> str

\# Encrypts string, returns base64 ciphertext

decrypt\_log(encrypted\_str, algorithm='fernet', key) -> str

\# Decrypts base64 ciphertext

```



\### 5.3. \*\*Signing\*\*



```python

sign\_data(data, key, algorithm='hmac', hash\_algo='sha256') -> str

\# Signs data (str/dict/bytes), returns base64 signature

```



\### 5.4. \*\*Provenance\*\*



```python

generate\_provenance(entry\_data, prev\_hash, history\_chain, method='basic', signing\_key=None, signing\_algo='hmac') -> dict

\# Generates a hash-chained, optionally signed provenance record

```



\### 5.5. \*\*Atomic File Saving\*\*



```python

save\_files\_to\_output(files, output\_dir, backup=True, recover=True, provenance\_metadata=None, signing\_key=None, signing\_algo='hmac') -> None

\# Writes files atomically with backup/recovery and provenance

```



---



\## 6. \*\*Security/Compliance Notes\*\*



\- \*\*Production/Dev Key Policy\*\*: In production, encryption/signing will not proceed without explicit key. In dev, a random key is generated with a warning, never persisted.

\- \*\*Provenance Chain\*\*: Ensures non-repudiation, tamper-evidence, and auditability for all critical file and artifact writes.

\- \*\*Redaction\*\*: Only as strong as supplied patterns/AI logic; registry allows for organization-specific PII/secret definitions.



---



\## 7. \*\*Extensibility and Customization\*\*



\- \*\*Plug-in Functions\*\*: All major functions (`REDACTORS`, `ENCRYPTORS`, `SIGNERS`, `PROVENANCE\_GENERATORS`) are registry-controlled.

\- \*\*Custom Redaction\*\*: Org/project can register NLP, ML, or external redactors.

\- \*\*Custom Crypto\*\*: Supports hardware modules, KMS, cloud encryption by extending ENCRYPTORS/DECRYPTORS.

\- \*\*Custom Provenance\*\*: Org/project can implement Merkle-chain, ZK-proof, or multi-party provenance.



---



\## 8. \*\*Potential Patent Claims\*\*



1\. \*\*A utility module for automated software systems providing recursive, pluggable redaction (regex and AI/NLP), multi-algorithm encryption/decryption, digital signing, and hash-chained provenance for any data/artifact.\*\*

2\. \*\*A method of atomic file writing with backup/recovery and per-file signed provenance, as described above.\*\*

3\. \*\*A system wherein all sensitive operations (redaction, encryption, signing, provenance) are registry-driven and extensible at runtime or project scope.\*\*

4\. \*\*A utility that fails safe in production when encryption keys are missing, with secure error handling and warnings in development.\*\*

5\. \*\*A provenance system supporting both workflow and artifact/document-level hash chaining, signing, and audit logging.\*\*



---



\## 9. \*\*Attachments and Supporting Evidence\*\*



\- \*\*Full Source Code\*\*: See `runner\_utils.py`

\- \*\*References to Usage\*\*: See calls to `redact\_secrets`, `encrypt\_log`, `generate\_provenance`, `save\_files\_to\_output` in core runner modules.

\- \*\*Tests\*\*: See test suite for atomic file save, redaction, encryption, and provenance chain integrity.

\- \*\*Error Handling\*\*: All exceptions use structured error classes, with error code registry.

\- \*\*Security Review\*\*: See documentation/comments on key management and redaction patterns.



---



