from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_scope
from app.models.integration import ApiKey
from app.models.user import User
from app.schemas.integration import (ApiKeyCreate, ApiKeyCreated, ApiKeyOut, DeliveryOut, IntegrationReviewCreated, PublishReport,
                                     StructuredReviewCreate, WebhookCreate, WebhookCreated, WebhookOut)
from app.services import integration_service as svc

router = APIRouter(prefix="/integrations", tags=["integrations"])


# ---- for other systems: authenticate with X-API-Key ----------------------------------------------
@router.post("/reviews", response_model=IntegrationReviewCreated, status_code=201)
def submit(body: StructuredReviewCreate, key: ApiKey = Depends(require_scope("reviews:write")), db: Session = Depends(get_db)):
    return svc.submit_structured(db, key, body)


@router.get("/reviews/{review_id}/report", response_model=PublishReport)
def report(review_id: str, key: ApiKey = Depends(require_scope("reviews:read")), db: Session = Depends(get_db)):
    return svc.report_for_key(db, key, review_id)


# ---- for brand admins: authenticate with a normal login token ------------------------------------
@router.get("/api-keys", response_model=list[ApiKeyOut])
def list_keys(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return svc.list_keys(db, user)


@router.post("/api-keys", response_model=ApiKeyCreated, status_code=201)
def create_key(body: ApiKeyCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return svc.create_key(db, user, body)


@router.post("/api-keys/{key_id}/revoke", response_model=ApiKeyOut)
def revoke_key(key_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return svc.revoke_key(db, user, key_id)


@router.get("/webhooks", response_model=list[WebhookOut])
def list_webhooks(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return svc.list_webhooks(db, user)


@router.post("/webhooks", response_model=WebhookCreated, status_code=201)
def create_webhook(body: WebhookCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return svc.create_webhook(db, user, body)


@router.post("/webhooks/{hook_id}/revoke", response_model=WebhookOut)
def revoke_webhook(hook_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return svc.revoke_webhook(db, user, hook_id)


@router.post("/webhooks/{hook_id}/test", response_model=DeliveryOut)
def test_webhook(hook_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return svc.test_webhook(db, user, hook_id)


@router.get("/deliveries", response_model=list[DeliveryOut])
def deliveries(limit: int = Query(30, ge=1, le=200), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return svc.list_deliveries(db, user, limit)
