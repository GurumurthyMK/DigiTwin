"""Digital twin routes: read-only views over derived state.

The twin is written only by the submit pipeline (assessment_service);
clients can never POST twin state — it is evidence, not input.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db import models
from app.db.session import get_db
from app.schemas import v1 as s
from app.services import twin_service
from app.services.profile_service import get_my_profile

router = APIRouter(tags=["twin"])


@router.get("/profiles/me/twin", response_model=s.TwinOut)
def read_twin(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    profile = get_my_profile(db, user.id)
    return twin_service.read_twin(db, profile.id)


@router.get("/profiles/me/twin/analytics", response_model=s.TwinAnalyticsOut)
def twin_analytics(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    from app.services import twin_analytics

    profile = get_my_profile(db, user.id)
    return twin_analytics.build_analytics(db, profile.id)


# Alias: GET /api/v1/twin/analytics (same semantics, same scoping).
@router.get("/twin/analytics", response_model=s.TwinAnalyticsOut)
def twin_analytics_alias(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    from app.services import twin_analytics

    profile = get_my_profile(db, user.id)
    return twin_analytics.build_analytics(db, profile.id)


@router.get("/profiles/me/twin/snapshots", response_model=list[s.TwinSnapshotOut])
def twin_snapshots(
    limit: int = 20, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)
):
    profile = get_my_profile(db, user.id)
    return twin_service.list_snapshots(db, profile.id, limit=min(max(limit, 1), 100))


@router.get("/profiles/me/twin/evolution", response_model=list[s.TwinEvolutionEventOut])
def twin_evolution(
    limit: int = 50, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)
):
    profile = get_my_profile(db, user.id)
    return twin_service.list_evolution_events(db, profile.id, limit=limit)


# Spec alias: GET /api/v1/twin/evolution (same semantics, same scoping).
@router.get("/twin/evolution", response_model=list[s.TwinEvolutionEventOut])
def twin_evolution_alias(
    limit: int = 50, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)
):
    profile = get_my_profile(db, user.id)
    return twin_service.list_evolution_events(db, profile.id, limit=limit)
