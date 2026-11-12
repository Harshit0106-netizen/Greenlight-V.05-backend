"""Reviews, findings, decisions, comments and the review state machine.

Rules that live here (and nowhere else):
  - only the creator resolves findings; only a reviewer approves or requests changes; the AI has no role
  - a review reaches `in_review` only when every finding is edited or disputed (accepted-but-unfixed blocks)
  - every change is stored with actor, time and reason (audit_event)
"""
import logging
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.database import next_number
from app.errors import forbidden, invalid, not_found
from app.models.audit import Approval, AuditEvent, Comment
from app.models.finding import Finding, FindingDecision
from app.models.integration import ReviewAsset
from app.models.review import Review, ReviewVersion
from app.models.user import User, utcnow
from app.pipeline.engine import run_pipeline
from app.schemas.finding import Decision, FindingOut, FindingPatch, FindingPatchOut
from app.schemas.asset import AssetOut, AssetReplace, AssetUpload
from app.schemas.review import (CommentCreate, CommentOut, ReviewCreate, ReviewCreated, ReviewDetail,
                                ReviewListItem, WorkflowOut)
from app.services import ai_disclosure, asset_service, brand_kit_service
from app.services.audit_log import audit  # noqa: F401  (re-exported: other modules call wf.audit)
from app.pipeline import visual

log = logging.getLogger("greenlight.workflow")

REVIEWER_VISIBLE = ("in_review", "approved", "changes_requested")
EDITABLE = ("scanned", "resolving", "changes_requested")
TRANSITIONS: dict[str, set[str]] = {
    "draft": {"scanned"},
    "scanned": {"resolving", "in_review"},
    "resolving": {"resolving", "in_review"},
    "in_review": {"approved", "changes_requested"},
    "changes_requested": {"resolving", "in_review"},
    "approved": set(),
}


# ---- audit + state machine ------------------------------------------------------------------------
def transition(db: Session, review: Review, new: str, actor: User | None, reason: str = "") -> None:
    if new not in TRANSITIONS.get(review.status, set()):
        raise invalid(f"A review cannot move from {review.status} to {new}.")
    if review.status != new:
        audit(db, actor, review.workspace_id, review.id, "status_changed", {"from": review.status, "to": new}, reason)
    review.status = new
    review.updated_at = utcnow()


# ---- visibility -----------------------------------------------------------------------------------
def can_view(user: User, review: Review) -> bool:
    if review.workspace_id != user.workspace_id:
        return False
    if user.role == "creator":
        return review.creator_id == user.id
    if user.role == "reviewer":
        return review.status in REVIEWER_VISIBLE or review.escalated_at is not None  # escalated items come with their evidence
    return True  # manager, brand_admin: whole workspace (read-only for brand_admin)


def visible_reviews_query(user: User):
    q = select(Review).where(Review.workspace_id == user.workspace_id)
    if user.role == "creator":
        q = q.where(Review.creator_id == user.id)
    elif user.role == "reviewer":
        q = q.where(or_(Review.status.in_(REVIEWER_VISIBLE), Review.escalated_at.is_not(None)))
    return q


def load_review(db: Session, user: User, review_id: str) -> Review:
    review = db.get(Review, review_id)
    if review is None or not can_view(user, review):
        raise not_found("Review not found.")
    return review


def _content(db: Session, review: Review) -> str:
    return db.scalar(select(ReviewVersion.content).where(
        ReviewVersion.review_id == review.id, ReviewVersion.number == review.current_version))


def _latest_decision(db: Session, finding_id: str) -> FindingDecision | None:
    return db.scalar(select(FindingDecision).where(FindingDecision.finding_id == finding_id)
                     .order_by(FindingDecision.pk.desc()).limit(1))


def _finding_out(db: Session, f: Finding) -> FindingOut:
    d = _latest_decision(db, f.id)
    return FindingOut(
        finding_id=f.id, type=f.type, severity=f.severity,
        span={"start": f.span_start, "end": f.span_end, "text": f.span_text},
        source={"kind": f.source_kind, "id": f.source_id, "version": f.source_version} if f.source_id else None,
        fact_match=f.fact_match, reason=f.reason, confidence=f.confidence, method=f.method, suggestion=f.suggestion,
        decision=Decision(action=d.action, reason=d.reason, actor=d.actor_id) if d else None,
    )


def _unresolved(db: Session, review_id: str) -> int:
    n = 0
    for f in db.scalars(select(Finding).where(Finding.review_id == review_id)):
        d = _latest_decision(db, f.id)
        if d is None or d.action == "accepted":  # accepted = confirmed but not yet fixed
            n += 1
    return n


# ---- reviews --------------------------------------------------------------------------------------
def create_review(db: Session, user: User, body: ReviewCreate, structured: dict | None = None, via: dict | None = None) -> ReviewCreated:
    """`structured` and `via` are set when the draft arrives through the API (see integration_service)."""
    if user.role != "creator":
        raise forbidden("Only creators can submit drafts.")
    kit_version = brand_kit_service.current_version(db, user.workspace_id)
    kit = brand_kit_service.snapshot(db, user.workspace_id, kit_version)
    prepared = [asset_service.prepare(a, kit) for a in body.assets]  # reject bad images before anything is stored
    result = run_pipeline(body.content, kit)

    review = Review(id=f"CL-{next_number(db, 'review', 2001)}", workspace_id=user.workspace_id, creator_id=user.id,
                    title=body.title.strip(), intent_note=body.intent_note, status="draft",
                    brand_kit_version=kit_version, scan_mode=result.mode, structured=structured)
    db.add(review)
    db.flush()
    db.add(ReviewVersion(review_id=review.id, number=1, content=body.content, created_by=user.id))
    audit(db, user, user.workspace_id, review.id, "review_submitted", {"brand_kit_version": brand_kit_service.label(kit_version), **(via or {})})

    for f in result.findings:
        sug = f["suggestion"]
        db.add(Finding(
            id=f"FD-{next_number(db, 'finding', 1001)}", review_id=review.id, type=f["type"], severity=f["severity"],
            span_start=f["span"]["start"], span_end=f["span"]["end"], span_text=f["span"]["text"],
            source_kind=f["source"]["kind"] if f["source"] else None, source_id=f["source"]["id"] if f["source"] else None,
            source_version=f["source"]["version"] if f["source"] else None, fact_match=f["fact_match"],
            reason=f["reason"], confidence=f["confidence"], method=f["method"], suggestion=sug))
    asset_findings = 0
    for p in prepared:
        _, n = asset_service.store(db, user, review, p, kit)
        asset_findings += n
    ai_disclosure.record(db, review.id, result, images=len(prepared))
    audit(db, None, user.workspace_id, review.id, "scan_completed",
          {"mode": result.mode, "findings": len(result.findings) + asset_findings, "assets": asset_findings,
           "dropped_unverified": result.dropped_unverified})
    transition(db, review, "scanned", None, "scan finished")
    total = len(result.findings) + asset_findings
    if not total:
        transition(db, review, "in_review", None, "no findings to resolve")
    db.commit()
    if not total:
        notify(db, review, "review.in_review", None)
    return ReviewCreated(id=review.id, status=review.status, finding_count=total)


def list_reviews(db: Session, user: User) -> list[ReviewListItem]:
    rows = db.execute(
        visible_reviews_query(user).add_columns(User.name, func.count(Finding.id))
        .join(User, User.id == Review.creator_id)
        .outerjoin(Finding, Finding.review_id == Review.id)
        .group_by(Review.id, User.name).order_by(Review.updated_at.desc())).all()
    return [ReviewListItem(id=r.id, title=r.title, creator=name, status=r.status, finding_count=n, updated_at=r.updated_at)
            for r, name, n in rows]


def get_review(db: Session, user: User, review_id: str) -> ReviewDetail:
    r = load_review(db, user, review_id)
    return ReviewDetail(id=r.id, title=r.title, content=_content(db, r), status=r.status, creator=db.get(User, r.creator_id).name,
                        brand_kit_version=brand_kit_service.label(r.brand_kit_version), updated_at=r.updated_at)


def get_findings(db: Session, user: User, review_id: str) -> list[FindingOut]:
    r = load_review(db, user, review_id)
    rank = {"high": 0, "medium": 1, "low": 2}
    rows = sorted(db.scalars(select(Finding).where(Finding.review_id == r.id, Finding.asset_id.is_(None))),
                  key=lambda f: (rank[f.severity], f.span_start))
    return [_finding_out(db, f) for f in rows]


# ---- decisions ------------------------------------------------------------------------------------
def decide_finding(db: Session, user: User, finding_id: str, body: FindingPatch) -> FindingPatchOut:
    finding = db.get(Finding, finding_id)
    review = db.get(Review, finding.review_id) if finding else None
    if finding is None or review is None or review.workspace_id != user.workspace_id:
        raise not_found("Finding not found.")
    if user.role != "creator" or review.creator_id != user.id:
        raise forbidden("Only the creator of this review can accept, edit or dispute its findings.")
    if review.status not in EDITABLE:
        raise invalid(f"Findings cannot be changed while the review is {review.status}.")

    reason = body.reason.strip()
    if body.action == "disputed" and not reason:
        raise invalid("A reason is required to dispute a finding.")

    asset = db.get(ReviewAsset, finding.asset_id) if finding.asset_id else None
    previous_status = review.status
    before_text = finding.span_text
    if body.action == "edited" and asset is not None:
        asset_service.apply_alt_edit(db, user, review, finding, asset, body.edited_text)
        reason = reason or "Updated the alt text."
    elif body.action == "edited":
        new_text = body.edited_text
        if new_text is None or not new_text.strip():
            raise invalid("edited_text is required when action is 'edited'.")
        content = _content(db, review)
        if content[finding.span_start:finding.span_end] != finding.span_text:
            raise invalid("The flagged text has changed; reload the review.")
        delta = len(new_text) - (finding.span_end - finding.span_start)
        updated = content[:finding.span_start] + new_text + content[finding.span_end:]
        review.current_version += 1
        db.add(ReviewVersion(review_id=review.id, number=review.current_version, content=updated, created_by=user.id,
                             reason=f"edit for {finding.id}"))
        for other in db.scalars(select(Finding).where(Finding.review_id == review.id, Finding.id != finding.id)):
            if other.span_start >= finding.span_end:  # findings never overlap, so later ones simply shift
                other.span_start += delta
                other.span_end += delta
        finding.span_end = finding.span_start + len(new_text)
        finding.span_text = new_text
        if finding.suggestion:
            finding.suggestion = {**finding.suggestion, "applied": new_text == finding.suggestion.get("text")}
        reason = reason or "Edited the flagged wording."
        audit(db, user, review.workspace_id, review.id, "content_edited",
              {"finding_id": finding.id, "version": review.current_version, "before": before_text, "after": new_text}, reason)

    decision = FindingDecision(finding_id=finding.id, action=body.action, reason=reason,
                               edited_text=body.edited_text if body.action == "edited" else None, actor_id=user.id)
    db.add(decision)
    db.flush()
    audit(db, user, review.workspace_id, review.id, "finding_decision",
          {"finding_id": finding.id, "action": body.action, "quote": before_text, "type": finding.type, "asset_id": finding.asset_id}, reason)

    settle(db, review, user)
    db.commit()
    if review.status == "in_review" and previous_status != "in_review":
        notify(db, review, "review.in_review", user)
    return FindingPatchOut(finding_id=finding.id, decision=Decision(action=body.action, reason=reason, actor=user.id))


# ---- comments, approval ---------------------------------------------------------------------------
def add_comment(db: Session, user: User, review_id: str, body: CommentCreate) -> CommentOut:
    if user.role not in ("creator", "manager"):
        raise forbidden("Only creators and managers can comment.")
    review = load_review(db, user, review_id)
    return _comment(db, user, review, body.text)


def _comment(db: Session, user: User, review: Review, text: str) -> CommentOut:
    c = Comment(id=f"C-{next_number(db, 'comment', 1)}", review_id=review.id, author_id=user.id, text=text.strip())
    db.add(c)
    db.flush()
    audit(db, user, review.workspace_id, review.id, "comment_added", {"comment_id": c.id, "text": c.text})
    db.commit()
    return CommentOut(id=c.id, author=user.name, text=c.text, created_at=c.created_at)


def _decide_review(db: Session, user: User, review_id: str, outcome: str, note: str) -> WorkflowOut:
    if user.role != "reviewer":
        raise forbidden("Only reviewers can approve or request changes.")
    review = load_review(db, user, review_id)
    if review.status != "in_review":
        raise invalid(f"Only reviews that are in_review can be decided (this one is {review.status}).")
    if outcome == "approved" and _unresolved(db, review.id):
        raise invalid("Every finding must be edited or disputed before approval.")
    note = note.strip()
    if outcome == "changes_requested" and not note:
        raise invalid("A note is required when requesting changes.")
    transition(db, review, outcome, user, note)
    review.decided_at = utcnow() if outcome == "approved" else None
    db.add(Approval(review_id=review.id, actor_id=user.id, decision=outcome, note=note))
    audit(db, user, review.workspace_id, review.id, outcome, {}, note)
    if note:  # the note is visible to the creator in the comment thread
        db.add(Comment(id=f"C-{next_number(db, 'comment', 1)}", review_id=review.id, author_id=user.id, text=note))
    db.commit()
    notify(db, review, "review.approved" if outcome == "approved" else "review.changes_requested", user, note)
    return WorkflowOut(id=review.id, status=review.status)


def approve(db: Session, user: User, review_id: str, note: str) -> WorkflowOut:
    return _decide_review(db, user, review_id, "approved", note)


def request_changes(db: Session, user: User, review_id: str, note: str) -> WorkflowOut:
    return _decide_review(db, user, review_id, "changes_requested", note)


# ---- shared helpers ----------------------------------------------------------------------------------
def settle(db: Session, review: Review, user: User) -> None:
    """After a decision or a change: ready for a reviewer when nothing is unresolved, otherwise still being resolved."""
    target = "in_review" if _unresolved(db, review.id) == 0 else "resolving"
    transition(db, review, target, user, "all findings resolved" if target == "in_review" else "")


def notify(db: Session, review: Review, event: str, actor: User | None, note: str = "") -> None:
    """Tell subscribed webhooks. Never lets a failing integration break the review workflow."""
    try:
        from app.services import integration_service  # imported here: integration_service uses this module

        integration_service.emit(db, review, event, actor, note)
    except Exception:  # noqa: BLE001
        log.exception("Webhook notification failed for %s %s", review.id, event)


def _editable_own_review(db: Session, user: User, review_id: str) -> Review:
    review = load_review(db, user, review_id)
    if user.role != "creator" or review.creator_id != user.id:
        raise forbidden("Only the creator of this review can add or replace images.")
    if review.status not in EDITABLE:
        raise invalid(f"Images cannot be changed while the review is {review.status}.")
    return review


def _asset_out(db: Session, asset: ReviewAsset) -> AssetOut:
    found = db.scalars(select(Finding).where(Finding.asset_id == asset.id).order_by(Finding.id)).all()
    return asset_service.to_out(db, asset, [_finding_out(db, f) for f in found])


def add_asset(db: Session, user: User, review_id: str, body: AssetUpload) -> AssetOut:
    review = _editable_own_review(db, user, review_id)
    if asset_service.count_for(db, review.id) >= visual.MAX_ASSETS_PER_REVIEW:
        raise invalid(f"A review can have at most {visual.MAX_ASSETS_PER_REVIEW} images.")
    kit = brand_kit_service.snapshot(db, review.workspace_id, review.brand_kit_version)
    asset, _ = asset_service.store(db, user, review, asset_service.prepare(body, kit), kit)
    previous_status = review.status
    settle(db, review, user)
    db.commit()
    if review.status == "in_review" and previous_status != "in_review":
        notify(db, review, "review.in_review", user)
    return _asset_out(db, asset)


def replace_asset(db: Session, user: User, review_id: str, asset_id: str, body: AssetReplace) -> AssetOut:
    review = _editable_own_review(db, user, review_id)
    asset = asset_service.load_asset(db, review, asset_id)
    kit = brand_kit_service.snapshot(db, review.workspace_id, review.brand_kit_version)
    prepared = asset_service.prepare(body, kit, filename=body.filename or asset.filename, alt_text=body.alt_text if body.alt_text is not None else asset.alt_text)
    asset_service.replace(db, user, review, asset, prepared, alt_given=body.alt_text is not None, kit=kit)
    previous_status = review.status
    settle(db, review, user)
    db.commit()
    if review.status == "in_review" and previous_status != "in_review":
        notify(db, review, "review.in_review", user)
    return _asset_out(db, asset)


def list_assets(db: Session, user: User, review_id: str) -> list[AssetOut]:
    review = load_review(db, user, review_id)
    rows = db.scalars(select(ReviewAsset).where(ReviewAsset.review_id == review.id).order_by(ReviewAsset.created_at, ReviewAsset.id)).all()
    return [_asset_out(db, a) for a in rows]


def asset_file(db: Session, user: User, review_id: str, asset_id: str) -> tuple[bytes, str]:
    review = load_review(db, user, review_id)
    asset = asset_service.load_asset(db, review, asset_id)
    return asset.data, asset.content_type
