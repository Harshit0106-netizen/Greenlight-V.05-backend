"""Connecting Greenlight to other systems: API keys, structured submissions, the publish check and webhooks.

Principles:
  - A key can only do what its scopes say, and every review it creates belongs to a named creator, so the audit
    trail still shows a person's account and the key's name.
  - Greenlight never publishes anything. The report says whether a human approved the exact copy; the other
    system decides what to do with that answer.
  - Webhooks carry ids, statuses and notes, never the draft text.
"""
import hashlib
import json
import logging
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import next_number
from app.errors import forbidden, invalid, not_found, unauthorized
from app.models.audit import Approval
from app.models.finding import Finding, FindingDecision
from app.models.integration import ApiKey, ReviewAsset, Webhook, WebhookDelivery
from app.models.review import Review
from app.models.user import User, utcnow
from app.schemas.asset import AssetUpload
from app.schemas.collab import Person
from app.schemas.integration import (ApiKeyCreate, ApiKeyCreated, ApiKeyOut, DeliveryOut, IntegrationReviewCreated, PublishReport,
                                     ReportAsset, ReportFinding, StructuredReviewCreate, WebhookCreate, WebhookCreated, WebhookOut)
from app.schemas.review import ReviewCreate
from app.services import brand_kit_service, integration_security as sec, structured
from app.services import workflow_service as wf

log = logging.getLogger("greenlight.integrations")


def _aware(dt: datetime | None) -> datetime | None:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _admin(user: User) -> None:
    if user.role != "brand_admin":
        raise forbidden("Only brand admins can manage integrations.")


# ---- API keys --------------------------------------------------------------------------------------
def _key_out(db: Session, k: ApiKey) -> ApiKeyOut:
    u = db.get(User, k.submit_as_user_id)
    return ApiKeyOut(id=k.id, name=k.name, prefix=k.prefix, scopes=list(k.scopes or []),
                     submit_as=Person(id=u.id, name=u.name, role=u.role) if u else None,
                     created_at=k.created_at, last_used_at=k.last_used_at, revoked_at=k.revoked_at)


def create_key(db: Session, user: User, body: ApiKeyCreate) -> ApiKeyCreated:
    _admin(user)
    owner = db.get(User, body.submit_as_user_id)
    if owner is None or owner.workspace_id != user.workspace_id or owner.role != "creator":
        raise invalid("submit_as_user_id must be a creator in this workspace.")
    plain, prefix, digest = sec.generate_api_key()
    key = ApiKey(id=f"K-{next_number(db, 'api_key', 1)}", workspace_id=user.workspace_id, name=body.name.strip(), prefix=prefix,
                 key_hash=digest, scopes=sorted(set(body.scopes)), submit_as_user_id=owner.id, created_by=user.id)
    db.add(key)
    db.commit()
    return ApiKeyCreated(**_key_out(db, key).model_dump(), key=plain)


def list_keys(db: Session, user: User) -> list[ApiKeyOut]:
    _admin(user)
    rows = db.scalars(select(ApiKey).where(ApiKey.workspace_id == user.workspace_id).order_by(ApiKey.created_at.desc(), ApiKey.id.desc())).all()
    return [_key_out(db, k) for k in rows]


def revoke_key(db: Session, user: User, key_id: str) -> ApiKeyOut:
    _admin(user)
    key = db.get(ApiKey, key_id)
    if key is None or key.workspace_id != user.workspace_id:
        raise not_found("API key not found.")
    if key.revoked_at is None:
        key.revoked_at = utcnow()
        db.commit()
    return _key_out(db, key)


# ---- structured submission and the publish report ---------------------------------------------------
def submit_structured(db: Session, key: ApiKey, body: StructuredReviewCreate) -> IntegrationReviewCreated:
    owner = db.get(User, key.submit_as_user_id)
    if owner is None or owner.role != "creator":
        raise forbidden("The creator account behind this API key can no longer submit drafts.")
    blocks = [b.model_dump(exclude_none=True) for b in body.blocks]
    uploads = [AssetUpload(**a.model_dump()) for a in body.assets]
    for i, b in enumerate(blocks):
        idx = b.get("asset_index")
        if idx is not None:
            if idx >= len(uploads):
                raise invalid(f"blocks[{i}].asset_index: there is no assets[{idx}].")
            if b.get("alt") and not uploads[idx].alt_text:
                uploads[idx] = uploads[idx].model_copy(update={"alt_text": b["alt"]})  # alt text can sit on the block
    try:
        text, block_map = structured.flatten(blocks)
    except structured.StructuredError as exc:
        raise invalid(str(exc))
    source = body.source.model_dump(exclude_none=True)
    meta = {"block_ids": [b["id"] for b in block_map], "source": source, "channel": body.channel, "key_id": key.id}
    via = {"via": "api", "source_system": source["system"], "api_key": key.name, **({"external_id": source["external_id"]} if source.get("external_id") else {})}
    created = wf.create_review(db, owner, ReviewCreate(title=body.title, content=text, intent_note=body.intent_note, assets=uploads),
                               structured=meta, via=via)
    return IntegrationReviewCreated(id=created.id, status=created.status, finding_count=created.finding_count,
                                    report_url=f"/integrations/reviews/{created.id}/report")


def build_report(db: Session, review: Review) -> PublishReport:
    content = wf._content(db, review)
    meta = review.structured or {}
    block_ids = meta.get("block_ids") or []
    decided = {d.finding_id: d for d in db.scalars(
        select(FindingDecision).join(Finding, Finding.id == FindingDecision.finding_id).where(Finding.review_id == review.id).order_by(FindingDecision.pk))}
    findings = []
    for f in db.scalars(select(Finding).where(Finding.review_id == review.id).order_by(Finding.id)):
        d = decided.get(f.id)
        findings.append(ReportFinding(
            finding_id=f.id, type=f.type, severity=f.severity, quote=f.span_text, asset_id=f.asset_id,
            block_id=structured.locate_block(content, block_ids, f.span_start) if (block_ids and not f.asset_id) else None,
            source={"kind": f.source_kind, "id": f.source_id, "version": f.source_version} if f.source_id else None, reason=f.reason,
            decision={"action": d.action, "reason": d.reason} if d else None))
    unresolved = wf._unresolved(db, review.id)
    blockers: list[str] = []
    if review.status != "approved":
        if unresolved:
            blockers.append(f"{unresolved} finding{'s' if unresolved != 1 else ''} still need a decision from the creator.")
        if review.status == "changes_requested":
            blockers.append("A reviewer asked for changes.")
        if review.status == "in_review":
            blockers.append("Waiting for a reviewer to approve.")
        if not blockers:
            blockers.append("The review is not approved yet.")
    approval = db.scalar(select(Approval).where(Approval.review_id == review.id, Approval.decision == "approved").order_by(Approval.pk.desc()).limit(1))
    approver = db.get(User, approval.actor_id) if approval else None
    assets = [ReportAsset(id=a.id, filename=a.filename, alt_text=a.alt_text, sha256=a.sha256, width=a.width, height=a.height)
              for a in db.scalars(select(ReviewAsset).where(ReviewAsset.review_id == review.id).order_by(ReviewAsset.id))]
    return PublishReport(
        id=review.id, title=review.title, status=review.status, ready_to_publish=review.status == "approved", blockers=blockers,
        approved_by=Person(id=approver.id, name=approver.name, role=approver.role) if approver else None,
        approved_at=approval.created_at if approval else None, brand_kit_version=brand_kit_service.label(review.brand_kit_version),
        content_sha256=hashlib.sha256(content.encode()).hexdigest(), content=content, source=meta.get("source"), findings=findings, assets=assets)


def report_for_user(db: Session, user: User, review_id: str) -> PublishReport:
    return build_report(db, wf.load_review(db, user, review_id))


def report_for_key(db: Session, key: ApiKey, review_id: str) -> PublishReport:
    review = db.get(Review, review_id)
    if review is None or review.workspace_id != key.workspace_id:
        raise not_found("Review not found.")
    return build_report(db, review)


# ---- webhooks --------------------------------------------------------------------------------------
def _allow_private() -> bool:
    return get_settings().app_env != "production"


def _hook_out(db: Session, h: Webhook) -> WebhookOut:
    last = db.scalar(select(WebhookDelivery.status).where(WebhookDelivery.webhook_id == h.id).order_by(WebhookDelivery.pk.desc()).limit(1))
    return WebhookOut(id=h.id, url=h.url, events=list(h.events or []), active=h.active, created_at=h.created_at, last_status=last)


def create_webhook(db: Session, user: User, body: WebhookCreate) -> WebhookCreated:
    _admin(user)
    problem = sec.check_webhook_url(body.url.strip(), _allow_private())
    if problem:
        raise invalid(problem)
    hook = Webhook(id=f"WH-{next_number(db, 'webhook', 1)}", workspace_id=user.workspace_id, url=body.url.strip(),
                   events=sorted(set(body.events)), secret=sec.generate_secret(), created_by=user.id)
    db.add(hook)
    db.commit()
    return WebhookCreated(**_hook_out(db, hook).model_dump(), secret=hook.secret)


def list_webhooks(db: Session, user: User) -> list[WebhookOut]:
    _admin(user)
    return [_hook_out(db, h) for h in db.scalars(select(Webhook).where(Webhook.workspace_id == user.workspace_id).order_by(Webhook.created_at.desc(), Webhook.id.desc()))]


def revoke_webhook(db: Session, user: User, hook_id: str) -> WebhookOut:
    _admin(user)
    hook = db.get(Webhook, hook_id)
    if hook is None or hook.workspace_id != user.workspace_id:
        raise not_found("Webhook not found.")
    hook.active = False
    db.commit()
    return _hook_out(db, hook)


def send_http(url: str, body: bytes, headers: dict) -> int:
    """One POST, short timeout, no redirects (a redirect could point at an internal address)."""
    return httpx.post(url, content=body, headers=headers, timeout=3.0, follow_redirects=False).status_code


def _deliver(db: Session, hook: Webhook, event: str, payload: dict, review_id: str | None) -> WebhookDelivery:
    body = json.dumps(payload, separators=(",", ":")).encode()
    ts = str(int(time.time()))
    headers = {"Content-Type": "application/json", "User-Agent": "Greenlight-Webhooks/1", "X-Greenlight-Event": event,
               "X-Greenlight-Timestamp": ts, "X-Greenlight-Signature": sec.sign(hook.secret, ts, body)}
    started = time.monotonic()
    status, http_status, error = "failed", None, ""
    problem = sec.check_webhook_url(hook.url, _allow_private())  # checked again now: DNS can change after registration
    if problem:
        error = problem
    else:
        try:
            http_status = send_http(hook.url, body, headers)
            status = "delivered" if 200 <= http_status < 300 else "failed"
            error = "" if status == "delivered" else f"The receiver answered HTTP {http_status}."
        except Exception as exc:  # noqa: BLE001 - any network problem is recorded, never raised
            error = f"{type(exc).__name__}: {exc}"[:300]
    row = WebhookDelivery(workspace_id=hook.workspace_id, webhook_id=hook.id, review_id=review_id, event=event, status=status,
                          http_status=http_status, error=error, duration_ms=int((time.monotonic() - started) * 1000))
    db.add(row)
    db.commit()
    return row


def emit(db: Session, review: Review, event: str, actor: User | None, note: str = "") -> None:
    hooks = [h for h in db.scalars(select(Webhook).where(Webhook.workspace_id == review.workspace_id, Webhook.active.is_(True)))
             if event in (h.events or [])]
    if not hooks:
        return
    meta = review.structured or {}
    payload = {"event": event, "delivered_at": datetime.now(timezone.utc).isoformat(),
               "review": {"id": review.id, "title": review.title, "status": review.status,
                          "brand_kit_version": brand_kit_service.label(review.brand_kit_version), "source": meta.get("source")},
               "actor": {"id": actor.id, "name": actor.name, "role": actor.role} if actor else None, "note": note,
               "report_url": f"/integrations/reviews/{review.id}/report"}
    for h in hooks:
        _deliver(db, h, event, payload, review.id)


def test_webhook(db: Session, user: User, hook_id: str) -> DeliveryOut:
    _admin(user)
    hook = db.get(Webhook, hook_id)
    if hook is None or hook.workspace_id != user.workspace_id:
        raise not_found("Webhook not found.")
    row = _deliver(db, hook, "ping", {"event": "ping", "delivered_at": datetime.now(timezone.utc).isoformat(), "workspace_id": hook.workspace_id}, None)
    return _delivery_out(row)


def _delivery_out(r: WebhookDelivery) -> DeliveryOut:
    return DeliveryOut(id=r.pk, webhook_id=r.webhook_id, event=r.event, review_id=r.review_id, status=r.status, http_status=r.http_status,
                       error=r.error or "", attempted_at=r.attempted_at, duration_ms=r.duration_ms)


def list_deliveries(db: Session, user: User, limit: int = 30) -> list[DeliveryOut]:
    _admin(user)
    rows = db.scalars(select(WebhookDelivery).where(WebhookDelivery.workspace_id == user.workspace_id)
                      .order_by(WebhookDelivery.pk.desc()).limit(max(1, min(limit, 200))))
    return [_delivery_out(r) for r in rows]
