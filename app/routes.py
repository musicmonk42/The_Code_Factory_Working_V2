from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter()


class Item(BaseModel):
    name: str = Field(max_length=500)
    price: float
    quantity: int


@router.post("/items")
def create_item(item: Item):
    data = item.model_dump()
    return {"message": "Item created", "item": data}
