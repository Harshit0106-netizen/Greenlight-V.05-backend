from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.patterns import PatternsOut
from app.schemas.review import InsightGap, InsightsSummary
from app.services import insights_service

router = APIRouter(prefix="/insights", tags=["insights"])


@router.get("/summary", response_model=InsightsSummary)
def summary(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return insights_service.summary(db, user)


@router.get("/gaps", response_model=list[InsightGap])
def gaps(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return insights_service.gaps(db, user)


@router.get("/patterns", response_model=PatternsOut)
def patterns(days: int = Query(90, ge=7, le=365), min_reviews: int = Query(2, ge=2, le=20),
             user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return insights_service.patterns(db, user, days, min_reviews)
