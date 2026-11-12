"""Collaboration: assignment, escalation, comments, versions and the readable activity trail.

Rules (enforced here, mirrored in the frontend's demo backend):
  - only managers assign or escalate; only a reviewer can assign-to; the AI has no role
  - escalating flags a review for attention and lets reviewers see it; it never skips a step or approves anything
  - everything below reads from the same audit_event rows the workflow writes, so nothing is reconstructed or guessed
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import forbidden, invalid
from app.models.audit import Approval, AuditEvent, Comment
from app.models.review import Review, ReviewVersion
from app.models.user import User, utcnow
from app.schemas.collab import (AssignBody, AssignOut, AuditEntryOut, EscalateBody, EscalateOut, EscalationOut, OpenItem,
                                Person, ReviewActivity, TeamMember, VersionOut)
from app.schemas.review import CommentOut
from app.services import audit_view
from app.services import workflow_service as wf
from app.services.audit_log import audit


def _people(db: Session, ids) -> dict[str, User]:
    ids = {i for i in ids if i}
    return {u.id: u for u in db.scalars(select(User).where(User.id.in_(ids)))} if ids else {}


def _person(users: dict[str, User], uid: str | None) -> Person | None:
    u = users.get(uid or "")
    return Person(id=u.id, name=u.name, role=u.role) if u else None


def _entries(db: Session, rows: list[AuditEvent]) -> list[AuditEntryOut]:
    users = _people(db, (r.actor_id for r in rows))
    return [AuditEntryOut(id=f"AE-{r.pk}", review_id=r.review_id, at=r.created_at, actor=_person(users, r.actor_id),
                          event_type=r.event_type, summary=audit_view.describe(r.event_type, r.detail, r.reason),
                          reason=r.reason or "", detail=r.detail or {}) for r in rows]


def activity(db: Session, user: User, review_id: str) -> ReviewActivity:
    review = wf.load_review(db, user, review_id)
    rows = db.scalars(select(AuditEvent).where(AuditEvent.review_id == review.id).order_by(AuditEvent.pk.desc())).all()
    users = _people(db, [review.assignee_id, review.escalated_by])
    escalation = (EscalationOut(by=_person(users, review.escalated_by), at=review.escalated_at, note=review.escalation_note or "")
                  if review.escalated_at else None)
    last_note = db.scalar(select(Approval.note).where(Approval.review_id == review.id, Approval.decision == "changes_requested")
                          .order_by(Approval.pk.desc()).limit(1)) or ""
    items = audit_view.compute_open_items(
        review.status, wf._unresolved(db, review.id), users[review.assignee_id].name if review.assignee_id in users else None,
        {"by_name": users[review.escalated_by].name if review.escalated_by in users else None, "note": review.escalation_note} if escalation else None,
        last_note)
    return ReviewActivity(review_id=review.id, status=review.status, assignee=_person(users, review.assignee_id),
                          escalation=escalation, open_items=[OpenItem(**i) for i in items], entries=_entries(db, list(rows)))


def versions(db: Session, user: User, review_id: str) -> list[VersionOut]:
    review = wf.load_review(db, user, review_id)
    rows = db.scalars(select(ReviewVersion).where(ReviewVersion.review_id == review.id).order_by(ReviewVersion.number.desc())).all()
    users = _people(db, (r.created_by for r in rows))
    return [VersionOut(number=r.number, reason=r.reason, created_at=r.created_at, author=_person(users, r.created_by), content=r.content) for r in rows]


def comments(db: Session, user: User, review_id: str) -> list[CommentOut]:
    review = wf.load_review(db, user, review_id)
    rows = db.scalars(select(Comment).where(Comment.review_id == review.id).order_by(Comment.created_at, Comment.id)).all()
    users = _people(db, (r.author_id for r in rows))
    return [CommentOut(id=c.id, author=users[c.author_id].name if c.author_id in users else "Unknown", text=c.text, created_at=c.created_at) for c in rows]


def assign(db: Session, user: User, review_id: str, body: AssignBody) -> AssignOut:
    if user.role != "manager":
        raise forbidden("Only managers can assign reviews.")
    review = wf.load_review(db, user, review_id)
    if review.status == "approved":
        raise invalid("Approved reviews cannot be reassigned.")
    note = body.note.strip()
    if body.assignee_id is None:
        if review.assignee_id is None:
            raise invalid("This review is not assigned to anyone.")
        old = db.get(User, review.assignee_id)
        review.assignee_id, review.assigned_at = None, None
        audit(db, user, review.workspace_id, review.id, "unassigned", {"assignee_name": old.name if old else ""}, note)
        target = None
    else:
        target = db.get(User, body.assignee_id)
        if target is None or target.workspace_id != user.workspace_id or target.role != "reviewer":
            raise invalid("assignee_id must be a reviewer in this workspace.")
        review.assignee_id, review.assigned_at = target.id, utcnow()
        audit(db, user, review.workspace_id, review.id, "assigned", {"assignee_id": target.id, "assignee_name": target.name}, note)
    review.updated_at = utcnow()
    db.commit()
    return AssignOut(review_id=review.id, assignee=Person(id=target.id, name=target.name, role=target.role) if target else None)


def escalate(db: Session, user: User, review_id: str, body: EscalateBody) -> EscalateOut:
    if user.role != "manager":
        raise forbidden("Only managers can escalate reviews.")
    review = wf.load_review(db, user, review_id)
    if review.status == "approved":
        raise invalid("Approved reviews cannot be escalated.")
    if review.escalated_at is not None:
        raise invalid("This review is already escalated.")
    note = body.note.strip()
    if not note:
        raise invalid("A note is required to escalate a review.")
    review.escalated_at, review.escalated_by, review.escalation_note = utcnow(), user.id, note
    review.updated_at = utcnow()
    audit(db, user, review.workspace_id, review.id, "escalated", {}, note)
    db.commit()
    wf.notify(db, review, "review.escalated", user, note)
    return EscalateOut(review_id=review.id, status=review.status, escalated_at=review.escalated_at)


def team(db: Session, user: User, role: str | None = None) -> list[TeamMember]:
    if user.role not in ("manager", "brand_admin"):
        raise forbidden("Only managers and brand admins can list the team.")
    q = select(User).where(User.workspace_id == user.workspace_id).order_by(User.name)
    if role:
        q = q.where(User.role == role)
    return [TeamMember(id=u.id, name=u.name, role=u.role) for u in db.scalars(q)]


def audit_feed(db: Session, user: User, limit: int = 200, review_id: str | None = None, event_type: str | None = None) -> list[AuditEntryOut]:
    if user.role not in ("manager", "brand_admin"):
        raise forbidden("Only managers and brand admins can read the workspace audit trail.")
    q = select(AuditEvent).where(AuditEvent.workspace_id == user.workspace_id)
    if review_id:
        q = q.where(AuditEvent.review_id == review_id)
    if event_type:
        q = q.where(AuditEvent.event_type == event_type)
    return _entries(db, list(db.scalars(q.order_by(AuditEvent.pk.desc()).limit(max(1, min(limit, 500))))))
