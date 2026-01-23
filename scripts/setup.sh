#!/bin/bash
# The Code Factory - Comprehensive Setup Script
# This script sets up all dependencies and initializes the platform
# For production deployments, review and customize environment variables

set -e  # Exit on error
set -o pipefail  # Catch errors in pipes

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

log_success() {
    echo -e "${GREEN}✅${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

log_error() {
    echo -e "${RED}❌${NC} $1"
}

log_header() {
    echo ""
    echo -e "${BLUE}╔════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║${NC} $1"
    echo -e "${BLUE}╚════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

# Check Python version
check_python() {
    log_header "Checking Python Version"
    
    if ! command -v python3 &> /dev/null; then
        log_error "Python 3 is not installed. Please install Python 3.11 or higher."
        exit 1
    fi
    
    PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    log_info "Found Python $PYTHON_VERSION"
    
    # Simple version comparison (assumes Python 3.x)
    MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
    MINOR=$(echo $PYTHON_VERSION | cut -d. -f2)
    
    if [ "$MAJOR" -lt 3 ] || ([ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 11 ]); then
        log_warning "Python 3.11+ is recommended. You have Python $PYTHON_VERSION"
    else
        log_success "Python version check passed"
    fi
}

# Install Python dependencies
install_dependencies() {
    log_header "Installing Python Dependencies"
    
    if [ ! -f "requirements.txt" ]; then
        log_error "requirements.txt not found. Are you in the project root?"
        exit 1
    fi
    
    log_info "Upgrading pip, setuptools, and wheel..."
    python3 -m pip install --upgrade pip setuptools wheel || {
        log_error "Failed to upgrade pip"
        exit 1
    }
    
    log_info "Installing project dependencies..."
    python3 -m pip install -r requirements.txt || {
        log_error "Failed to install dependencies"
        exit 1
    }
    
    log_success "Python dependencies installed successfully"
}

# Download NLTK data
download_nltk_data() {
    log_header "Downloading NLTK Data"
    
    log_info "Downloading required NLTK datasets..."
    python3 -c "
import nltk
import sys

datasets = ['punkt', 'stopwords', 'vader_lexicon', 'punkt_tab']
failed = []

for dataset in datasets:
    try:
        nltk.download(dataset, quiet=True)
        print(f'✓ Downloaded {dataset}')
    except Exception as e:
        print(f'✗ Failed to download {dataset}: {e}', file=sys.stderr)
        failed.append(dataset)

if failed:
    sys.exit(1)
" || {
        log_warning "Some NLTK datasets failed to download. The platform may still work with reduced functionality."
    }
    
    log_success "NLTK data downloaded successfully"
}

# Download SpaCy models
download_spacy_models() {
    log_header "Downloading SpaCy Models"
    
    log_info "Downloading SpaCy English models..."
    
    # Download small model (required)
    python3 -m spacy download en_core_web_sm || {
        log_warning "Failed to download en_core_web_sm model"
    }
    
    # Download large model (optional, for better accuracy)
    python3 -m spacy download en_core_web_lg || {
        log_warning "Failed to download en_core_web_lg model. Test generation may have reduced accuracy."
    }
    
    log_success "SpaCy models downloaded"
}

# Check for .env file
check_environment() {
    log_header "Checking Environment Configuration"
    
    if [ ! -f ".env" ]; then
        log_warning ".env file not found"
        
        if [ -f ".env.example" ]; then
            log_info "Creating .env from .env.example..."
            cp .env.example .env
            log_success "Created .env file. Please review and update it with your configuration."
        else
            log_warning "No .env.example found. You'll need to create .env manually."
        fi
    else
        log_success ".env file exists"
    fi
    
    # Check for critical environment variables
    if [ -f ".env" ]; then
        log_info "Checking for critical environment variables..."
        
        # Source the .env file
        set -a
        source .env 2>/dev/null || true
        set +a
        
        # Check for API keys (optional but recommended)
        if [ -z "$OPENAI_API_KEY" ] && [ -z "$ANTHROPIC_API_KEY" ] && [ -z "$GEMINI_API_KEY" ]; then
            log_warning "No LLM API keys found. Set at least one: OPENAI_API_KEY, ANTHROPIC_API_KEY, or GEMINI_API_KEY"
        fi
        
        # Check for audit signing key
        if [ -z "$AGENTIC_AUDIT_HMAC_KEY" ]; then
            log_warning "AGENTIC_AUDIT_HMAC_KEY not set. Generating a secure key..."
            # Generate a secure 64-character hex key
            if command -v openssl &> /dev/null; then
                SECURE_KEY=$(openssl rand -hex 32)
            else
                SECURE_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
            fi
            echo "AGENTIC_AUDIT_HMAC_KEY=$SECURE_KEY" >> .env
            log_success "Generated and saved AGENTIC_AUDIT_HMAC_KEY to .env"
        fi
    fi
}

# Initialize database (if applicable)
initialize_database() {
    log_header "Checking Database Configuration"
    
    if [ -f "alembic.ini" ]; then
        log_info "Alembic configuration found. Running migrations..."
        
        # Check if DATABASE_URL is set
        if [ -z "$DATABASE_URL" ]; then
            log_warning "DATABASE_URL not set. Skipping database initialization."
            log_info "Set DATABASE_URL in .env to enable database features."
            return
        fi
        
        # Run migrations
        python3 -m alembic upgrade head || {
            log_warning "Database migration failed. Continuing anyway..."
        }
        
        log_success "Database initialized"
    else
        log_info "No Alembic configuration found. Skipping database setup."
    fi
}

# Check optional dependencies
check_optional_dependencies() {
    log_header "Checking Optional Dependencies"
    
    # Check Redis
    if command -v redis-cli &> /dev/null; then
        if redis-cli ping &> /dev/null 2>&1; then
            log_success "Redis is running"
        else
            log_warning "Redis is installed but not running. Message bus features will be disabled."
        fi
    else
        log_info "Redis not found. Message bus features will use fallback mode."
    fi
    
    # Check Docker
    if command -v docker &> /dev/null; then
        if docker ps &> /dev/null 2>&1; then
            log_success "Docker is available"
        else
            log_warning "Docker is installed but not accessible. Container features may be limited."
        fi
    else
        log_info "Docker not found. Container isolation features will be disabled."
    fi
    
    # Check graphviz
    if command -v dot &> /dev/null; then
        log_success "Graphviz is available for diagram generation"
    else
        log_warning "Graphviz not found. Install with: apt-get install graphviz (or your package manager)"
    fi
}

# Create required directories
create_directories() {
    log_header "Creating Required Directories"
    
    DIRS=("logs" "uploads" "logs/analyzer_audit" ".cache")
    
    for dir in "${DIRS[@]}"; do
        if [ ! -d "$dir" ]; then
            mkdir -p "$dir"
            log_info "Created directory: $dir"
        fi
    done
    
    log_success "Required directories created"
}

# Run health check
run_health_check() {
    log_header "Running Health Check"
    
    if [ -f "health_check.py" ]; then
        log_info "Running health check script..."
        python3 health_check.py || {
            log_warning "Health check completed with warnings. Review the output above."
        }
    else
        log_info "No health check script found. Skipping."
    fi
}

# Main setup flow
main() {
    log_header "🚀 The Code Factory - Setup Script"
    
    log_info "Starting setup process..."
    log_info "Working directory: $(pwd)"
    
    # Check if we're in the project root
    if [ ! -f "requirements.txt" ]; then
        log_error "This script must be run from the project root directory."
        exit 1
    fi
    
    # Run setup steps
    check_python
    install_dependencies
    download_nltk_data
    download_spacy_models
    check_environment
    create_directories
    initialize_database
    check_optional_dependencies
    run_health_check
    
    # Final summary
    log_header "✨ Setup Complete!"
    
    echo ""
    log_success "The Code Factory has been set up successfully!"
    echo ""
    log_info "Next steps:"
    echo "  1. Review and update .env with your configuration"
    echo "  2. Ensure database and Redis are configured (if needed)"
    echo "  3. Start the server with: python -m uvicorn server.main:app --host 0.0.0.0 --port 8000"
    echo "  4. Or use Docker: docker-compose up --build"
    echo ""
    log_info "For more information, see:"
    echo "  - README.md - General documentation"
    echo "  - DEPLOYMENT.md - Production deployment guide"
    echo "  - QUICKSTART.md - Quick start guide"
    echo ""
}

# Run main function
main
