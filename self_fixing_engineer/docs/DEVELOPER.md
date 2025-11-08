\# Self-Fixing Engineer Developer Guide



\## Purpose

Enable rapid, safe extension of the platform by adding new plugins, agents, simulation environments, or intent capture interfaces.

\# Self-Fixing Engineer Developer Guide



\## Purpose

Enable rapid, safe extension of the platform by adding new plugins, agents, simulation environments, or intent capture interfaces.



---



\## Architecture Overview



```

┌────────────┐         ┌────────────────┐

│   User     │<------->│ Intent Capture │

└────────────┘         └───────┬────────┘

&nbsp;        ▲                     │

&nbsp;        │      ┌──────────────▼─────────────┐

&nbsp;        └─────►│         Arbiter           │

&nbsp;               └───────┬───────────┬───────┘

&nbsp;                       │           │

&nbsp;       ┌───────────────▼───┐   ┌───▼───────────────┐

&nbsp;       │   Arena           │   │  Plugins          │

&nbsp;       │ (arena.py)        │   │ (plugins.py)      │

&nbsp;       └────┬──────────────┘   └────┬──────────────┘

&nbsp;            │                        │

&nbsp;   ┌────────▼──────┐        ┌────────▼────────────┐

&nbsp;   │   Agents      │        │   Knowledge         │

&nbsp;   │ (crew\_manager │        │ (learner, k\_graph)  │

&nbsp;   └────┬──────────┘        └────────┬────────────┘

&nbsp;        │                             │

&nbsp;   ┌────▼───────────┐          ┌──────▼────────────┐

&nbsp;   │ Simulation     │          │ Policy/Audit      │

&nbsp;   │ (evolution,    │          │ (policy, audit)   │

&nbsp;   │  code\_health)  │          └────────┬──────────┘

&nbsp;   └────────────────┘                   │

&nbsp;                                        ▼

&nbsp;                                ┌──────────────┐

&nbsp;                                │   DLT/SIEM   │

&nbsp;                                └──────────────┘

```



\*\*Key Concepts:\*\*

\- \*\*Intent Capture:\*\* CLI, API, Web UI entry points.

\- \*\*Arbiter:\*\* Core orchestrator (decision, routing, policy, simulation).

\- \*\*Agents:\*\* Modular workers/skills (crew).

\- \*\*Plugins:\*\* Extensible behaviors (sandboxed).

\- \*\*Knowledge:\*\* Dynamic, updatable graph.

\- \*\*Simulation:\*\* Training \& test envs.

\- \*\*Policy/Audit:\*\* Guardrails and observability.



---



\## Extension Points



\### 1. Creating Agents



\*\*Files:\*\* `crew\_config.yaml`, `crew\_manager.py`



\*\*How:\*\*



```yaml

\# crew\_config.yaml

agents:

&nbsp; - name: custom\_agent

&nbsp;   role: custom\_role

&nbsp;   manifest: custom\_agent

&nbsp;   skills\_ref: \[custom\_skill]

```



\- Add agent class in `crew\_manager.py`

\- Register manifest/skills



---



\### 2. Adding Plugins



\*\*Files:\*\* `plugins.py`, `plugin\_config.py`



\- \*\*Sandbox:\*\* All plugins must be whitelisted in `SANDBOXED\_PLUGINS` mapping.



\*\*Plugin example:\*\*



```python

\# plugins/custom\_plugin.py

async def custom\_plugin(arbiter, \*args):

&nbsp;   return {"result": "Custom action executed"}

```



\*\*Registration:\*\*



```python

\# plugin\_config.py

SANDBOXED\_PLUGINS\["custom\_plugin"] = "plugins.custom\_plugin.custom\_plugin"

```



---



\### 3. Knowledge Management



\*\*Files:\*\* `knowledge\_loader.py`, `learner.py`, `knowledge\_graph.py`



\- Add new loader modules or graph nodes.

\- Override update/refresh logic for streaming, RL, or third-party data.



\*\*Example:\*\*  

Add new node type to `knowledge\_graph.py`  

Register loader in `knowledge\_loader.py`



---



\### 4. Intent Capture Interfaces



\*\*Files:\*\* `cli.py`, `api.py`, `web.py`



\- \*\*CLI:\*\* Add new commands/args in `cli.py`

\- \*\*API:\*\* Extend `api.py` with FastAPI endpoints

\- \*\*Web UI:\*\* Add event handlers, websocket integration, or custom widgets in `web.py`



---



\### 5. Simulation Environments



\*\*Files:\*\* `evolution.py`, `code\_health\_env.py`, `main\_sim\_runner.py`



\*\*How:\*\*



```python

\# simulation/custom\_env.py

from evolution import BaseEnvironment

class CustomEnvironment(BaseEnvironment):

&nbsp;   def step(self, action):

&nbsp;       # Custom logic here

&nbsp;       return super().step(action)

```



\- Register in simulation registry in `main\_sim\_runner.py` or config



---



\### 6. Writing \& Running Tests



\*\*Example Test:\*\*



```python

\# arbiter/tests/test\_custom.py

import pytest



@pytest.mark.asyncio

async def test\_custom\_plugin(test\_agent):

&nbsp;   result = await test\_agent.run\_task({"type": "custom\_plugin"})

&nbsp;   assert result\["result"] == "Custom action executed"

```



---



\## Relevant Files \& Roles



| File                                    | Purpose                               |

|------------------------------------------|---------------------------------------|

| crew\_manager.py                         | Agent orchestration/management        |

| crew\_config.yaml                        | Agent/skill configuration             |

| plugins.py, plugin\_config.py             | Plugin registry \& definitions         |

| knowledge\_loader.py                      | Knowledge ingestion                   |

| learner.py, knowledge\_graph.py           | Learning and knowledge management     |

| cli.py, api.py, web.py                   | Intent capture/entrypoints            |

| evolution.py, code\_health\_env.py, main\_sim\_runner.py | Simulation                    |

| test\_arbiter\_knowledge.py, tests/        | Testing                               |

| policy.py, audit\_log.py                  | Policy enforcement/audit trail        |

| event\_bus.py                            | Event/message passing                 |



---



\## Plugin Development Example



```python

\# plugins/custom\_plugin.py

async def custom\_plugin(arbiter, \*args):

&nbsp;   return {"result": "Custom action executed"}

```



```python

\# plugin\_config.py

SANDBOXED\_PLUGINS\["custom\_plugin"] = "plugins.custom\_plugin.custom\_plugin"

```



---



\## Agent Creation Example



```yaml

\# crew\_config.yaml

agents:

&nbsp; - name: custom\_agent

&nbsp;   role: custom\_role

&nbsp;   manifest: custom\_agent

&nbsp;   skills\_ref: \[custom\_skill]

```



---



\## Simulation Environment Extension Example



```python

\# simulation/custom\_env.py

from evolution import BaseEnvironment

class CustomEnvironment(BaseEnvironment):

&nbsp;   def step(self, action):

&nbsp;       # Custom logic here

&nbsp;       return super().step(action)

```



---



\## Test Writing Example



```python

\# arbiter/tests/test\_custom.py

import pytest



@pytest.mark.asyncio

async def test\_custom\_plugin(test\_agent):

&nbsp;   result = await test\_agent.run\_task({"type": "custom\_plugin"})

&nbsp;   assert result\["result"] == "Custom action executed"

```



---



\## CI/CD and Contribution



\- Add new tests in `tests/` folder; use pytest for async support.

\- All plugins and simulation environments must be registered in their config files.

\- Run linting, type checks, and test suite before PR.



---



\## Further Reading \& API Docs



\- \*\*README.md:\*\* High-level usage and project philosophy

\- \*\*CONTRIBUTING.md:\*\* Contribution workflow

\- \*\*API docs:\*\* FastAPI/OpenAPI schema for REST extension



---



\## Need help?



Open an issue, or check `/docs` in your running instance for auto-generated OpenAPI schema and plugin/agent registry browser.



---



\## Architecture Overview



```

┌────────────┐         ┌────────────────┐

│   User     │<------->│ Intent Capture │

└────────────┘         └───────┬────────┘

&nbsp;        ▲                     │

&nbsp;        │      ┌──────────────▼─────────────┐

&nbsp;        └─────►│         Arbiter           │

&nbsp;               └───────┬───────────┬───────┘

&nbsp;                       │           │

&nbsp;       ┌───────────────▼───┐   ┌───▼───────────────┐

&nbsp;       │   Arena           │   │  Plugins          │

&nbsp;       │ (arena.py)        │   │ (plugins.py)      │

&nbsp;       └────┬──────────────┘   └────┬──────────────┘

&nbsp;            │                        │

&nbsp;   ┌────────▼──────┐        ┌────────▼────────────┐

&nbsp;   │   Agents      │        │   Knowledge         │

&nbsp;   │ (crew\_manager │        │ (learner, k\_graph)  │

&nbsp;   └────┬──────────┘        └────────┬────────────┘

&nbsp;        │                             │

&nbsp;   ┌────▼───────────┐          ┌──────▼────────────┐

&nbsp;   │ Simulation     │          │ Policy/Audit      │

&nbsp;   │ (evolution,    │          │ (policy, audit)   │

&nbsp;   │  code\_health)  │          └────────┬──────────┘

&nbsp;   └────────────────┘                   │

&nbsp;                                        ▼

&nbsp;                                ┌──────────────┐

&nbsp;                                │   DLT/SIEM   │

&nbsp;                                └──────────────┘

```



\*\*Key Concepts:\*\*

\- \*\*Intent Capture:\*\* CLI, API, Web UI entry points.

\- \*\*Arbiter:\*\* Core orchestrator (decision, routing, policy, simulation).

\- \*\*Agents:\*\* Modular workers/skills (crew).

\- \*\*Plugins:\*\* Extensible behaviors (sandboxed).

\- \*\*Knowledge:\*\* Dynamic, updatable graph.

\- \*\*Simulation:\*\* Training \& test envs.

\- \*\*Policy/Audit:\*\* Guardrails and observability.



---



\## Extension Points



\### 1. Creating Agents



\*\*Files:\*\* `crew\_config.yaml`, `crew\_manager.py`



\*\*How:\*\*



```yaml

\# crew\_config.yaml

agents:

&nbsp; - name: custom\_agent

&nbsp;   role: custom\_role

&nbsp;   manifest: custom\_agent

&nbsp;   skills\_ref: \[custom\_skill]

```



\- Add agent class in `crew\_manager.py`

\- Register manifest/skills



---



\### 2. Adding Plugins



\*\*Files:\*\* `plugins.py`, `plugin\_config.py`



\- \*\*Sandbox:\*\* All plugins must be whitelisted in `SANDBOXED\_PLUGINS` mapping.



\*\*Plugin example:\*\*



```python

\# plugins/custom\_plugin.py

async def custom\_plugin(arbiter, \*args):

&nbsp;   return {"result": "Custom action executed"}

```



\*\*Registration:\*\*



```python

\# plugin\_config.py

SANDBOXED\_PLUGINS\["custom\_plugin"] = "plugins.custom\_plugin.custom\_plugin"

```



---



\### 3. Knowledge Management



\*\*Files:\*\* `knowledge\_loader.py`, `learner.py`, `knowledge\_graph.py`



\- Add new loader modules or graph nodes.

\- Override update/refresh logic for streaming, RL, or third-party data.



\*\*Example:\*\*  

Add new node type to `knowledge\_graph.py`  

Register loader in `knowledge\_loader.py`



---



\### 4. Intent Capture Interfaces



\*\*Files:\*\* `cli.py`, `api.py`, `web.py`



\- \*\*CLI:\*\* Add new commands/args in `cli.py`

\- \*\*API:\*\* Extend `api.py` with FastAPI endpoints

\- \*\*Web UI:\*\* Add event handlers, websocket integration, or custom widgets in `web.py`



---



\### 5. Simulation Environments



\*\*Files:\*\* `evolution.py`, `code\_health\_env.py`, `main\_sim\_runner.py`



\*\*How:\*\*



```python

\# simulation/custom\_env.py

from evolution import BaseEnvironment

class CustomEnvironment(BaseEnvironment):

&nbsp;   def step(self, action):

&nbsp;       # Custom logic here

&nbsp;       return super().step(action)

```



\- Register in simulation registry in `main\_sim\_runner.py` or config



---



\### 6. Writing \& Running Tests



\*\*Example Test:\*\*



```python

\# arbiter/tests/test\_custom.py

import pytest



@pytest.mark.asyncio

async def test\_custom\_plugin(test\_agent):

&nbsp;   result = await test\_agent.run\_task({"type": "custom\_plugin"})

&nbsp;   assert result\["result"] == "Custom action executed"

```



---



\## Relevant Files \& Roles



| File                                    | Purpose                               |

|------------------------------------------|---------------------------------------|

| crew\_manager.py                         | Agent orchestration/management        |

| crew\_config.yaml                        | Agent/skill configuration             |

| plugins.py, plugin\_config.py             | Plugin registry \& definitions         |

| knowledge\_loader.py                      | Knowledge ingestion                   |

| learner.py, knowledge\_graph.py           | Learning and knowledge management     |

| cli.py, api.py, web.py                   | Intent capture/entrypoints            |

| evolution.py, code\_health\_env.py, main\_sim\_runner.py | Simulation                    |

| test\_arbiter\_knowledge.py, tests/        | Testing                               |

| policy.py, audit\_log.py                  | Policy enforcement/audit trail        |

| event\_bus.py                            | Event/message passing                 |



---



\## Plugin Development Example



```python

\# plugins/custom\_plugin.py

async def custom\_plugin(arbiter, \*args):

&nbsp;   return {"result": "Custom action executed"}

```



```python

\# plugin\_config.py

SANDBOXED\_PLUGINS\["custom\_plugin"] = "plugins.custom\_plugin.custom\_plugin"

```



---



\## Agent Creation Example



```yaml

\# crew\_config.yaml

agents:

&nbsp; - name: custom\_agent

&nbsp;   role: custom\_role

&nbsp;   manifest: custom\_agent

&nbsp;   skills\_ref: \[custom\_skill]

```



---



\## Simulation Environment Extension Example



```python

\# simulation/custom\_env.py

from evolution import BaseEnvironment

class CustomEnvironment(BaseEnvironment):

&nbsp;   def step(self, action):

&nbsp;       # Custom logic here

&nbsp;       return super().step(action)

```



---



\## Test Writing Example



```python

\# arbiter/tests/test\_custom.py

import pytest



@pytest.mark.asyncio

async def test\_custom\_plugin(test\_agent):

&nbsp;   result = await test\_agent.run\_task({"type": "custom\_plugin"})

&nbsp;   assert result\["result"] == "Custom action executed"

```



---



\## CI/CD and Contribution



\- Add new tests in `tests/` folder; use pytest for async support.

\- All plugins and simulation environments must be registered in their config files.

\- Run linting, type checks, and test suite before PR.



---



\## Further Reading \& API Docs



\- \*\*README.md:\*\* High-level usage and project philosophy

\- \*\*CONTRIBUTING.md:\*\* Contribution workflow

\- \*\*API docs:\*\* FastAPI/OpenAPI schema for REST extension



---



\## Need help?



Open an issue, or check `/docs` in your running instance for auto-generated OpenAPI schema and plugin/agent registry browser.



