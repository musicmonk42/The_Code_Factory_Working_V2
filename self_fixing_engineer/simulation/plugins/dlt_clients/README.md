\# DLT Clients Package



\## Overview



The \*\*DLT Clients Package\*\* provides a suite of production-ready, asynchronous Python clients for interacting with Distributed Ledger Technologies (DLTs) and off-chain storage solutions. Built for reliability, security, and observability, it is ideal for enterprise applications requiring robust and auditable blockchain connectivity.



---



\## ✨ Core Features



\- \*\*Modular Architecture\*\*  

&nbsp; Flexible factory pattern enables easy integration of new DLT and storage clients.



\- \*\*Asynchronous Operations\*\*  

&nbsp; All operations leverage `asyncio` for high-performance, non-blocking I/O.



\- \*\*Production Readiness:\*\*  

&nbsp; - \*\*Robust Error Handling:\*\* Custom exception hierarchy (`DLTClientAuthError`, `DLTClientTransactionError`, etc.).

&nbsp; - \*\*Circuit Breaker:\*\* Prevents cascading failures during outages.

&nbsp; - \*\*Automated Retries:\*\* Configurable exponential backoff with jitter.

&nbsp; - \*\*Observability:\*\* Prometheus metrics \& OpenTelemetry tracing.

&nbsp; - \*\*Auditing:\*\* Secure `AuditManager` signs all critical events using HMAC for forensic integrity.

&nbsp; - \*\*Secure Config:\*\* Enforces use of dedicated secrets managers in production.

&nbsp; - \*\*Graceful Dependency Management:\*\* Optional dependencies handled gracefully—core functions remain stable if a client’s dependencies are missing.



---



\## Supported Clients



\### DLT Clients



| Client                 | Protocol/Tech             | Extra Dependency         |

|------------------------|---------------------------|-------------------------|

| \*\*dlt\_corda\_clients\*\*  | R3 Corda (REST API)       | `corda-rest`            |

| \*\*dlt\_evm\_clients\*\*    | Ethereum/EVM, web3.py     | `web3`                  |

| \*\*dlt\_fabric\_clients\*\* | Hyperledger Fabric, hfc   | `hfc.fabric`            |

| \*\*dlt\_quorum\_clients\*\* | Quorum + Tessera          | `web3`, `tessera-sdk`   |

| \*\*dlt\_simple\_clients\*\* | In-memory simulator       | None                    |



\### Off-Chain Storage Clients



| Client                  | Service           | Extra Dependency            |

|-------------------------|-------------------|----------------------------|

| \*\*S3OffChainClient\*\*    | Amazon S3         | `boto3`                    |

| \*\*GcsOffChainClient\*\*   | Google Cloud      | `google-cloud-storage`     |

| \*\*AzureBlobOffChainClient\*\* | Azure Blob    | `azure-storage-blob`       |

| \*\*IPFSClient\*\*          | IPFS node         | `ipfshttpclient`           |

| \*\*InMemoryOffChainClient\*\* | In-memory      | None                       |



---



\## 🚀 Getting Started



\### 1. Installation



Install the core package:



```bash

pip install -e .

```



To use a specific DLT or storage backend, install its dependencies (see tables above).



\### 2. Configuration



All clients are configured via a \*\*JSON\*\* or \*\*YAML\*\* file.



\*\*JSON Example:\*\*



```json

{

&nbsp; "dlt\_type": "evm",

&nbsp; "off\_chain\_storage\_type": "s3",

&nbsp; "evm": {

&nbsp;   "rpc\_url": "https://<your\_evm\_node\_url>",

&nbsp;   "chain\_id": 1,

&nbsp;   "contract\_address": "0x...",

&nbsp;   "contract\_abi\_path": "/path/to/abi.json"

&nbsp; },

&nbsp; "s3": {

&nbsp;   "bucket\_name": "my-off-chain-bucket"

&nbsp; }

}

```



\*\*YAML Example:\*\*



```yaml

dlt\_type: evm

off\_chain\_storage\_type: s3

evm:

&nbsp; rpc\_url: "https://<your\_evm\_node\_url>"

&nbsp; chain\_id: 1

&nbsp; contract\_address: "0x..."

&nbsp; contract\_abi\_path: "/path/to/abi.json"

s3:

&nbsp; bucket\_name: "my-off-chain-bucket"

```



> \*\*Security Best Practice:\*\*  

> \*\*Never\*\* store secrets (API keys, private keys, connection strings) in plaintext or in code. Always use a secure secrets manager (AWS Secrets Manager, Azure Key Vault, GCP Secret Manager) in production.



---



\### 3. Usage



\#### Command-Line Interface (CLI)



The package provides a CLI for common operations.



\*\*Health Check\*\*



```bash

python -m simulation.plugins.dlt\_clients.dlt\_main health-check \\

&nbsp;   --dlt-type <client\_type> \\

&nbsp;   --config-file /path/to/config.json

```



\*\*Write a Checkpoint\*\*



```bash

python -m simulation.plugins.dlt\_clients.dlt\_main write-checkpoint \\

&nbsp;   --dlt-type <client\_type> \\

&nbsp;   --config-file /path/to/config.json \\

&nbsp;   --checkpoint-name "my-first-checkpoint" \\

&nbsp;   --hash "0x123..." \\

&nbsp;   --payload-file /path/to/payload.bin

```



---



\## 🏢 Production Deployment Checklist



\- Set the `PRODUCTION\_MODE` environment variable to `true`.

\- Store all sensitive credentials in a dedicated secrets manager.

\- Monitor Prometheus metrics for performance and error tracking.

\- Regularly verify audit logs with the `AuditManager` to ensure forensic integrity.



---



\## 📁 Project Structure



```

dlt\_base.py         # Base classes, exception hierarchy, core utilities

dlt\_factory.py      # Factory for creating DLT and off-chain client instances

dlt\_main.py         # CLI entry point

dlt\_clients/        # All specific DLT and off-chain client implementations

```



---



\## 🤝 Contributing



Contributions are welcome! Please:



\- Use \*\*Pydantic\*\* for config validation

\- Use \*\*asyncio\*\* for all async operations

\- Add unit tests with \*\*pytest\*\* and \*\*pytest-mock\*\*

\- Document all new clients and features

\- Ensure metrics and audit logging for all new DLT operations



---



\## 📚 Documentation



\- See client-specific docs for backend requirements and advanced configuration.

\- For questions or issues, please open a GitHub Issue.



---

