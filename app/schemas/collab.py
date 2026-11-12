from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.review import ReviewStatus


class Person(BaseModel):
    id: str
    name: str
    role: str | None = None


class AuditEntryOut(BaseModel):
    id: str
    review_id: str | None
    at: datetime
    actor: Person | None  # null = Greenlight itself (automated checks)
    event_type: str
    summary: str
    reason: str
    detail: dict[str, Any]


class OpenItem(BaseModel):
    kind: str
    label: str
    count: int | None = None


class EscalationOut(BaseModel):
    by: Person | None
    at: datetime
    note: str


class ReviewActivity(BaseModel):
    review_id: str
    status: ReviewStatus
    assignee: Person | None
    escalation: EscalationOut | None
    open_items: list[OpenItem]
    entries: list[AuditEntryOut]  # newest first


class VersionOut(BaseModel):
    number: int
    reason: str
    created_at: datetime
    author: Person | None
    content: str


class AssignBody(BaseModel):
    assignee_id: str | None = None  # null removes the assignment
    note: str = Field(default="", max_length=2000)


class AssignOut(BaseModel):
    review_id: str
    assignee: Person | None


class EscalateBody(BaseModel):
    note: str = Field(min_length=1, max_length=2000)


class EscalateOut(BaseModel):
    review_id: str
    status: ReviewStatus
    escalated_at: datetime


class TeamMember(BaseModel):
    id: str
    name: str
    role: str
