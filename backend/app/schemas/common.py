from typing import Generic, TypeVar, List
from pydantic import BaseModel

T = TypeVar("T")

class BaseResponse(BaseModel):
    """Base response model."""
    pass

class PaginatedResponse(BaseResponse, Generic[T]):
    """Standard pagination response."""
    items: List[T]
    total: int
    page: int
    page_size: int
    total_pages: int
