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
    """Adaptive plan (Set 2): Twin-driven via adaptive service. Same shape as
    before + rec_key/status/is_stale per rec; legacy clients ignore extras."""
    from app.services import adaptive

    profile = get_my_profile(db, user.id)
    return adaptive.build_adaptive_recommendations(db, profile.id)


@router.post("/profiles/me/recommendations/feedback", response_model=s.RecommendationFeedbackOut)
def recommendation_feedback(
    body: s.RecommendationFeedbackIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Record accepted/started/completed/dismissed/skipped for one rec.

    Profile-scoped; refs validated server-side. Append-only (history kept).
    """
    import json as _json

    from app.services import adaptive

    profile = get_my_profile(db, user.id)
    row = adaptive.record_feedback(
        db, profile.id, body.rec_key, body.kind, body.refs or {}, body.action,
        title=body.title,
    )
    db.commit()
    db.refresh(row)
    return s.RecommendationFeedbackOut(
        id=row.id, rec_key=row.rec_key, kind=row.kind, title=row.title,
        refs=_json.loads(row.refs_json or "{}"), status=row.status,
        created_at=row.created_at.isoformat() if row.created_at else None,
    )


@router.get("/profiles/me/recommendations/history", response_model=list[s.RecommendationHistoryOut])
def recommendation_history(
    limit: int = 50, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)
):
    from app.services import adaptive

    profile = get_my_profile(db, user.id)
    return adaptive.feedback_history(db, profile.id, limit=limit)


@router.get("/profiles/me/career", response_model=s.CareerOut)
def career(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    return engine.career_matches(_features(db, user))
