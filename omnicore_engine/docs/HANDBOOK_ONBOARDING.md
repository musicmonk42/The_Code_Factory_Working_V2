\# OmniCore Omega Pro Engine: Developer Onboarding Handbook



\## Welcome



Welcome to OmniCore Omega Pro! This guide will get you productive fast—covering setup, first run, and essential commands.



---



\## 1. Quick Setup



1\. \*\*Clone the repo\*\*  

&nbsp;  ```sh

&nbsp;  git clone <repository-url>

&nbsp;  cd Code\_Factory/omnicore\_engine

&nbsp;  ```



2\. \*\*Create virtualenv and install dependencies\*\*  

&nbsp;  ```sh

&nbsp;  python -m venv venv

&nbsp;  source venv/bin/activate      # Windows: venv\\Scripts\\activate

&nbsp;  pip install -r requirements.txt

&nbsp;  ```



3\. \*\*Configure your environment\*\*  

&nbsp;  - Copy `.env.example` to `.env` or create one:

&nbsp;    ```

&nbsp;    DATABASE\_URL=sqlite+aiosqlite:///omnicore.db

&nbsp;    PLUGIN\_DIR=./plugins

&nbsp;    LOG\_LEVEL=DEBUG

&nbsp;    JWT\_SECRET=dev-secret

&nbsp;    USER\_ID=devuser

&nbsp;    ```



4\. \*\*Initialize the database\*\*  

&nbsp;  ```sh

&nbsp;  alembic upgrade head

&nbsp;  ```



---



\## 2. First Run



\- \*\*Start the Engine:\*\*  

&nbsp; ```sh

&nbsp; python -m omnicore\_engine.cli serve

&nbsp; ```

&nbsp; - API: \[http://localhost:8000](http://localhost:8000)

&nbsp; - Swagger UI: \[http://localhost:8000/docs](http://localhost:8000/docs)



\- \*\*Try the CLI:\*\*  

&nbsp; ```sh

&nbsp; python -m omnicore\_engine.cli list-plugins

&nbsp; python -m omnicore\_engine.cli repl

&nbsp; ```



---



\## 3. Directory Structure (What’s Where)



\- `omnicore\_engine/` – Core engine code

\- `database/` – DB interface and ORM models

\- `message\_bus/` – Async sharded message bus

\- `plugin\_registry.py` – Plugin loading, install, hot-reload

\- `plugin\_event\_handler.py` – Watches `PLUGIN\_DIR` for changes

\- `metrics.py` – Prometheus metrics

\- `tests/` – All tests



---



\## 4. Useful Scripts



\- \*\*Run all tests:\*\*  

&nbsp; ```sh

&nbsp; pytest tests/ --asyncio-mode=auto --cov=omnicore\_engine --cov-report=html

&nbsp; ```

\- \*\*Format code:\*\*  

&nbsp; ```sh

&nbsp; black .

&nbsp; flake8 .

&nbsp; mypy omnicore\_engine

&nbsp; ```



---



\## 5. Common Problems



\- \*\*Can’t connect to DB:\*\*  

&nbsp; - Check `DATABASE\_URL` in `.env`

&nbsp; - Make sure your SQLite file exists or PostgreSQL is running



\- \*\*Plugin not loading:\*\*  

&nbsp; - Check the `PLUGIN\_DIR` value and file syntax

&nbsp; - Watch logs for errors (`tail -f omnicore.log`)



\- \*\*API 403s:\*\*  

&nbsp; - Set `JWT\_SECRET` in `.env`

&nbsp; - Use a valid token for endpoints



See \[TROUBLESHOOTING.md](TROUBLESHOOTING.md) for more.



---



\## 6. Next Steps



\- Try adding a plugin (see \[PLUGINS.md](PLUGINS.md))

\- Read \[DEVELOPER\_GUIDE.md](DEVELOPER\_GUIDE.md) for extending the engine

\- Explore the \[API docs](http://localhost:8000/docs)

\- Ask questions via the repository issue tracker



Happy hacking!

