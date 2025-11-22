#!/bin/bash
# Post-create script for development container

set -e

echo "Setting up development environment..."

# Install dependencies
if [ -f "requirements.txt" ]; then
    echo "Installing dependencies..."
    pip install -r requirements.txt
fi

# Install pre-commit hooks
if [ -f ".pre-commit-config.yaml" ]; then
    echo "Installing pre-commit hooks..."
    pip install pre-commit
    pre-commit install
fi

# Set up git
git config --global core.autocrlf input
git config --global pull.rebase false

echo "✓ Development environment setup complete!"
echo ""
echo "Quick start commands:"
echo "  make setup        - Initial setup"
echo "  make install-dev  - Install all dependencies"
echo "  make test         - Run tests"
echo "  make lint         - Run linters"
echo "  make docker-up    - Start all services"
echo ""
