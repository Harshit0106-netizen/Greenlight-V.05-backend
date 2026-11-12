"""Images attached to reviews, API keys, webhooks and their delivery log."""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.user import utcnow


class ReviewAsset(Base):
    """An image attached to a review. Bytes live in the database so this works on read-only serverless disks."""

    __tablename__ = "review_asset"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)  # AS-n
    review_id: Mapped[str] = mapped_column(ForeignKey("review.id"), index=True)
    filename: Mapped[str] = mapped_column(String(200))
    content_type: Mapped[str] = mapped_column(String(40))
    size_bytes: Mapped[int] = mapped_column(Integer)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64))
    alt_text: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[bytes] = mapped_column(LargeBinary)
    version: Mapped[int] = mapped_column(Integer, default=1)
    palette_checked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ApiKey(Base):
    """Lets another system (a CMS, a publishing pipeline) submit and read reviews. Only the hash is stored."""

    __tablename__ = "api_key"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)  # K-n
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    prefix: Mapped[str] = mapped_column(String(16))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    submit_as_user_id: Mapped[str] = mapped_column(ForeignKey("app_user.id"))  # reviews from this key belong to this creator
    created_by: Mapped[str] = mapped_column(ForeignKey("app_user.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Webhook(Base):
    __tablename__ = "webhook"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)  # WH-n
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id"), index=True)
    url: Mapped[str] = mapped_column(String(500))
    events: Mapped[list] = mapped_column(JSON, default=list)
    secret: Mapped[str] = mapped_column(String(80))  # needed to sign deliveries, so stored; shown to the admin once
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("app_user.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WebhookDelivery(Base):
    """One attempt to call a webhook. Holds the outcome only, never the draft text."""

    __tablename__ = "webhook_delivery"
    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id"), index=True)
    webhook_id: Mapped[str] = mapped_column(ForeignKey("webhook.id"), index=True)
    review_id: Mapped[str | None] = mapped_column(ForeignKey("review.id"), nullable=True)
    event: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20))  # delivered | failed
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
