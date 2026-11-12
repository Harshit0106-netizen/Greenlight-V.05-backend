from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.asset import AssetOut, AssetReplace, AssetUpload
from app.services import workflow_service as wf

router = APIRouter(prefix="/reviews", tags=["assets"])


@router.get("/{review_id}/assets", response_model=list[AssetOut])
def list_assets(review_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return wf.list_assets(db, user, review_id)


@router.post("/{review_id}/assets", response_model=AssetOut, status_code=201)
def add_asset(review_id: str, body: AssetUpload, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return wf.add_asset(db, user, review_id, body)


@router.put("/{review_id}/assets/{asset_id}", response_model=AssetOut)
def replace_asset(review_id: str, asset_id: str, body: AssetReplace, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return wf.replace_asset(db, user, review_id, asset_id, body)


@router.get("/{review_id}/assets/{asset_id}/file")
def asset_file(review_id: str, asset_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    data, content_type = wf.asset_file(db, user, review_id, asset_id)
    return Response(content=data, media_type=content_type, headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"})
