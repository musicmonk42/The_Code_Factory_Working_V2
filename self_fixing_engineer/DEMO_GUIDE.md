<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

Self-Fixing Engineer (SFE) Demo Guide

Version: 1.0.0Last Updated: August 22, 2025Authors: Alex Rivera, musicmonk42, Infinite Open Source Collective, Universal AI Swarm, The Oracle ConsortiumPurpose: This guide provides a step-by-step process for an engineer to set up and run a demo of the SFE platform, showcasing its AI-driven DevOps automation capabilities.

Overview

The Self-Fixing Engineer (SFE) platform is an AI-driven DevOps automation framework that autonomously analyzes, tests, fixes, and optimizes software systems. This demo guide walks you through setting up a local environment to demonstrate SFE’s key features:



Arbiter: Orchestrates workflows (arbiter.py).

Test Generation: Creates tests (gen\_agent/\*, orchestrator/\*).

Simulation: Runs tests in a sandbox (sandbox.py, dashboard.py).

Self-Healing Import Fixer: Resolves imports and dependencies (fixer\_ai.py, fixer\_dep.py).

Refactor Agent: Improves code structure (refactor\_agent.yaml).

Guardrails: Enforces compliance (audit\_log.py, compliance\_mapper.py).

DLT Clients: Logs audits to blockchain (dlt\_evm\_clients.py).

SIEM Clients: Publishes logs (siem\_aws\_clients.py).

Plugins: Streams events/alerts (kafka\_plugin.py, pagerduty\_plugin.py).

Mesh: Manages events (event\_bus.py).

Agent Orchestration: Coordinates agents (crew\_manager.py, agent\_core.py).

Envs: Optimizes code health (code\_health\_env.py, evolution.py).

Contracts: Manages blockchain checkpoints (checkpoint\_chaincode.go, CheckpointContract.sol).

Configs: Applies settings (config.json).

CI/CD: Simulates deployment (ci.yml).



The demo uses a sample Python codebase with intentional issues (e.g., broken imports) to showcase SFE’s self-fixing capabilities in ~30 minutes.

Prerequisites

Hardware Requirements



CPU: 4 cores (8 recommended)

RAM: 8GB (16GB recommended)

Disk: 20GB free space

OS: Ubuntu 20.04+ or macOS 12+ (Windows with WSL2 supported)



Software Requirements



Python: 3.10.11 (or 3.10+)

Go: 1.18+ (for checkpoint\_chaincode.go)

Node.js: 16+ (for JavaScript test generation)

Docker: 20.10+ (for mock services and sandbox)

Docker Compose: 1.29+

Redis: 6.2+ (for event bus and caching)

PostgreSQL: 13+ (optional, for requirements management)

Git: For cloning the repository

Curl: For API testing

Hardhat/Foundry: For deploying CheckpointContract.sol (optional)



Dependencies

Install the following tools globally:

pip install --user pipx

pipx install poetry

sudo apt-get install -y redis-server docker.io docker-compose nodejs npm golang-go curl



Setup Instructions

Step 1: Clone the Repository

Clone the SFE repository and navigate to the project directory:

git clone https://github.com/musicmonk42/self\_fixing\_engineer.git

cd self\_fixing\_engineer



Step 2: Create a Sample Codebase

Create a sample Python codebase with intentional issues for the demo:

mkdir demo\_codebase

cat <<EOL > demo\_codebase/broken\_script.py

\# Sample script with broken imports and dependencies

import nonexistent\_module

from missing\_package import some\_function



def main():

&nbsp;   print(some\_function())

&nbsp;   nonexistent\_module.process()

EOL

cat <<EOL > demo\_codebase/requirements.txt

\# Intentionally incorrect dependency

missing\_package==99.9.9

EOL



This codebase includes:



A broken import (nonexistent\_module).

A missing dependency (missing\_package).

Simple logic to demonstrate test generation and fixing.



Step 3: Set Up Environment Variables

Create an .env file with mock credentials for the demo:

cp .env.example .env

cat <<EOL > .env

APP\_ENV=development

OPENAI\_API\_KEY=sk-demo-key-for-testing-only

REDIS\_URL=redis://localhost:6379/0

AWS\_ACCESS\_KEY\_ID=AKIAIOSFODNN7EXAMPLE

AWS\_SECRET\_ACCESS\_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY

AWS\_REGION=us-east-1

SENTRY\_DSN=https://example.sentry.io/123

PINECONE\_API\_KEY=demo-pinecone-key

AUDIT\_LOG\_PATH=./audit\_trail.log

CREW\_CONFIG\_PATH=./agent\_orchestration/crew\_config.yaml

CHECKPOINT\_FS\_DIR=./checkpoints

METRICS\_PORT=9091

EOL



Note: Replace OPENAI\_API\_KEY and PINECONE\_API\_KEY with valid keys if available. For the demo, mock keys suffice as SFE uses mocked backends (SimpleDLTClient, siem\_generic\_clients.py).

Step 4: Install Python Dependencies

Use Poetry to install Python dependencies:

poetry install



If requirements.txt is used instead:

pip install -r requirements.txt



Required dependencies include:



fastapi, uvicorn, pydantic, prometheus-client, deap, tenacity, aiohttp, aioredis, cryptography, pyjwt, bleach, sentry-sdk, langchain, pytest, ruff.



Step 5: Set Up Docker Services

Create a docker-compose.demo.yml file to run mock services (Redis, PostgreSQL, Splunk):

cat <<EOL > docker-compose.demo.yml

version: '3.8'

services:

&nbsp; redis:

&nbsp;   image: redis:6.2

&nbsp;   ports:

&nbsp;     - "6379:6379"

&nbsp;   volumes:

&nbsp;     - redis\_data:/data

&nbsp; postgres:

&nbsp;   image: postgres:13

&nbsp;   environment:

&nbsp;     POSTGRES\_USER: sfe\_user

&nbsp;     POSTGRES\_PASSWORD: sfe\_password

&nbsp;     POSTGRES\_DB: sfe\_db

&nbsp;   ports:

&nbsp;     - "5432:5432"

&nbsp;   volumes:

&nbsp;     - postgres\_data:/var/lib/postgresql/data

&nbsp; splunk:

&nbsp;   image: splunk/splunk:8.2

&nbsp;   environment:

&nbsp;     SPLUNK\_START\_ARGS: --accept-license

&nbsp;     SPLUNK\_PASSWORD: sfe\_splunk\_password

&nbsp;   ports:

&nbsp;     - "8000:8000"

&nbsp;     - "8088:8088"

&nbsp;   volumes:

&nbsp;     - splunk\_data:/opt/splunk

volumes:

&nbsp; redis\_data:

&nbsp; postgres\_data:

&nbsp; splunk\_data:

EOL



Start the services:

docker-compose -f docker-compose.demo.yml up -d



Verify services are running:

docker ps



Step 6: Build Go Chaincode

Build the Hyperledger Fabric chaincode for checkpointing:

cd fabric\_chaincode

go build checkpoint\_chaincode.go

cd ..



Note: For the demo, the chaincode will use a mock DLT client (SimpleDLTClient in dlt\_simple\_clients.py). A full Fabric network setup is optional and covered in Step 9 (optional).

Step 7: Deploy Smart Contract (Optional)

For EVM checkpointing, deploy CheckpointContract.sol using Hardhat:

cd contracts

npx hardhat init

cat <<EOL > hardhat.config.js

require("@nomicfoundation/hardhat-toolbox");

module.exports = {

&nbsp; solidity: "0.8.21",

&nbsp; networks: {

&nbsp;   localhost: {

&nbsp;     url: "http://127.0.0.1:8545"

&nbsp;   }

&nbsp; }

};

EOL

npx hardhat run scripts/deploy.js --network localhost



Note: For the demo, skip this step as SFE uses a mock DLT client. See Step 9 for full blockchain setup.

Step 8: Configure the Demo

Update config.json to use the file-based checkpoint backend:

cat <<EOL > configs/config.json

{

&nbsp; "project\_type": "demo\_safe\_mode",

&nbsp; "plugins\_dir": "plugins",

&nbsp; "results\_dir": "simulation\_results",

&nbsp; "notification\_backend": {

&nbsp;   "type": "local",

&nbsp;   "url": "local://"

&nbsp; },

&nbsp; "checkpoint\_backend": {

&nbsp;   "type": "fs",

&nbsp;   "dir": "./checkpoints"

&nbsp; },

&nbsp; "environment\_variables": {

&nbsp;   "MESH\_BACKEND\_URL": "local://",

&nbsp;   "CHECKPOINT\_BACKEND\_TYPE": "fs",

&nbsp;   "CHECKPOINT\_FS\_DIR": "./checkpoints"

&nbsp; },

&nbsp; "generated\_with": {

&nbsp;   "wizard\_version": "1.0.0",

&nbsp;   "python\_version": "3.10.11",

&nbsp;   "timestamp": "2025-08-22T13:39:05Z"

&nbsp; }

}

EOL



Ensure the checkpoints directory exists:

mkdir -p checkpoints



Step 9: Optional Blockchain Setup

For a full blockchain demo (optional, requires more setup):



Set Up Hyperledger Fabric:Follow the Fabric test network guide:

curl -sSL https://bit.ly/2ysbOFE | bash -s

cd fabric-samples/test-network

./network.sh up createChannel

./network.sh deployCC -ccn checkpoint -ccp ../self\_fixing\_engineer/fabric\_chaincode -ccl go





Set Up Ethereum Node:Run a local Ethereum node with Hardhat:

cd contracts

npx hardhat node

npx hardhat run scripts/deploy.js --network localhost



Update .env with the deployed contract address:

echo "EVM\_CONTRACT\_ADDRESS=0xYourContractAddress" >> .env







Note: For the demo, mock DLT clients suffice, so this step is optional.

Running the Demo

Step 10: Start the SFE Platform



Initialize Configuration:

poetry run python config.py



This validates and loads config.json, setting up the environment.



Start the FastAPI Server:

poetry run uvicorn api:create\_app --host 0.0.0.0 --port 8000 \&



Verify the API is running:

curl http://localhost:8000/health





Start the Web App:

poetry run streamlit run web\_app.py \&



Access at http://localhost:8501 to view the dashboard.



Run the CLI:Open a new terminal and start the interactive CLI:

poetry run python cli.py







Step 11: Execute the Demo Workflow

Follow these steps to demonstrate SFE’s end-to-end capabilities using the demo\_codebase:



Analyze the Codebase:Run the analysis to detect issues (e.g., broken imports):

poetry run python cli.py analyze demo\_codebase



Expected Output: A report (codebase\_report.markdown) detailing issues like nonexistent\_module and missing\_package.



Generate Tests:Create tests for the codebase:

poetry run python cli.py generate-tests demo\_codebase



Expected Output: Test files generated in demo\_codebase/tests (via gen\_plugins.py).



Run Tests in Sandbox:Execute tests in a secure sandbox:

poetry run python cli.py run-sandbox demo\_codebase



Expected Output: Simulation results in simulation\_results (via sandbox.py, visualized in dashboard.py).



Fix Imports and Dependencies:Apply self-healing fixes:

poetry run python cli.py heal demo\_codebase



Expected Output: Updated demo\_codebase/broken\_script.py (imports fixed by fixer\_ai.py) and requirements.txt (dependencies updated by fixer\_dep.py).



Apply Refactoring:Refactor the codebase:

poetry run python cli.py refactor demo\_codebase



Expected Output: Improved code structure (via refactor\_agent.yaml).



Enforce Compliance:Check compliance with NIST/GDPR:

poetry run python cli.py compliance-check demo\_codebase



Expected Output: Compliance report (via compliance\_mapper.py) and audit logs (audit\_trail.log).



Log to Blockchain and SIEM:Log actions to a mock blockchain and Splunk:

poetry run python cli.py log-audit demo\_codebase



Expected Output: Audit entries in audit\_trail.log (via audit\_log.py) and Splunk (via siem\_plugin.py).



Stream Events and Alerts:Send events to mock Kafka and PagerDuty:

poetry run python cli.py stream-events demo\_codebase



Expected Output: Events logged in simulation\_results/events.log (via kafka\_plugin.py, pagerduty\_plugin.py).



Optimize Code Health:Run RL-based optimization:

poetry run python cli.py optimize demo\_codebase



Expected Output: Optimization suggestions (via code\_health\_env.py, evolution.py).



Save Checkpoints:Save a checkpoint to the file system:

poetry run python cli.py save-checkpoint demo\_codebase



Expected Output: Checkpoint in checkpoints/ (via checkpoint\_manager.py, mock DLT in dlt\_simple\_clients.py).



Simulate Deployment:Simulate CI/CD deployment:

poetry run python cli.py deploy demo\_codebase



Expected Output: Deployment simulation log (via ci.yml).



View Results in Web App:Open http://localhost:8501 to view:



Codebase analysis reports.

Test results and simulation dashboards.

Compliance and audit logs.

Event streams and optimization suggestions.







Step 12: Monitor Metrics



Start a Prometheus server (if not already running):

docker run -p 9090:9090 -v $(pwd)/prometheus.yml:/etc/prometheus/prometheus.yml prom/prometheus



Create prometheus.yml:

global:

&nbsp; scrape\_interval: 15s

scrape\_configs:

&nbsp; - job\_name: 'sfe'

&nbsp;   static\_configs:

&nbsp;     - targets: \['host.docker.internal:9091']





Access Prometheus at http://localhost:9090 to view metrics (e.g., policy\_decisions\_total, session\_save\_attempts\_total).



Set up Grafana for visualization:

docker run -p 3000:3000 grafana/grafana



Access http://localhost:3000, add Prometheus as a data source, and create dashboards for SFE metrics.





Step 13: Shut Down

Stop all services:

docker-compose -f docker-compose.demo.yml down

pkill -f uvicorn

pkill -f streamlit



Demo Highlights

The demo showcases:



Arbiter: Orchestrates the workflow, coordinating all modules (arbiter.py).

Test Generation: Creates tests for broken\_script.py (gen\_plugins.py).

Simulation: Runs tests in a sandbox, visualizes results (sandbox.py, dashboard.py).

Self-Healing: Fixes imports and dependencies (fixer\_ai.py, fixer\_dep.py).

Refactor Agent: Improves code structure (refactor\_agent.yaml).

Guardrails: Enforces NIST compliance, logs audits (compliance\_mapper.py, audit\_log.py).

DLT/SIEM Clients: Logs to mock blockchain and Splunk (dlt\_simple\_clients.py, siem\_plugin.py).

Plugins: Streams events to mock Kafka/PagerDuty (kafka\_plugin.py, pagerduty\_plugin.py).

Mesh: Manages events (event\_bus.py).

Agent Orchestration: Coordinates agents (crew\_manager.py).

Envs: Optimizes code health (code\_health\_env.py).

Contracts: Saves mock checkpoints (checkpoint\_manager.py).

Configs: Applies demo settings (config.json).

CI/CD: Simulates deployment (ci.yml).



Troubleshooting



Redis Connection Error:

Check Redis is running: redis-cli ping (should return PONG).

Verify REDIS\_URL in .env.





API Not Responding:

Ensure uvicorn is running: ps aux | grep uvicorn.

Check http://localhost:8000/health.





CLI Errors:

Run poetry run python cli.py selftest to diagnose issues.

Check logs in audit\_trail.log.





Mock Backends Failing:

Ensure APP\_ENV=development in .env to use mocks.

Verify Splunk at http://localhost:8000.





Dependency Issues:

Reinstall dependencies: poetry install --no-cache.

Check poetry.lock for conflicts.





Prometheus Metrics Missing:

Verify METRICS\_PORT=9091 in .env.

Check Prometheus scrape config in prometheus.yml.







Known Limitations



Mocked Backends: Demo uses mock DLT (SimpleDLTClient) and SIEM (Splunk) backends.

Incomplete Components: agent\_orchestration and ci\_cd lack full code.

Duplicated Logic: fixer\_ai.py and core\_ai.py overlap.

Missing Entrypoints: main.py and smart\_refactor\_agent.py are absent.

Security: Mock keys are used; production requires real keys and Vault integration.



Additional Resources



README.md: Overview of SFE architecture and usage.

CONTRIBUTING.md: Guidelines for contributing to SFE.

SECURITY.md: Security reporting process.

Source Code: https://github.com/musicmonk42/self\_fixing\_engineer

Issues: https://github.com/musicmonk42/self\_fixing\_engineer/issues



Contact

For support, contact support@self\_fixing\_engineer.org or file an issue on GitHub.

