<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

Demo Guide: Building Your First Import Fix in SFE

This guide helps a coder new to SFE set up and run a simple demo for the Self-Healing Import Fixer module. We'll create a buggy Python codebase, analyze it, fix import issues, and validate the results.

As of September 10, 2025, this works with version 1.0.

Prerequisites

Follow GETTING\_STADemo Guide: Building Your First Import Fix in SFE

This guide helps a coder new to SFE set up and run a simple demo for the Self-Healing Import Fixer module. We'll create a buggy Python codebase, analyze it, fix import issues, and validate the results.

As of September 10, 2025, this works with version 1.0.

Prerequisites

Follow GETTING\_STARTED.md to set up the environment, including Python 3.10+, dependencies (requirements.txt), and Redis (optional). Ensure .env has OPENAI\_API\_KEY for AI-powered fixes.

Step 1: Create a Demo Codebase

Create a directory demo\_src/ with two files to simulate an import cycle:

demo\_src/main.py:

\# main.py

from .utils import helper  # Relative import causing cycle

def main():

&nbsp;   helper()

if \_\_name\_\_ == "\_\_main\_\_":

&nbsp;   main()



demo\_src/utils.py:

\# utils.py

from main import main  # Circular import

def helper():

&nbsp;   print("Helper function")



Add a requirements.txt with a missing dependency:

requests>=2.31.0

\# Missing: numpy (used in code but not declared)



Step 2: Run the Analysis



Command:python cli.py analyze demo\_src/ --output-format json > report.json





Expected Output (report.json):{

&nbsp; "cycles": \[\["main", "utils", "main"]],

&nbsp; "dead\_code": \[],

&nbsp; "policy\_violations": \["relative\_import\_in\_main"],

&nbsp; "dependency\_issues": \["missing: numpy"]

}





Check: Open report.json to confirm cycles and missing dependencies.



Step 3: Fix Imports



Command (interactive mode):python cli.py heal demo\_src/ --fix-cycles --interactive





Interaction: The CLI shows proposed fixes (e.g., convert relative to absolute imports, break cycle with lazy import). Type y to approve or n to skip.

Output: Fixed files in demo\_src/; backups in .backups/.

Example Fixed main.py:from demo\_src.utils import helper

def main():

&nbsp;   helper()

if \_\_name\_\_ == "\_\_main\_\_":

&nbsp;   main()







Step 4: Fix Dependencies



Command:python cli.py heal demo\_src/ --sync-reqs





Output: Updates requirements.txt to include numpy.

Check: cat demo\_src/requirements.txt shows:requests>=2.31.0

numpy>=1.26.0







Step 5: Validate Fixes



Re-run analysis:python cli.py analyze demo\_src/ --output-format json





Expected: No cycles or missing dependencies.

Run tests (if demo\_src/test\_main.py exists):pytest demo\_src/





Check logs: logs/audit.log for tamper-evident records.



Step 6: Add AI-Powered Refactoring



Command (requires OPENAI\_API\_KEY):python cli.py heal demo\_src/ --ai-refactor





Output: AI suggests refactors (e.g., extract interface to break cycle). Approve interactively.



Step 7: Extend with a Plugin



Create plugins/my\_plugin.py:from fixer\_plugins import BasePlugin

class MyPlugin(BasePlugin):

&nbsp;   async def pre\_healing(self, context):

&nbsp;       print(f"Custom pre-healing: {context}")





Register and run:python cli.py heal demo\_src/ --plugin my\_plugin







Troubleshooting Demos



No Issues Found?: Add more bugs (e.g., invalid import from nonexistent import x).

API Key Error?: Check OPENAI\_API\_KEY in .env.

Redis Fails?: Ensure Docker Redis is running: docker ps.

Validation Fails?: Check logs/audit.log or run python cli.py selftest.

Need Help?: Join Discord (link in README.md).



Next Steps



Visualize: If dashboard implemented, run python cli.py serve demo\_src/ --port 8000.

Integrate: Publish results to arbiter (see README.md).

Contribute: Add tests or plugins; see README.md Contribution Guidelines.



Success? Share your demo on Discord (link in README.md)!

RTED.md to set up the environment, including Python 3.10+, dependencies (requirements.txt), and Redis (optional). Ensure .env has OPENAI\_API\_KEY for AI-powered fixes.

Step 1: Create a Demo Codebase

Create a directory demo\_src/ with two files to simulate an import cycle:

demo\_src/main.py:

\# main.py

from .utils import helper  # Relative import causing cycle

def main():

&nbsp;   helper()

if \_\_name\_\_ == "\_\_main\_\_":

&nbsp;   main()



demo\_src/utils.py:

\# utils.py

from main import main  # Circular import

def helper():

&nbsp;   print("Helper function")



Add a requirements.txt with a missing dependency:

requests>=2.31.0

\# Missing: numpy (used in code but not declared)



Step 2: Run the Analysis



Command:python cli.py analyze demo\_src/ --output-format json > report.json





Expected Output (report.json):{

&nbsp; "cycles": \[\["main", "utils", "main"]],

&nbsp; "dead\_code": \[],

&nbsp; "policy\_violations": \["relative\_import\_in\_main"],

&nbsp; "dependency\_issues": \["missing: numpy"]

}





Check: Open report.json to confirm cycles and missing dependencies.



Step 3: Fix Imports



Command (interactive mode):python cli.py heal demo\_src/ --fix-cycles --interactive





Interaction: The CLI shows proposed fixes (e.g., convert relative to absolute imports, break cycle with lazy import). Type y to approve or n to skip.

Output: Fixed files in demo\_src/; backups in .backups/.

Example Fixed main.py:from demo\_src.utils import helper

def main():

&nbsp;   helper()

if \_\_name\_\_ == "\_\_main\_\_":

&nbsp;   main()







Step 4: Fix Dependencies



Command:python cli.py heal demo\_src/ --sync-reqs





Output: Updates requirements.txt to include numpy.

Check: cat demo\_src/requirements.txt shows:requests>=2.31.0

numpy>=1.26.0







Step 5: Validate Fixes



Re-run analysis:python cli.py analyze demo\_src/ --output-format json





Expected: No cycles or missing dependencies.

Run tests (if demo\_src/test\_main.py exists):pytest demo\_src/





Check logs: logs/audit.log for tamper-evident records.



Step 6: Add AI-Powered Refactoring



Command (requires OPENAI\_API\_KEY):python cli.py heal demo\_src/ --ai-refactor





Output: AI suggests refactors (e.g., extract interface to break cycle). Approve interactively.



Step 7: Extend with a Plugin



Create plugins/my\_plugin.py:from fixer\_plugins import BasePlugin

class MyPlugin(BasePlugin):

&nbsp;   async def pre\_healing(self, context):

&nbsp;       print(f"Custom pre-healing: {context}")





Register and run:python cli.py heal demo\_src/ --plugin my\_plugin







Troubleshooting Demos



No Issues Found?: Add more bugs (e.g., invalid import from nonexistent import x).

API Key Error?: Check OPENAI\_API\_KEY in .env.

Redis Fails?: Ensure Docker Redis is running: docker ps.

Validation Fails?: Check logs/audit.log or run python cli.py selftest.

Need Help?: Join Discord (link in README.md).



Next Steps



Visualize: If dashboard implemented, run python cli.py serve demo\_src/ --port 8000.

Integrate: Publish results to arbiter (see README.md).

Contribute: Add tests or plugins; see README.md Contribution Guidelines.



Success? Share your demo on Discord (link in README.md)!



