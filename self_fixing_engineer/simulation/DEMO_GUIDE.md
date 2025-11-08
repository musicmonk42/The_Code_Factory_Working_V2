\# Demo Guide: Building Your First Simulation in SFE



This guide helps you set up and run a simple demo if you're new. We'll simulate a basic "code evolution" scenario using the module's agentic features.



As of September 10, 2025, this works with v2.0.



\## Prerequisites

Follow `GETTING\_STARTED.md` for setup. Ensure Docker is running for sandboxing.



\## Step 1: Create a Demo Config

Create `demo\_config.yaml`:

```yaml

id: demo\_sim\_1

duration: 5  # seconds

should\_fail: false

This simulates a short task.

Step 2: Run the Demo



CLI: python -m simulation.core --config demo\_config.yaml --mode single.

Expected Output:

textResults for Job 1 Status: COMPLETED (local)

{

&nbsp; "status": "completed",

&nbsp; "id": "demo\_sim\_1",

&nbsp; "output": "result\_demo\_sim\_1",

&nbsp; "duration": 5

}



Check simulation\_results/ for JSON file.



Step 3: Add Complexity (Parallel Execution)

Update demo\_config.yaml for multiple tasks:

yamltasks: 3

duration: 2

Run: python -m simulation.parallel --backend asyncio --tasks 3.



Output: Results for 3 parallel sims.



Step 4: Visualize



Run: streamlit run dashboard.py.

Load results from simulation\_results/.

See charts/tables for durations, statuses.



Step 5: Extend with Plugins



Add a custom plugin in plugins/my\_plugin.py:

pythonclass MyPlugin:

&nbsp;   async def run(self, target, params):

&nbsp;       return True, "Custom success", {"output": "demo"}



Register in registry.py: Add to registry.

Run with plugin: Modify config to include it.



Troubleshooting Demos



Dependency Error: pip install -r requirements.txt again.

Sandbox Fail: Ensure Docker: docker ps.

No Output: Check logs in logs/.

Audit Issues: Verify sandbox\_audit.log.



For advanced demos (quantum, DLT): See README.md Integration Guides.

Success? Share on Discord (link in README.md)!

