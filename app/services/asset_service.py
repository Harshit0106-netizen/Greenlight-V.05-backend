"""Images attached to a review: validation, the checks, and turning problems into findings.

Image findings are ordinary findings (type `accessibility` or `visual`, rule-based, tied to an approved source) with
`asset_id` set. They go through the same decisions and the same state machine as text findings. They are listed
with the image (GET /reviews/{id}/assets) and not in GET /reviews/{id}/findings, so that endpoint keeps its shape.
"""
import base64
import binascii
import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import next_number
from app.errors import invalid, not_found
from app.models.finding import Finding, FindingDecision
from app.models.integration import ReviewAsset
from app.models.review import Review
from app.models.user import User, utcnow
from app.pipeline import SourceView, visual
from app.pipeline.rules import _guideline, _ref, palette
from app.schemas.asset import AssetOut, AssetReplace, AssetUpload
from app.services import audit_log


class Prepared:
    """An upload that passed validation and has been analysed, not yet stored."""

    def __init__(self, filename: str, content_type: str, data: bytes, alt_text: str, info: dict):
        self.filename, self.content_type, self.data, self.alt_text, self.info = filename, content_type, data, alt_text, info


def prepare(upload: AssetUpload | AssetReplace, kit: list[SourceView], filename: str | None = None, alt_text: str | None = None) -> Prepared:
    try:
        data = base64.b64decode(upload.data_base64, validate=True)
    except (binascii.Error, ValueError):
        raise invalid("data_base64: the image data is not valid base64.")
    if not data:
        raise invalid("data_base64: the image is empty.")
    if len(data) > visual.MAX_IMAGE_BYTES:
        raise invalid(f"The image is larger than {visual.MAX_IMAGE_BYTES // (1024 * 1024)} MB. Export a smaller copy.")
    approved, _ = palette(kit)
    try:
        info = visual.analyze(data, approved)
    except ValueError as exc:
        raise invalid(str(exc))
    if visual.ALLOWED_TYPES.get(upload.content_type) != info["format"]:
        raise invalid("The file does not match its content_type.")
    name = (filename or getattr(upload, "filename", None) or "image").strip()[:200] or "image"
    alt = " ".join((alt_text if alt_text is not None else getattr(upload, "alt_text", "") or "").split())
    return Prepared(name, upload.content_type, data, alt, info)


def _add_finding(db: Session, review: Review, asset: ReviewAsset, ftype: str, severity: str, reason: str,
                 confidence: float, source: dict | None) -> None:
    db.add(Finding(
        id=f"FD-{next_number(db, 'finding', 1001)}", review_id=review.id, asset_id=asset.id, type=ftype, severity=severity,
        span_start=0, span_end=0, span_text=asset.filename,
        source_kind=source["kind"] if source else None, source_id=source["id"] if source else None,
        source_version=source["version"] if source else None, fact_match=None, reason=reason,
        confidence=confidence, method="rule_based", suggestion=None))


def create_findings(db: Session, review: Review, asset: ReviewAsset, info: dict, kit: list[SourceView], only: tuple[str, ...] = ("alt", "palette")) -> int:
    """Runs the image checks and stores a finding for each problem. Returns how many were created."""
    created = 0
    if "alt" in only:
        problem = visual.alt_problem(asset.alt_text)
        if problem:
            src = _ref(_guideline(kit, ("accessib", "alt text", "contrast", "wcag")))  # accessibility findings may be sourceless
            _add_finding(db, review, asset, "accessibility", "medium", f"{problem} Describe what the image shows for people who cannot see it.", 0.95, src)
            created += 1
    if "palette" in only and info.get("palette_issue"):
        _, holder = palette(kit)
        issue = info["palette_issue"]
        if holder is not None:  # no approved palette, no finding
            _add_finding(db, review, asset, "visual", issue["severity"],
                         f"About {round(issue['share'] * 100)}% of this image is {issue['colour']}, which is not in the approved brand palette. "
                         "This is an estimate from a small copy of the image; check it by eye.", 0.72, _ref(holder))
            created += 1
    return created


def store(db: Session, user: User, review: Review, prepared: Prepared, kit: list[SourceView]) -> tuple[ReviewAsset, int]:
    asset = ReviewAsset(
        id=f"AS-{next_number(db, 'asset', 1)}", review_id=review.id, filename=prepared.filename, content_type=prepared.content_type,
        size_bytes=len(prepared.data), width=prepared.info["width"], height=prepared.info["height"],
        sha256=hashlib.sha256(prepared.data).hexdigest(), alt_text=prepared.alt_text, data=prepared.data, version=1,
        palette_checked=prepared.info["palette_checked"], created_by=user.id)
    db.add(asset)
    db.flush()
    audit_log.audit(db, user, review.workspace_id, review.id, "asset_added",
                    {"asset_id": asset.id, "filename": asset.filename, "width": asset.width, "height": asset.height})
    return asset, create_findings(db, review, asset, prepared.info, kit)


def load_asset(db: Session, review: Review, asset_id: str) -> ReviewAsset:
    asset = db.get(ReviewAsset, asset_id)
    if asset is None or asset.review_id != review.id:
        raise not_found("Image not found.")
    return asset


def count_for(db: Session, review_id: str) -> int:
    return len(db.scalars(select(ReviewAsset.id).where(ReviewAsset.review_id == review_id)).all())


def replace(db: Session, user: User, review: Review, asset: ReviewAsset, prepared: Prepared, alt_given: bool, kit: list[SourceView]) -> int:
    """Swap the image bytes (and optionally the alt text), close findings the new image fixes, and re-run the checks.
    Returns how many new findings were created."""
    def latest(fid: str) -> FindingDecision | None:
        return db.scalar(select(FindingDecision).where(FindingDecision.finding_id == fid).order_by(FindingDecision.pk.desc()).limit(1))

    before_name = asset.filename
    asset.filename, asset.content_type, asset.data = prepared.filename, prepared.content_type, prepared.data
    asset.size_bytes, asset.width, asset.height = len(prepared.data), prepared.info["width"], prepared.info["height"]
    asset.sha256, asset.palette_checked, asset.version = hashlib.sha256(prepared.data).hexdigest(), prepared.info["palette_checked"], asset.version + 1
    asset.updated_at = utcnow()
    if alt_given:
        asset.alt_text = prepared.alt_text

    own = db.scalars(select(Finding).where(Finding.asset_id == asset.id)).all()
    for f in own:
        if f.span_text != asset.filename:
            f.span_text = asset.filename
        d = latest(f.id)
        if d is not None and d.action != "accepted":
            continue  # already edited or disputed: keep that decision
        if f.type == "visual":  # the picture changed, so the old colour finding is superseded
            db.add(FindingDecision(finding_id=f.id, action="edited", reason="Replaced the image.", actor_id=user.id))
        elif f.type == "accessibility" and alt_given and not visual.alt_problem(asset.alt_text):
            db.add(FindingDecision(finding_id=f.id, action="edited", reason="Updated the alt text with the new image.", actor_id=user.id))
    db.flush()

    open_types = {f.type for f in own if (lambda d: d is None or d.action == "accepted")(latest(f.id))}
    only = tuple(k for k, t in (("alt", "accessibility"), ("palette", "visual")) if t not in open_types or k == "palette")
    created = create_findings(db, review, asset, prepared.info, kit, only=only)
    audit_log.audit(db, user, review.workspace_id, review.id, "asset_replaced",
                    {"asset_id": asset.id, "filename": asset.filename, "previous_filename": before_name, "version": asset.version})
    return created


def apply_alt_edit(db: Session, user: User, review: Review, finding: Finding, asset: ReviewAsset, new_alt: str | None) -> None:
    """`edited` on an image finding: only accessibility findings can be edited (the new text is the alt text)."""
    if finding.type != "accessibility":
        raise invalid("This finding is about the image itself. Replace the image, or mark it not applicable and say why.")
    new = " ".join((new_alt or "").split())
    if not new:
        raise invalid("edited_text is required when action is 'edited'.")
    problem = visual.alt_problem(new)
    if problem:
        raise invalid(f"{problem} Describe what the image shows.")
    before = asset.alt_text
    asset.alt_text = new
    asset.updated_at = utcnow()
    audit_log.audit(db, user, review.workspace_id, review.id, "asset_alt_updated",
                    {"asset_id": asset.id, "filename": asset.filename, "before": before, "after": new})


def to_out(db: Session, asset: ReviewAsset, findings_out) -> AssetOut:
    return AssetOut(id=asset.id, filename=asset.filename, content_type=asset.content_type, size_bytes=asset.size_bytes,
                    width=asset.width, height=asset.height, alt_text=asset.alt_text, version=asset.version,
                    palette_checked=asset.palette_checked, created_at=asset.created_at, findings=findings_out)
