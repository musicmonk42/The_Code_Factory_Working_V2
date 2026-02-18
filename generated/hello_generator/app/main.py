"""Main FastAPI application for hello_generator."""
from fastapi import FastAPI, HTTPException
from app.schemas import EchoRequest

app = FastAPI(title="Hello Generator", version="1.0.0")


@app.post("/echo")
async def echo_message(request: EchoRequest) -> dict:
    """Echo back the provided message.
    
    Args:
        request: EchoRequest containing the message to echo
        
    Returns:
        dict: Response containing the echoed message
    """
    return {"message": request.message, "length": len(request.message)}


@app.get("/health")
async def health_check() -> dict:
    """Health check endpoint.
    
    Returns:
        dict: Health status
    """
    return {"status": "healthy"}
