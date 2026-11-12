from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.finding import FindingOut

ImageType = Literal["image/png", "image/jpeg", "image/webp", "image/gif"]


class AssetUpload(BaseModel):
    filename: str = Field(min_length=1, max_length=200)
    content_type: ImageType
    data_base64: str = Field(min_length=1)
    alt_text: str = Field(default="", max_length=500)


class AssetReplace(BaseModel):
    content_type: ImageType
    data_base64: str = Field(min_length=1)
    filename: str | None = Field(default=None, max_length=200)
    alt_text: str | None = Field(default=None, max_length=500)


class AssetOut(BaseModel):
    id: str
    filename: str
    content_type: str
    size_bytes: int
    width: int
    height: int
    alt_text: str
    version: int
    palette_checked: bool
    created_at: datetime
    findings: list[FindingOut]
