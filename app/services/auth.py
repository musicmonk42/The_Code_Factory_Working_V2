# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""Authentication service functions."""

from typing import Any, Optional


def authenticate_user(username: str, password: str) -> Optional[dict]:
    """Verify credentials and return user info on success, None on failure.

    In a real implementation this would query a database and compare
    a hashed password.  This stub returns a synthetic user for any
    non-empty username/password pair to allow the API to start.
    """
    if username and password:
        return {"id": 1, "username": username}
    return None


def create_access_token(data: dict) -> str:
    """Create a JWT-like access token for the given payload.

    This stub returns a deterministic placeholder string.  Replace with
    a proper JWT library (e.g. python-jose or PyJWT) in production.
    """
    return f"stub-token-for-{data.get('username', 'unknown')}"


def get_current_user(token: str) -> Optional[dict]:
    """Decode and validate an access token, returning the user payload.

    Returns ``None`` when the token is invalid or missing.
    """
    if token and token.startswith("stub-token-for-"):
        username = token.removeprefix("stub-token-for-")
        return {"id": 1, "username": username}
    return None
