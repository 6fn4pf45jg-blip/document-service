from pydantic import BaseModel
from typing import Literal


class DocumentResponse(BaseModel):
    id: str
    filename: str
    status: Literal["uploaded", "processing", "processed", "failed"]