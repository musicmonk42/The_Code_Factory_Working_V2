"""Pydantic schemas for the hello_generator application."""
from pydantic import BaseModel, Field, field_validator


class EchoRequest(BaseModel):
    """Schema for echo request with validation."""
    
    message: str = Field(..., min_length=1, max_length=500, description="Message to echo")
    
    @field_validator('message', mode='before')
    @classmethod
    def trim_and_validate_message(cls, v):
        """Trim whitespace and validate message is not empty."""
        if not isinstance(v, str):
            raise ValueError('Message must be a string')
        v = v.strip()
        if not v:
            raise ValueError('Message cannot be empty after trimming whitespace')
        return v
