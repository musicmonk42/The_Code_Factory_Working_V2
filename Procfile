# Procfile for Railway deployment
# Updated with all critical fixes for event loop, config, and startup optimization
# Railway injects the PORT environment variable at runtime
# FIX: Use --log-level info (not debug) to align with production log level configuration
# The main.py production detection will further reduce to WARNING for root logger
web: python server/run.py --host 0.0.0.0 --workers 1 --log-level info
