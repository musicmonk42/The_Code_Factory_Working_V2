\# Arbiter Learner Submodule — Practical Operations \& Developer Guide



This guide is designed to make operating and coding with the `arbiter.learner` submodule as easy as possible, even for newcomers.  

\*\*It covers: setup, configuration, running, debugging, extending, and best practices.\*\*



---



\## 1. Quick Overview



\*\*What is this?\*\*  

`arbiter.learner` is a secure, extensible Python system for learning, validating, explaining, encrypting, and auditing structured knowledge.  

It supports LLM-based explanations, unstructured fact extraction, cryptographic audit, and more.



\*\*Key Dependencies:\*\*  

\- Python 3.10+ (asyncio support)

\- Redis (cache, locks)

\- Postgres (persistent storage)

\- \[Optional] Neo4j (knowledge graph)

\- \[Optional] JIRA (bug reporting)

\- \[Optional] AWS SSM (encryption keys)

\- LLM API (OpenAI by default)



---



\## 2. Environment \& Configuration



\*\*.env Example (minimum):\*\*

```env

DATABASE\_URL=postgresql://postgres:password@localhost:5432/arbiter

REDIS\_URL=redis://localhost:6379/0

LLM\_PROVIDER=openai

LLM\_API\_KEY=sk-...

LLM\_MODEL=gpt-4o-mini

\# Optional for extra features:

NEO4J\_URL=bolt://localhost:7687

NEO4J\_USER=neo4j

NEO4J\_PASSWORD=your\_pw

JIRA\_URL=https://jira.example.com

JIRA\_USER=admin

JIRA\_PASSWORD=...

AWS\_REGION=us-west-2

ENCRYPTION\_KEY\_V1\_PATH=/arbiter/learner/encryption/key/v1

```



\*\*How config works:\*\*  

\- Most settings are read from `.env` or environment variables.

\- Encryption keys are loaded from AWS SSM (if configured) or generated in-memory.

\- Schema files go in `arbiter/schemas/` (JSON).



---



\## 3. Installation



```bash

\# Install dependencies

pip install -r requirements.txt

\# Or with poetry:

poetry install

```



Make sure Redis and Postgres are running and accessible.



---



\## 4. Running the Learner



\*\*Minimal Example:\*\*

```python

from arbiter.learner.core import Arbiter, Learner

from redis.asyncio import Redis



arbiter = Arbiter()

redis = Redis.from\_url("redis://localhost:6379/0")

learner = Learner(arbiter, redis)



import asyncio

asyncio.run(learner.start())

```



\*\*Learning a fact:\*\*

```python

result = asyncio.run(

&nbsp;   learner.learn\_new\_thing(

&nbsp;       domain="TestDomain",

&nbsp;       key="foo",

&nbsp;       value={"bar": 42},

&nbsp;       user\_id="demo\_user"

&nbsp;   )

)

print(result)

```



\*\*Batch learning, explanation, validation, etc. all use similar APIs.\*\*



---



\## 5. Observability \& Debugging



\- \*\*Logs:\*\* Structured logs via structlog (JSON). Tail your logs for errors and audit events.

\- \*\*Metrics:\*\* Prometheus metrics available; scrape endpoint if exporting (e.g. with `prometheus\_client` HTTP server).

\- \*\*Tracing:\*\* OpenTelemetry spans are created for all major operations.

\- \*\*Audit log:\*\* All operations are audited; audit trail can be verified cryptographically.



\*\*Common issues \& resolutions:\*\*

| Problem                   | Solution |

|---------------------------|----------|

| Redis unavailable         | Check connection, restart, or use fallback memory (not prod safe) |

| Postgres errors           | Check DB URL, credentials, and schema |

| LLM API failures          | Check API key, quota, or swap to a stub client for dev |

| Encryption errors         | Ensure AWS creds/SSM, or set `FALLBACK\_ENCRYPTION\_KEY` for dev |

| Schema validation fails   | Fix JSON schema in `arbiter/schemas/` |

| Feedback log lost         | For analytics, persist or export `explanation\_feedback\_log` before shutdown |



---



\## 6. Extending the Learner



\*\*a) Add New Validation Logic\*\*

```python

from arbiter.learner.validation import register\_validation\_hook



async def my\_custom\_validator(value):

&nbsp;   # Return True if valid, False if not

&nbsp;   return isinstance(value, dict) and "foo" in value



register\_validation\_hook(learner, "TestDomain", my\_custom\_validator)

```



\*\*b) Add a Fuzzy Parser\*\*

```python

from arbiter.learner.fuzzy import register\_fuzzy\_parser\_hook, FuzzyParser



class MyFuzzyParser:

&nbsp;   async def parse(self, text, context):

&nbsp;       # Return list of facts (dicts)

&nbsp;       return \[{"domain": "TestDomain", "key": "fuzzy", "value": {"parsed": text}}]



register\_fuzzy\_parser\_hook(learner, MyFuzzyParser(), priority=10)

```



\*\*c) Custom Audit/Event Hooks\*\*

```python

async def post\_learn\_hook(domain, key, value, result):

&nbsp;   print(f"Learned: {domain}:{key} -> {value}")



learner.event\_hooks\['post\_learn'].append(post\_learn\_hook)

```



---



\## 7. Maintenance \& Best Practices



\- \*\*Backup:\*\* Regularly backup Postgres, Redis, and (if enabled) audit logs.

\- \*\*Audit Verification:\*\* Use the built-in or external scripts to verify Merkle proofs/audit chain integrity.

\- \*\*Key Rotation:\*\* Periodically rotate encryption keys (production only!).

\- \*\*Schema Management:\*\* Keep JSON schemas versioned and under source control.

\- \*\*Monitor:\*\* Set up dashboards/alerts for Prometheus metrics and error logs.



---



\## 8. FAQ



\*\*Q: Can I run this without Redis/Postgres?\*\*  

A: You can stub them for dev, but not recommended for production.



\*\*Q: How do I add a new domain?\*\*  

A: Add a schema in `arbiter/schemas/`, optionally a validation hook, and start using it.



\*\*Q: How do I debug a fact that won’t learn?\*\*  

A: Check logs for validation or policy block, check schema, and ensure all keys/values are correct.



\*\*Q: Can I use a different LLM?\*\*  

A: Swap out the LLM client in config or code. Interface is pluggable.



---



\## 9. Code Structure Reference



| File               | Role                        |

|--------------------|----------------------------|

| core.py            | Main learner logic          |

| explanations.py    | LLM explanations, feedback  |

| fuzzy.py           | Fuzzy/unstructured parsing  |

| validation.py      | Schema/custom validation    |

| encryption.py      | Encryption/key mgmt         |

| audit.py           | Audit/circuit/Merkle        |

| metrics.py         | Prometheus metrics          |



---



\## 10. TL;DR "How to Operate"



1\. \*\*Set up Postgres, Redis, .env, and (optional) extras.\*\*

2\. \*\*Install dependencies.\*\*

3\. \*\*Start your code/script with Arbiter + Learner.\*\*

4\. \*\*Learn/test facts and watch logs/metrics.\*\*

5\. \*\*Add custom logic via hooks, parsers, and schemas as needed.\*\*

6\. \*\*Regularly monitor, backup, and verify audit trail.\*\*

7\. \*\*Rotate keys and update schemas as required.\*\*



---



\*\*For further help:\*\*  

\- Check docstrings and comments in each file (they are detailed and descriptive).

\- Use logs and metrics—they tell you what’s going on.

\- Don’t hesitate to troubleshoot one subsystem at a time—everything is modular.



---

