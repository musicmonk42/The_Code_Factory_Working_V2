<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# ASE Code Access Configuration

- **Where/how does ASE access code?**  
  - Inputs are provided explicitly per job: uploaded archives, mounted workspace paths (e.g., `./uploads/{job_id}/`), or repository checkouts staged by CI/ops. ASE does not auto-discover repos from logs.
  - The OmniCore API accepts job payloads that include paths/references; services like `omnicore_service` then operate on those paths.

- **CLI presence?**  
  - ASE exposes a FastAPI/HTTP interface (see `server/main.py`) and internal services; there is no standalone “local CLI like Claude.” Local usage is via HTTP, or by invoking agents/services within the repo’s Python environment.

- **GitHub/network access on server?**  
  - Network/repo access depends on deployment configuration/credentials supplied by operators. By default, no implicit GitHub access is assumed; if allowed, CI/ops stages the repo before invoking ASE.

- **Determining which repo to use when handling logs/alerts**  
  - ASE relies on the job/request payload to specify the code location. Logging aggregation across pools is external; ASE will not infer the repo from a log line. The caller must provide the path/checkout that corresponds to the incident.

- **Who provides the path?**  
  - The invoking system (CI pipeline, operator, or upstream service) supplies the code path or upload location in the request. ASE works on what it is handed; it doesn’t resolve paths from monitoring data on its own.
