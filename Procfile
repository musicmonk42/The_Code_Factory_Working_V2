# Procfile for Railway deployment
# Updated with all critical fixes for event loop, config, and startup optimization
# Railway injects the PORT environment variable at runtime
web: sh -c 'python -m uvicorn server.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1'
