# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Generated FastAPI application entry point.

Run with:
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
"""

from fastapi import FastAPI

from app.routes import router

app = FastAPI(title="Generated App", version="1.0.0")
app.include_router(router)


@app.get("/health")
async def health() -> dict:
    """Liveness probe — always returns HTTP 200."""
    return {"status": "ok"}
