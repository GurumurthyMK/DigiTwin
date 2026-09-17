"""AI insight routes: read-only, deterministic views over twin evidence.

Nothing here trains, stores, or invents data. Every response cites evidence
rows or declares insufficiency. Engine version is stamped on outputs.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.ai import engine, features
from app.api.deps import get_current_user
from app.db import models
from app.db.session import get_db
from app.schemas import v1 as s
from app.services.profile_service import get_my_profile

router = APIRouter(tags=["insights"])


def _features(db: Session, user: models.User) -> dict:
    profile = get_my_profile(db, user.id)
    return features.build_features(db, profile.id)


@router.get("/profiles/me/insights", response_model=list[s.InsightOut])
def list_insights(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    return engine.insights(_features(db, user))


@router.get("/profiles/me/prediction", response_model=s.PredictionOut)
def prediction(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    return engine.predict(_features(db, user))


@router.get("/profiles/me/recommendations", response_model=s.StudyPlanOut)
def recommendations(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    return engine.recommendations(_features(db, user))


@router.get("/profiles/me/career", response_model=s.CareerOut)
def career(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    return engine.career_matches(_features(db, user))
