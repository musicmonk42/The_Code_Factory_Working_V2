<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

SFE Environment Setup

Version: 1.0.0Last Updated: August 22, 2025Authors: Alex Rivera, musicmonk42, Infinite Open Source Collective, Universal AI Swarm, The Oracle Consortium  

Purpose

This document guides engineers through setting up the development environment for the Self-Fixing Engineer (SFE) platform, ensuring all tools and dependencies are corSFE Environment Setup

Version: 1.0.0Last Updated: August 22, 2025Authors: Alex Rivera, musicmonk42, Infinite Open Source Collective, Universal AI Swarm, The Oracle Consortium  

Purpose

This document guides engineers through setting up the development environment for the Self-Fixing Engineer (SFE) platform, ensuring all tools and dependencies are correctly installed for running the demo described in DEMO\_GUIDE.md. It includes platform-specific instructions and verification steps to avoid common setup issues.

System Requirements



Operating System: Ubuntu 20.04+, macOS 12+, or Windows 10+ with WSL2

CPU: 4 cores (8 recommended for performance)

RAM: 8GB (16GB recommended for Docker and simulations)

Disk Space: 20GB free (for Docker images and logs)

Network: Stable internet for dependency downloads and blockchain setup (optional)



Required Tools



Python: 3.10.11 (or 3.10+)

Go: 1.18+ (for checkpoint\_chaincode.go)

Node.js: 16+ (for JavaScript test generation)

Docker: 20.10+ (for mock services and sandbox)

Docker Compose: 1.29+ (for multi-container setup)

Redis: 6.2+ (for event bus and caching)

PostgreSQL: 13+ (optional, for requirements management)

Git: For repository cloning

Curl: For API testing

Hardhat/Foundry: For Ethereum smart contract deployment (optional)



Installation Instructions

Ubuntu 20.04+



Update System:

sudo apt-get update \&\& sudo apt-get upgrade -y





Install Tools:

sudo apt-get install -y python3.10 python3-pip golang-go nodejs npm docker.io docker-compose redis-server curl





Install Poetry:

pip install --user pipx

pipx install poetry





Verify Installations:

python3 --version  # Should output 3.10.11

go version         # Should output go1.18 or higher

node --version     # Should output v16 or higher

docker --version   # Should output 20.10 or higher

docker-compose --version  # Should output 1.29 or higher

redis-cli ping     # Should output PONG

poetry --version   # Should output Poetry version





Add User to Docker Group:

sudo usermod -aG docker $USER

newgrp docker







macOS 12+



Install Homebrew (if not installed):

/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"





Install Tools:

brew install python@3.10 go node docker docker-compose redis





Install Poetry:

pip install --user pipx

pipx install poetry





Start Redis:

brew services start redis





Verify Installations:

python3.10 --version

go version

node --version

docker --version

docker-compose --version

redis-cli ping

poetry --version







Windows with WSL2



Enable WSL2:

wsl --install



Install Ubuntu 20.04 from the Microsoft Store.



Open Ubuntu in WSL2:

wsl -d Ubuntu-20.04





Follow Ubuntu Instructions (see above).



Install Docker Desktop:



Download and install Docker Desktop for Windows.

Enable WSL2 integration in Docker Desktop settings.





Verify Docker:

docker run hello-world







Dependency Installation



Clone the SFE Repository:

git clone https://github.com/musicmonk42/self\_fixing\_engineer.git

cd self\_fixing\_engineer





Install Python Dependencies:

poetry install



If poetry fails, try:

pip install -r requirements.txt





Optional: Install Hardhat for Ethereum:

npm install -g @nomicfoundation/hardhat







Verification



Check Redis:

redis-cli ping  # Should return PONG





Check Docker:

docker ps  # Should list running containers





Check Python Environment:

poetry run python -c "import fastapi, pydantic, prometheus\_client, deap; print('Dependencies OK')"







Troubleshooting



Poetry Dependency Conflicts:

Clear cache: poetry cache clear --all pypi

Reinstall: poetry install --no-cache





Docker Permission Denied:

Ensure user is in Docker group: sudo usermod -aG docker $USER





Redis Not Connecting:

Restart Redis: sudo service redis restart (Ubuntu) or brew services restart redis (macOS)





See TROUBLESHOOTING.md for more details.



Next Steps



Follow DEMO\_GUIDE.md to set up and run the demo.

Refer to CONFIG\_REFERENCE.md for environment variable setup.

rectly installed for running the demo described in DEMO\_GUIDE.md. It includes platform-specific instructions and verification steps to avoid common setup issues.

System Requirements



Operating System: Ubuntu 20.04+, macOS 12+, or Windows 10+ with WSL2

CPU: 4 cores (8 recommended for performance)

RAM: 8GB (16GB recommended for Docker and simulations)

Disk Space: 20GB free (for Docker images and logs)

Network: Stable internet for dependency downloads and blockchain setup (optional)



Required Tools



Python: 3.10.11 (or 3.10+)

Go: 1.18+ (for checkpoint\_chaincode.go)

Node.js: 16+ (for JavaScript test generation)

Docker: 20.10+ (for mock services and sandbox)

Docker Compose: 1.29+ (for multi-container setup)

Redis: 6.2+ (for event bus and caching)

PostgreSQL: 13+ (optional, for requirements management)

Git: For repository cloning

Curl: For API testing

Hardhat/Foundry: For Ethereum smart contract deployment (optional)



Installation Instructions

Ubuntu 20.04+



Update System:

sudo apt-get update \&\& sudo apt-get upgrade -y





Install Tools:

sudo apt-get install -y python3.10 python3-pip golang-go nodejs npm docker.io docker-compose redis-server curl





Install Poetry:

pip install --user pipx

pipx install poetry





Verify Installations:

python3 --version  # Should output 3.10.11

go version         # Should output go1.18 or higher

node --version     # Should output v16 or higher

docker --version   # Should output 20.10 or higher

docker-compose --version  # Should output 1.29 or higher

redis-cli ping     # Should output PONG

poetry --version   # Should output Poetry version





Add User to Docker Group:

sudo usermod -aG docker $USER

newgrp docker







macOS 12+



Install Homebrew (if not installed):

/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"





Install Tools:

brew install python@3.10 go node docker docker-compose redis





Install Poetry:

pip install --user pipx

pipx install poetry





Start Redis:

brew services start redis





Verify Installations:

python3.10 --version

go version

node --version

docker --version

docker-compose --version

redis-cli ping

poetry --version







Windows with WSL2



Enable WSL2:

wsl --install



Install Ubuntu 20.04 from the Microsoft Store.



Open Ubuntu in WSL2:

wsl -d Ubuntu-20.04





Follow Ubuntu Instructions (see above).



Install Docker Desktop:



Download and install Docker Desktop for Windows.

Enable WSL2 integration in Docker Desktop settings.





Verify Docker:

docker run hello-world







Dependency Installation



Clone the SFE Repository:

git clone https://github.com/musicmonk42/self\_fixing\_engineer.git

cd self\_fixing\_engineer





Install Python Dependencies:

poetry install



If poetry fails, try:

pip install -r requirements.txt





Optional: Install Hardhat for Ethereum:

npm install -g @nomicfoundation/hardhat







Verification



Check Redis:

redis-cli ping  # Should return PONG





Check Docker:

docker ps  # Should list running containers





Check Python Environment:

poetry run python -c "import fastapi, pydantic, prometheus\_client, deap; print('Dependencies OK')"







Troubleshooting



Poetry Dependency Conflicts:

Clear cache: poetry cache clear --all pypi

Reinstall: poetry install --no-cache





Docker Permission Denied:

Ensure user is in Docker group: sudo usermod -aG docker $USER





Redis Not Connecting:

Restart Redis: sudo service redis restart (Ubuntu) or brew services restart redis (macOS)





See TROUBLESHOOTING.md for more details.



Next Steps



Follow DEMO\_GUIDE.md to set up and run the demo.

Refer to CONFIG\_REFERENCE.md for environment variable setup.



