\# Secure and Production-Ready SIEM Client Library



A robust, asynchronous, and security-focused Python client library for interacting with various Security Information and Event Management (SIEM) systems. Built for production environments, it emphasizes reliability, scalability, and strict security out-of-the-box.



---



\## ✨ Key Features



\- \*\*Multi-Client Support:\*\*

  Unified interface for multiple SIEM platforms:

  - \*\*Cloud-Native:\*\* AWS CloudWatch Logs, GCP Cloud Logging, Azure Sentinel, Azure Event Grid, Azure Service Bus

  - \*\*Generic:\*\* Splunk, Elasticsearch, Datadog



\- \*\*Security by Design:\*\*

  - \*\*Fail-Fast on Critical Errors:\*\* The system aborts startup on missing dependencies, invalid configs, or insecure settings in `PRODUCTION\\\_MODE`.

  - \*\*HSM-Backed Secrets:\*\* Enforces secure, HSM-backed secret providers (e.g., AWS KMS, Azure Key Vault) for credentials in production. Plaintext credentials are forbidden.

  - \*\*Automated Secret Scrubbing:\*\* Integrated utility (`scrub\\\_secrets`) redacts sensitive data (API keys, passwords, PII) from logs and environment variables before processing.

  - \*\*Transport Layer Security:\*\* All HTTP-based clients require HTTPS (TLS >= 1.2).



\- \*\*Reliability \& Scalability:\*\*

  - \*\*Async Architecture:\*\* Fully built on `asyncio` for high-throughput, non-blocking I/O.

  - \*\*Intelligent Batching:\*\* Splits large log payloads into efficient, API-compliant chunks.

  - \*\*Resilient Operations:\*\* Uses `tenacity` for robust retries with exponential backoff on transient errors.

  - \*\*Configurable Rate Limiting:\*\* Token bucket-based limiter prevents overloading endpoints.



\- \*\*Observability:\*\*

  - \*\*Structured Logging:\*\* All logs include `client\\\_type` and `correlation\\\_id` for traceability.

  - \*\*Production-Grade Tracing:\*\* Integrated OpenTelemetry tracing on all operations.

  - \*\*Operator Alerting:\*\* Critical events (e.g., health check failure, auth errors) trigger alerts via a configurable `alert\\\_operator`.



---



\## 🛠️ Installation



Install the core library, then the extras for your SIEM client(s):



```bash

\\# Core library

pip install siem-client-library



\\# AWS CloudWatch dependencies

pip install boto3



\\# Azure dependencies

pip install azure-eventgrid azure-servicebus azure-monitor-query azure-identity



\\# GCP dependencies

pip install google-cloud-logging google-cloud-secret-manager google-auth



\\# Splunk, Elasticsearch, Datadog

pip install aiohttp

```



---



\## ⚙️ Configuration



Configuration is via a Python dictionary or JSON file.

\*\*In production, all sensitive values must be referenced by a secret ID.\*\*



\*\*Example `config.json`:\*\*

```json

{

\&nbsp; "default\\\_timeout\\\_seconds": 15,

\&nbsp; "rate\\\_limit\\\_tps": 10,

\&nbsp; "paranoid\\\_mode": true,

\&nbsp; "secret\\\_scrub\\\_patterns": \\\[

\&nbsp;   "personal\\\_data",

\&nbsp;   "access\\\_token"

\&nbsp; ],

\&nbsp; "aws\\\_cloudwatch": {

\&nbsp;   "region\\\_name": "us-east-1",

\&nbsp;   "log\\\_group\\\_name": "sfe-audit-logs",

\&nbsp;   "log\\\_stream\\\_name": "production-stream",

\&nbsp;   "auto\\\_create\\\_log\\\_group": false,

\&nbsp;   "auto\\\_create\\\_log\\\_stream": false,

\&nbsp;   "aws\\\_credentials\\\_secret\\\_id": "sfe-aws-creds-prod-json",

\&nbsp;   "secrets\\\_providers": \\\["aws"]

\&nbsp; },

\&nbsp; "splunk": {

\&nbsp;   "url": "https://splunk.mycompany.com:8088/services/collector/event",

\&nbsp;   "token\\\_secret\\\_id": "splunk-hec-token-prod"

\&nbsp; }

}

```



> \\\*\\\*Security Note:\\\*\\\*  

> \\\_Never store secrets in plaintext or code. Use secret managers (e.g., AWS Secrets Manager, Azure Key Vault, GCP Secret Manager) in production.\\\_



---



\## 🚀 Usage Examples



All operations are \*\*asynchronous\*\* and must run inside an `asyncio` event loop.



\### 1. Running a Health Check



```python

import asyncio

import json

from siem\\\_factory import get\\\_siem\\\_client



def metrics\\\_hook(event, status, data):

\&nbsp;   print(f"\\\[METRIC] {event}.{status}: {data}")



async def run\\\_health\\\_check():

\&nbsp;   with open("config.json", 'r') as f:

\&nbsp;       config = json.load(f)

\&nbsp;   try:

\&nbsp;       async with get\\\_siem\\\_client("aws\\\_cloudwatch", config, metrics\\\_hook) as client:

\&nbsp;           is\\\_healthy, message = await client.health\\\_check()

\&nbsp;           print(f"Health Check Status: {is\\\_healthy}, Message: {message}")

\&nbsp;   except Exception as e:

\&nbsp;       print(f"Failed to run health check: {e}")



if \\\_\\\_name\\\_\\\_ == "\\\_\\\_main\\\_\\\_":

\&nbsp;   asyncio.run(run\\\_health\\\_check())

```



\### 2. Sending a Single Log



```python

import asyncio

import json

from siem\\\_factory import get\\\_siem\\\_client



async def send\\\_single\\\_log():

\&nbsp;   with open("config.json", 'r') as f:

\&nbsp;       config = json.load(f)

\&nbsp;   log\\\_entry = {

\&nbsp;       "timestamp\\\_utc": "2025-08-04T12:00:00Z",

\&nbsp;       "event\\\_type": "user\\\_login",

\&nbsp;       "message": "User 'jdoe' logged in from 1.2.3.4",

\&nbsp;       "user\\\_id": "jdoe",

\&nbsp;   }

\&nbsp;   async with get\\\_siem\\\_client("aws\\\_cloudwatch", config) as client:

\&nbsp;       success, message = await client.send\\\_log(log\\\_entry)

\&nbsp;       print(f"Log sent successfully: {success}, Message: {message}")



if \\\_\\\_name\\\_\\\_ == "\\\_\\\_main\\\_\\\_":

\&nbsp;   asyncio.run(send\\\_single\\\_log())

```



\### 3. Sending a Batch of Logs



```python

import asyncio

import json

from siem\\\_factory import get\\\_siem\\\_client



async def send\\\_batch\\\_logs():

\&nbsp;   with open("config.json", 'r') as f:

\&nbsp;       config = json.load(f)

\&nbsp;   log\\\_entries = \\\[

\&nbsp;       {"timestamp\\\_utc": "2025-08-04T12:00:00Z", "event\\\_type": "api\\\_call", "message": "API call to /data"},

\&nbsp;       {"timestamp\\\_utc": "2025-08-04T12:01:00Z", "event\\\_type": "security\\\_alert", "message": "High severity event detected"},

\&nbsp;       # ... up to 10,000+ entries

\&nbsp;   ]

\&nbsp;   async with get\\\_siem\\\_client("aws\\\_cloudwatch", config) as client:

\&nbsp;       success, message, failed\\\_logs = await client.send\\\_logs(log\\\_entries)

\&nbsp;       print(f"Batch sent: {success}, Message: {message}")

\&nbsp;       if failed\\\_logs:

\&nbsp;           print(f"Failed to send {len(failed\\\_logs)} logs.")



if \\\_\\\_name\\\_\\\_ == "\\\_\\\_main\\\_\\\_":

\&nbsp;   asyncio.run(send\\\_batch\\\_logs())

```



\### 4. Querying Logs



```python

import asyncio

import json

from siem\\\_factory import get\\\_siem\\\_client



async def query\\\_siem():

\&nbsp;   with open("config.json", 'r') as f:

\&nbsp;       config = json.load(f)

\&nbsp;   async with get\\\_siem\\\_client("aws\\\_cloudwatch", config) as client:

\&nbsp;       query = "fields @timestamp, @message | filter @message like /security\\\_alert/"

\&nbsp;       results = await client.query\\\_logs(query, time\\\_range="1h", limit=100)

\&nbsp;       print(f"Found {len(results)} matching logs:")

\&nbsp;       for log in results:

\&nbsp;           print(log)



if \\\_\\\_name\\\_\\\_ == "\\\_\\\_main\\\_\\\_":

\&nbsp;   asyncio.run(query\\\_siem())

```



---



\## 🛡️ Production CLI



A secure CLI (`siem\\\_main.py`) is provided for operators—enforces HMAC validation on config files in `PRODUCTION\\\_MODE`.



> \\\*\\\*Note:\\\*\\\*  

> Ensure the `click` library is installed to use the CLI.



---



\## 📚 Further Reading



\- See client-specific docs for advanced configuration and integration.

\- For questions or issues, please open a GitHub Issue or Pull Request.



---

