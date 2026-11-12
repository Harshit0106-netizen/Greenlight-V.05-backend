from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.collab import (AssignBody, AssignOut, AuditEntryOut, EscalateBody, EscalateOut, ReviewActivity, TeamMember, VersionOut)
from app.schemas.integration import PublishReport
from app.schemas.review import CommentOut
from app.services import collab_service, integration_service

router = APIRouter(tags=["collaboration"])


@router.get("/reviews/{review_id}/activity", response_model=ReviewActivity)
def activity(review_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return collab_service.activity(db, user, review_id)


@router.get("/reviews/{review_id}/versions", response_model=list[VersionOut])
def versions(review_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return collab_service.versions(db, user, review_id)


@router.get("/reviews/{review_id}/comments", response_model=list[CommentOut])
def comments(review_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return collab_service.comments(db, user, review_id)


@router.post("/reviews/{review_id}/assign", response_model=AssignOut)
def assign(review_id: str, body: AssignBody, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return collab_service.assign(db, user, review_id, body)


@router.post("/reviews/{review_id}/escalate", response_model=EscalateOut)
def escalate(review_id: str, body: EscalateBody, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return collab_service.escalate(db, user, review_id, body)


@router.get("/reviews/{review_id}/publish-status", response_model=PublishReport)
def publish_status(review_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return integration_service.report_for_user(db, user, review_id)


@router.get("/team/members", response_model=list[TeamMember])
def team(role: str | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return collab_service.team(db, user, role)


@router.get("/audit", response_model=list[AuditEntryOut])
def audit_feed(limit: int = Query(200, ge=1, le=500), review_id: str | None = None, event_type: str | None = None,
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return collab_service.audit_feed(db, user, limit, review_id, event_type)
