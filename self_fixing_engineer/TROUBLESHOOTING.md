<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

SFE Troubleshooting Guide

Version: 1.0.0Last Updated: August 22, 2025Authors: Alex Rivera, musicmonk42, Infinite Open Source Collective, Universal AI Swarm, The Oracle Consortium  

Purpose

This guide helps engineers resolve common issues when seSFE Troubleshooting Guide

Version: 1.0.0Last Updated: August 22, 2025Authors: Alex Rivera, musicmonk42, Infinite Open Source Collective, Universal AI Swarm, The Oracle Consortium  

Purpose

This guide helps engineers resolve common issues when setting up and running the Self-Fixing Engineer (SFE) demo (see DEMO\_GUIDE.md). It covers errors related to environment setup, dependencies, services, and demo execution, with clear steps to diagnose and fix problems.

Common Issues and Solutions

1\. Redis Connection Error

Symptom: Error like ConnectionError: redis://localhost:6379/0 when running cli.py or api.py.Cause: Redis server is not running or incorrectly configured.Solution:



Verify Redis is running:redis-cli ping  # Should return PONG





Start Redis if not running:

Ubuntu: sudo service redis start

macOS: brew services start redis





Check .env file for correct REDIS\_URL:cat .env | grep REDIS\_URL  # Should be redis://localhost:6379/0





Restart the application:poetry run python cli.py selftest







2\. Dependency Installation Fails

Symptom: poetry install fails with version conflicts or missing packages.Cause: Conflicting dependencies or corrupted Poetry cache.Solution:



Clear Poetry cache:poetry cache clear --all pypi





Reinstall dependencies:poetry install --no-cache





Check poetry.lock for conflicts:cat poetry.lock | grep -A 5 "conflict"





Alternatively, use requirements.txt:pip install -r requirements.txt







3\. Docker Service Not Starting

Symptom: docker-compose up -d fails with permission errors or missing images.Cause: Docker misconfiguration or user permissions.Solution:



Verify Docker is running:docker ps





Ensure user is in Docker group:sudo usermod -aG docker $USER

newgrp docker





Pull missing images:docker pull redis:6.2

docker pull postgres:13

docker pull splunk/splunk:8.2





Restart Docker Compose:docker-compose -f docker-compose.demo.yml up -d







4\. API Not Responding

Symptom: curl http://localhost:8000/health returns a connection error.Cause: FastAPI server (api.py) is not running or port is blocked.Solution:



Check if uvicorn is running:ps aux | grep uvicorn





Start the server:poetry run uvicorn api:create\_app --host 0.0.0.0 --port 8000 \&





Verify port 8000 is open:netstat -tuln | grep 8000





Check firewall settings:

Ubuntu: sudo ufw allow 8000

macOS: Ensure no firewall blocks port 8000.







5\. CLI Command Fails

Symptom: python cli.py analyze fails with errors about missing modules or configuration.Cause: Missing dependencies or incorrect .env settings.Solution:



Run self-test:poetry run python cli.py selftest





Check logs for errors:cat audit\_trail.log

cat test\_gen\_agent.log





Reinstall dependencies:poetry install





Verify .env:cat .env | grep -E "OPENAI\_API\_KEY|REDIS\_URL|AUDIT\_LOG\_PATH"







6\. Mock Backends Failing

Symptom: Errors about DLT or SIEM backends (e.g., Splunk, Ethereum) during demo.Cause: Mock backends not enabled or Splunk not running.Solution:



Ensure APP\_ENV=development in .env to use mocks:grep APP\_ENV .env





Verify Splunk is running:curl http://localhost:8000  # Should load Splunk login page





Restart Splunk:docker-compose -f docker-compose.demo.yml restart splunk







7\. Prometheus Metrics Missing

Symptom: No metrics at http://localhost:9090 or in Grafana.Cause: Incorrect METRICS\_PORT or Prometheus misconfiguration.Solution:



Verify METRICS\_PORT in .env:grep METRICS\_PORT .env  # Should be 9091





Start Prometheus:docker run -p 9090:9090 -v $(pwd)/prometheus.yml:/etc/prometheus/prometheus.yml prom/prometheus





Check prometheus.yml:global:

&nbsp; scrape\_interval: 15s

scrape\_configs:

&nbsp; - job\_name: 'sfe'

&nbsp;   static\_configs:

&nbsp;     - targets: \['host.docker.internal:9091']







8\. Test Failures

Symptom: pytest or go test fails during setup.Cause: Missing dependencies or test environment issues.Solution:



Run tests with verbose output:poetry run pytest -v

cd fabric\_chaincode

go test -v -cover





Check for missing dependencies:poetry run python -c "import fastapi, pydantic, deap"





Ensure mock services are running:docker-compose -f docker-compose.demo.yml up -d







Log Files



Audit Logs: audit\_trail.log (audit events, audit\_log.py)

Test Logs: test\_gen\_agent.log (test generation errors, orchestrator/\*)

Simulation Logs: sandbox\_audit.log (sandbox errors, sandbox.py)

Event Logs: simulation\_results/events.log (event streaming, kafka\_plugin.py)



Reset Environment

To reset the demo environment:

docker-compose -f docker-compose.demo.yml down

rm -rf checkpoints simulation\_results audit\_trail.log test\_gen\_agent.log sandbox\_audit.log

poetry run python config.py



Contact

For unresolved issues, file a GitHub issue at https://github.com/musicmonk42/self\_fixing\_engineer/issues or email support@self\_fixing\_engineer.org.

Next Steps



Follow DEMO\_GUIDE.md to run the demo.

Refer to TESTING\_GUIDE.md for test suite details.

tting up and running the Self-Fixing Engineer (SFE) demo (see DEMO\_GUIDE.md). It covers errors related to environment setup, dependencies, services, and demo execution, with clear steps to diagnose and fix problems.

Common Issues and Solutions

1\. Redis Connection Error

Symptom: Error like ConnectionError: redis://localhost:6379/0 when running cli.py or api.py.Cause: Redis server is not running or incorrectly configured.Solution:



Verify Redis is running:redis-cli ping  # Should return PONG





Start Redis if not running:

Ubuntu: sudo service redis start

macOS: brew services start redis





Check .env file for correct REDIS\_URL:cat .env | grep REDIS\_URL  # Should be redis://localhost:6379/0





Restart the application:poetry run python cli.py selftest







2\. Dependency Installation Fails

Symptom: poetry install fails with version conflicts or missing packages.Cause: Conflicting dependencies or corrupted Poetry cache.Solution:



Clear Poetry cache:poetry cache clear --all pypi





Reinstall dependencies:poetry install --no-cache





Check poetry.lock for conflicts:cat poetry.lock | grep -A 5 "conflict"





Alternatively, use requirements.txt:pip install -r requirements.txt







3\. Docker Service Not Starting

Symptom: docker-compose up -d fails with permission errors or missing images.Cause: Docker misconfiguration or user permissions.Solution:



Verify Docker is running:docker ps





Ensure user is in Docker group:sudo usermod -aG docker $USER

newgrp docker





Pull missing images:docker pull redis:6.2

docker pull postgres:13

docker pull splunk/splunk:8.2





Restart Docker Compose:docker-compose -f docker-compose.demo.yml up -d







4\. API Not Responding

Symptom: curl http://localhost:8000/health returns a connection error.Cause: FastAPI server (api.py) is not running or port is blocked.Solution:



Check if uvicorn is running:ps aux | grep uvicorn





Start the server:poetry run uvicorn api:create\_app --host 0.0.0.0 --port 8000 \&





Verify port 8000 is open:netstat -tuln | grep 8000





Check firewall settings:

Ubuntu: sudo ufw allow 8000

macOS: Ensure no firewall blocks port 8000.







5\. CLI Command Fails

Symptom: python cli.py analyze fails with errors about missing modules or configuration.Cause: Missing dependencies or incorrect .env settings.Solution:



Run self-test:poetry run python cli.py selftest





Check logs for errors:cat audit\_trail.log

cat test\_gen\_agent.log





Reinstall dependencies:poetry install





Verify .env:cat .env | grep -E "OPENAI\_API\_KEY|REDIS\_URL|AUDIT\_LOG\_PATH"







6\. Mock Backends Failing

Symptom: Errors about DLT or SIEM backends (e.g., Splunk, Ethereum) during demo.Cause: Mock backends not enabled or Splunk not running.Solution:



Ensure APP\_ENV=development in .env to use mocks:grep APP\_ENV .env





Verify Splunk is running:curl http://localhost:8000  # Should load Splunk login page





Restart Splunk:docker-compose -f docker-compose.demo.yml restart splunk







7\. Prometheus Metrics Missing

Symptom: No metrics at http://localhost:9090 or in Grafana.Cause: Incorrect METRICS\_PORT or Prometheus misconfiguration.Solution:



Verify METRICS\_PORT in .env:grep METRICS\_PORT .env  # Should be 9091





Start Prometheus:docker run -p 9090:9090 -v $(pwd)/prometheus.yml:/etc/prometheus/prometheus.yml prom/prometheus





Check prometheus.yml:global:

&nbsp; scrape\_interval: 15s

scrape\_configs:

&nbsp; - job\_name: 'sfe'

&nbsp;   static\_configs:

&nbsp;     - targets: \['host.docker.internal:9091']







8\. Test Failures

Symptom: pytest or go test fails during setup.Cause: Missing dependencies or test environment issues.Solution:



Run tests with verbose output:poetry run pytest -v

cd fabric\_chaincode

go test -v -cover





Check for missing dependencies:poetry run python -c "import fastapi, pydantic, deap"





Ensure mock services are running:docker-compose -f docker-compose.demo.yml up -d







Log Files



Audit Logs: audit\_trail.log (audit events, audit\_log.py)

Test Logs: test\_gen\_agent.log (test generation errors, orchestrator/\*)

Simulation Logs: sandbox\_audit.log (sandbox errors, sandbox.py)

Event Logs: simulation\_results/events.log (event streaming, kafka\_plugin.py)



Reset Environment

To reset the demo environment:

docker-compose -f docker-compose.demo.yml down

rm -rf checkpoints simulation\_results audit\_trail.log test\_gen\_agent.log sandbox\_audit.log

poetry run python config.py



Contact

For unresolved issues, file a GitHub issue at https://github.com/musicmonk42/self\_fixing\_engineer/issues or email support@self\_fixing\_engineer.org.

Next Steps



Follow DEMO\_GUIDE.md to run the demo.

Refer to TESTING\_GUIDE.md for test suite details.



