from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator

router = APIRouter()


class Item(BaseModel):
    name: str = Field(max_length=500)
    price: float = Field(gt=0)
    quantity: int

    @field_validator("name", mode="before")
    @classmethod
    def name_must_not_be_empty(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("name must not be empty")
        return v


@router.post("/items")
def create_item(item: Item):
    data = item.model_dump()
    return {"message": "Item created", "item": data}
