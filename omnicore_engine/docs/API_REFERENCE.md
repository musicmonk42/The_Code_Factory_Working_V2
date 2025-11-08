# OmniCore Omega Pro Engine API Reference

## FastAPI Endpoints

- **/health** (GET): System health status, no auth required.
- **/metrics** (GET): Prometheus metrics.
- **/fix-imports** (POST): AI import fixer, file upload, OAuth2 JWT required.
- **/admin/plugins/install** (GET): Install plugin via marketplace, OAuth2 JWT required.
- **/admin/audit/export-proof-bundle** (GET): Export audit proofs, OAuth2 JWT required.
- **/admin/generate-test-cases** (GET): Generate test cases, OAuth2 JWT required.

See Swagger at `/docs`.

## CLI Commands

- `serve`: Start FastAPI server
- `list-plugins`: List all plugins
- `fix-imports path/to/file.py`: Fix imports in Python file
- `metrics-status`: Show Prometheus metrics
- `audit-query --user_id user123`: Query audit logs
- `repl`: Interactive shell
- `simulate --engine montecarlo --data '{"input": [1,2,3]}'`: Run simulation

## Authentication

- **API:** OAuth2 JWT via `/token` endpoint (not shown)
- **CLI:** Set `USER_ID` for PolicyEngine checks

## Error Codes

- **200:** Success
- **400:** Bad request (e.g., invalid input)
- **403:** Unauthorized (e.g., invalid JWT)
- **500:** Internal server/database/AI error

**See:**  
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md)  
- [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md)  
- [CONFIGURATION.md](CONFIGURATION.md)