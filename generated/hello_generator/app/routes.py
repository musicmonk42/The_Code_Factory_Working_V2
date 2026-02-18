# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.
"""API routes for the Hello Generator application."""

from fastapi import APIRouter, status
from app import schemas

router = APIRouter()

# Counter for generating item IDs
_item_id_counter = 1
_items_storage = []


@router.get("/health", response_model=schemas.HealthResponse, tags=["system"])
async def health_check() -> schemas.HealthResponse:
    """
    Health check endpoint.
    
    Returns:
        HealthResponse: Service health status.
    """
    return schemas.HealthResponse(status="ok")


@router.get("/version", response_model=schemas.VersionResponse, tags=["system"])
async def version() -> schemas.VersionResponse:
    """
    Get application version.
    
    Returns:
        VersionResponse: Application version information.
    """
    from app import __version__
    return schemas.VersionResponse(version=__version__)


@router.post("/echo", response_model=schemas.EchoResponse, tags=["echo"])
async def echo_message(request: schemas.EchoRequest) -> schemas.EchoResponse:
    """
    Echo back a message.
    
    Args:
        request: EchoRequest containing the message to echo.
        
    Returns:
        EchoResponse: The echoed message.
    """
    return schemas.EchoResponse(message=request.message)


@router.post(
    "/items",
    response_model=schemas.ItemResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["items"]
)
async def create_item(item: schemas.ItemRequest) -> schemas.ItemResponse:
    """
    Create a new item.
    
    Args:
        item: ItemRequest containing item details.
        
    Returns:
        ItemResponse: The created item with assigned ID.
    """
    global _item_id_counter
    
    # Create response with generated ID
    response = schemas.ItemResponse(
        id=_item_id_counter,
        name=item.name,
        description=item.description,
        price=item.price
    )
    
    # Store and increment counter
    _items_storage.append(response)
    _item_id_counter += 1
    
    return response
