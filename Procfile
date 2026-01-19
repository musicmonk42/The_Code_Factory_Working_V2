# Procfile for Railway deployment
# Starts the A.S.E web interface using uvicorn with the FastAPI app
# Railway injects the PORT environment variable at runtime
web: python -m uvicorn server.main:app --host 0.0.0.0 --port $PORT
