# Procfile for Railway deployment
# Updated with all critical fixes for event loop, config, and startup optimization
# Railway injects the PORT environment variable at runtime
# Using --log-level debug for verbose logging to identify startup issues
web: python server/run.py --workers 1 --log-level debug
