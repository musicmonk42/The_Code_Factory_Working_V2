# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.
"""Main FastAPI application setup."""

import time
from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware
from app.routes import router


class LoggingMiddleware(BaseHTTPMiddleware):
    """Middleware for logging requests."""
    
    async def dispatch(self, request: Request, call_next):
        """Process request and log timing information."""
        start_time = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start_time
        
        # Log request details (in production, use proper logger)
        print(f"{request.method} {request.url.path} - {duration:.3f}s")
        
        return response


# Create FastAPI application
app = FastAPI(
    title="Hello Generator API",
    description="A sample FastAPI application with echo and items endpoints",
    version="1.0.0",
)

# Add middleware
app.add_middleware(LoggingMiddleware)

# Include routes
app.include_router(router)


@app.on_event("startup")
async def startup_event():
    """Application startup event handler."""
    print("Hello Generator API starting up...")


@app.on_event("shutdown")
async def shutdown_event():
    """Application shutdown event handler."""
    print("Hello Generator API shutting down...")
