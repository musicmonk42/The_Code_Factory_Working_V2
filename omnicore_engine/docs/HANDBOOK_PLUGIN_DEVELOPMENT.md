<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

\# OmniCore Omega Pro Engine: Plugin Developer Handbook



\## 1. What is a Plugin?



A plugin is a Python function (sync or async) in `PLUGIN\_DIR`, registered with `@plugin`. Plugins extend the engine for tasks like code fixing, simulation, or analysis.



---



\## 2. Writing Your First Plugin



1\. \*\*Create a file\*\*: `plugins/my\_plugin.py`



2\. \*\*Paste this skeleton:\*\*

&nbsp;  ```python

&nbsp;  from omnicore\_engine.plugin\_registry import plugin, PlugInKind



&nbsp;  @plugin(

&nbsp;      kind=PlugInKind.FIX,

&nbsp;      name="my\_plugin",

&nbsp;      version="1.0.0",

&nbsp;      description="A simple plugin that echoes input.",

&nbsp;      params\_schema={"input": {"type": "string"}},

&nbsp;      safe=True

&nbsp;  )

&nbsp;  def my\_plugin(input: str) -> dict:

&nbsp;      return {"output": input}

&nbsp;  ```



3\. \*\*Check it loaded:\*\*

&nbsp;  ```sh

&nbsp;  python -m omnicore\_engine.cli list-plugins

&nbsp;  # Should see my\_plugin in the output

&nbsp;  ```



---



\## 3. Best Practices



\- Use `async def` for I/O bound tasks.

\- Always define `params\_schema` for input/output validation.

\- Add clear docstrings and descriptions.

\- Use `safe=True` for plugins with untrusted code.

\- Handle errors with clear exceptions.



---



\## 4. Testing Plugins



\- Create a test: `tests/test\_my\_plugin.py`

&nbsp; ```python

&nbsp; import pytest

&nbsp; from omnicore\_engine.plugin\_registry import PLUGIN\_REGISTRY



&nbsp; @pytest.mark.asyncio

&nbsp; async def test\_my\_plugin():

&nbsp;     result = await PLUGIN\_REGISTRY.execute("FIX", "my\_plugin", {"input": "hello"})

&nbsp;     assert result == {"output": "hello"}

&nbsp; ```

\- Run with:

&nbsp; ```sh

&nbsp; pytest tests/test\_my\_plugin.py --asyncio-mode=auto

&nbsp; ```



---



\## 5. Hot Reloading



\- The engine auto-reloads plugins on file change.

\- Watch logs for reload events or errors.



---



\## 6. Advanced



\- Use `PluginMarketplace` to install plugins from remote sources.

\- For dependencies between plugins, declare them in the plugin decorator.

\- For rollback, use `PluginRollbackHandler`.



---



\## 7. Need Help?



\- See \[PLUGINS.md](PLUGINS.md) for more details and advanced features.

\- Ask in the repository issue tracker.



---



Happy Plugin Building!

