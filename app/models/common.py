from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ApiEnvelope(BaseModel, Generic[T]):
    status: str = Field(default="ok")
    ts: datetime = Field(default_factory=datetime.now)
    data: T
    message: str = Field(default="")


class ErrorEnvelope(BaseModel):
    status: str = Field(default="error")
    ts: datetime = Field(default_factory=datetime.now)
    error_code: str
    message: str
