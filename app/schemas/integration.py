from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.asset import AssetUpload
from app.schemas.collab import Person
from app.schemas.finding import FindingType, Severity
from app.schemas.review import ReviewStatus

BlockType = Literal["heading", "paragraph", "list", "quote", "image", "link", "button"]
Scope = Literal["reviews:write", "reviews:read"]
EventName = Literal["review.in_review", "review.approved", "review.changes_requested", "review.escalated", "ping"]


class Block(BaseModel):
    id: str | None = Field(default=None, max_length=80)
    type: BlockType
    text: str | None = Field(default=None, max_length=20_000)
    level: int | None = Field(default=None, ge=1, le=6)
    items: list[str] | None = Field(default=None, max_length=200)
    label: str | None = Field(default=None, max_length=300)
    url: str | None = Field(default=None, max_length=1000)
    alt: str | None = Field(default=None, max_length=500)
    asset_index: int | None = Field(default=None, ge=0)  # image block that refers to an entry in `assets`


class SourceRef(BaseModel):
    system: str = Field(min_length=1, max_length=80)
    external_id: str | None = Field(default=None, max_length=200)
    url: str | None = Field(default=None, max_length=500)


class StructuredReviewCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    blocks: list[Block] = Field(min_length=1, max_length=500)
    assets: list[AssetUpload] = Field(default_factory=list, max_length=4)
    source: SourceRef
    channel: str = Field(default="", max_length=80)
    intent_note: str = Field(default="", max_length=2000)


class IntegrationReviewCreated(BaseModel):
    id: str
    status: ReviewStatus
    finding_count: int
    report_url: str


class ReportFinding(BaseModel):
    finding_id: str
    type: FindingType
    severity: Severity
    quote: str
    block_id: str | None
    asset_id: str | None
    source: dict | None
    reason: str
    decision: dict | None


class ReportAsset(BaseModel):
    id: str
    filename: str
    alt_text: str
    sha256: str
    width: int
    height: int


class PublishReport(BaseModel):
    id: str
    title: str
    status: ReviewStatus
    ready_to_publish: bool
    blockers: list[str]
    approved_by: Person | None
    approved_at: datetime | None
    brand_kit_version: str
    content_sha256: str
    content: str
    source: dict | None
    findings: list[ReportFinding]
    assets: list[ReportAsset]


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    submit_as_user_id: str = Field(min_length=1, max_length=20)
    scopes: list[Scope] = Field(default_factory=lambda: ["reviews:write", "reviews:read"], min_length=1)


class ApiKeyOut(BaseModel):
    id: str
    name: str
    prefix: str
    scopes: list[str]
    submit_as: Person | None
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class ApiKeyCreated(ApiKeyOut):
    key: str  # shown once


class WebhookCreate(BaseModel):
    url: str = Field(min_length=1, max_length=500)
    events: list[EventName] = Field(min_length=1)


class WebhookOut(BaseModel):
    id: str
    url: str
    events: list[str]
    active: bool
    created_at: datetime
    last_status: str | None = None


class WebhookCreated(WebhookOut):
    secret: str  # shown once


class DeliveryOut(BaseModel):
    id: int
    webhook_id: str
    event: str
    review_id: str | None
    status: str
    http_status: int | None
    error: str
    attempted_at: datetime
    duration_ms: int
