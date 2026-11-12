"""Dashboard numbers and Brand Kit gap signals. Read-only aggregates, scoped to what the caller may see."""
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import forbidden
from app.models.brand_kit import SourceItem
from app.models.finding import Finding, FindingDecision
from app.models.review import Review
from app.models.user import User
from app.schemas.patterns import PatternsOut
from app.schemas.review import InsightGap, InsightsSummary, TopIssue
from app.services.patterns import build_patterns
from app.services.workflow_service import visible_reviews_query

NEEDS_ATTENTION = ("scanned", "resolving", "changes_requested")


def summary(db: Session, user: User) -> InsightsSummary:
    now = datetime.now(timezone.utc)
    month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    reviews = db.scalars(visible_reviews_query(user)).all()
    ids = [r.id for r in reviews]
    top: list[TopIssue] = []
    if ids:
        rows = db.execute(select(Finding.type, func.count()).where(Finding.review_id.in_(ids))
                          .group_by(Finding.type).order_by(func.count().desc(), Finding.type).limit(5)).all()
        top = [TopIssue(type=t, count=n) for t, n in rows]

    def ts(d):  # SQLite returns naive datetimes
        return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)

    return InsightsSummary(
        reviewed_this_month=sum(1 for r in reviews if ts(r.created_at) >= month_start),
        needs_attention=sum(1 for r in reviews if r.status in NEEDS_ATTENTION),
        awaiting_approval=sum(1 for r in reviews if r.status == "in_review"),
        approved_this_month=sum(1 for r in reviews if r.status == "approved" and ts(r.decided_at) and ts(r.decided_at) >= month_start),
        top_issues=top,
    )


def gaps(db: Session, user: User) -> list[InsightGap]:
    if user.role != "brand_admin":
        raise forbidden("Only brand admins can view guideline-gap insights.")
    counts: dict[str, int] = {}
    q = (select(Finding).join(Review, Review.id == Finding.review_id)
         .where(Review.workspace_id == user.workspace_id, Finding.source_id.is_not(None)))
    for f in db.scalars(q):
        d = db.scalar(select(FindingDecision).where(FindingDecision.finding_id == f.id).order_by(FindingDecision.pk.desc()).limit(1))
        if d is not None and d.action == "disputed":
            counts[f.source_id] = counts.get(f.source_id, 0) + 1
    titles = {s.public_id: s.title for s in db.scalars(select(SourceItem).where(SourceItem.workspace_id == user.workspace_id))}
    out = [InsightGap(source_id=sid, title=titles.get(sid, sid), dispute_count=n,
                      note="Often disputed; may be unclear." if n >= 5 else f"Disputed {n} time(s); consider clarifying the wording.")
           for sid, n in counts.items()]
    return sorted(out, key=lambda g: -g.dispute_count)


# ---- recurring issues ------------------------------------------------------------------------------
def patterns(db: Session, user: User, days: int = 90, min_reviews: int = 2) -> PatternsOut:
    """Groups findings across the reviews the caller can see. The grouping itself is in services/patterns.py."""
    if user.role not in ("manager", "brand_admin"):
        raise forbidden("Only managers and brand admins can view recurring-issue insights.")
    reviews = {r.id: r for r in db.scalars(visible_reviews_query(user))}
    findings: list[Finding] = []
    ids = list(reviews)
    for i in range(0, len(ids), 400):  # chunked so very large workspaces stay inside database parameter limits
        findings += db.scalars(select(Finding).where(Finding.review_id.in_(ids[i:i + 400]))).all()
    latest: dict[str, str] = {}
    fids = [f.id for f in findings]
    for i in range(0, len(fids), 400):
        for d in db.scalars(select(FindingDecision).where(FindingDecision.finding_id.in_(fids[i:i + 400])).order_by(FindingDecision.pk)):
            latest[d.finding_id] = d.action  # later rows overwrite earlier ones: the newest decision wins
    sources = {s.public_id: {"kind": s.kind, "title": s.title, "text": s.text}
               for s in db.scalars(select(SourceItem).where(SourceItem.workspace_id == user.workspace_id))}
    rows = [{"finding_id": f.id, "review_id": f.review_id, "creator_id": reviews[f.review_id].creator_id, "type": f.type,
             "severity": f.severity, "quote": f.span_text, "reason": f.reason, "source_id": f.source_id, "source_kind": f.source_kind,
             "source_version": f.source_version, "at": reviews[f.review_id].created_at, "decision": latest.get(f.id)} for f in findings]
    return PatternsOut(**build_patterns(rows, sources, days=days, min_reviews=min_reviews))
