# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# Procfile for Railway deployment
# Updated with all critical fixes for event loop, config, and startup optimization
# Railway injects the PORT environment variable at runtime
# FIX: Use --log-level info (not debug) to align with production log level configuration
# The main.py production detection will further reduce to WARNING for root logger
# FIX: Use 4 workers (not 1) to handle concurrent requests and prevent event loop saturation
web: python server/run.py --host 0.0.0.0 --workers 4 --log-level info
