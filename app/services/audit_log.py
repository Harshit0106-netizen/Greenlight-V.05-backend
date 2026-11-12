"""Writing audit events. Kept in its own module so every service can record events without importing each other."""
from sqlalchemy.orm import Session

from app.models.audit import AuditEvent
from app.models.user import User


def audit(db: Session, user: User | None, workspace_id: str, review_id: str | None, event: str,
          detail: dict | None = None, reason: str = "") -> None:
    db.add(AuditEvent(workspace_id=workspace_id, review_id=review_id, actor_id=user.id if user else None,
                      event_type=event, detail=detail or {}, reason=reason))
