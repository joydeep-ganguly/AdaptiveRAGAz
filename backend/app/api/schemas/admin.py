from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class AdminCreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    email: EmailStr
    password: str = Field(min_length=8)
    type: Literal["admin", "user"]


class FileUploadResponse(BaseModel):
    filename: str
    message: str


class IngestionResponse(BaseModel):
    filename: str
    pages_ingested: int
    skipped: bool