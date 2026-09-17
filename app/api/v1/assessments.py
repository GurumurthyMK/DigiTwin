"""Assessment engine routes: the attempt lifecycle is the ONLY path to a score."""

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session
from starlette import status

from app.api.deps import get_current_user
from app.db import models
from app.db.session import get_db
from app.schemas import v1 as s
from app.services import assessment_service, learning_service
from app.services.profile_service import get_my_profile

router = APIRouter(tags=["assessments"])


@router.get("/assessments/{assessment_id}", response_model=s.AssessmentDetail)
def assessment_detail(
    assessment_id: str, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)
):
    profile = get_my_profile(db, user.id)
    return assessment_service.get_assessment_detail(db, profile.id, assessment_id)


@router.get("/topics/{topic_id}/assessments", response_model=list[s.AssessmentSummary])
def topic_assessments(
    topic_id: str, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)
):
    profile = get_my_profile(db, user.id)
    learning_service.get_topic(db, topic_id)  # 404 gate
    return assessment_service.list_assessments(db, profile.id, topic_id=topic_id)


@router.post("/assessments/{assessment_id}/attempts", response_model=s.AttemptOut)
def start_attempt(
    assessment_id: str,
    response: Response,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    attempt, created = assessment_service.start_attempt(db, user.id, assessment_id)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return attempt


@router.get("/attempts/{attempt_id}", response_model=s.AttemptOut)
def read_attempt(
    attempt_id: str, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)
):
    return assessment_service.read_taking_attempt(db, user.id, attempt_id)


@router.put("/attempts/{attempt_id}/answers", response_model=s.AttemptOut)
def save_answers(
    attempt_id: str,
    body: s.AnswersUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    return assessment_service.save_answers(db, user.id, attempt_id, body)


@router.post("/attempts/{attempt_id}/submit", response_model=s.AttemptResultOut)
def submit_attempt(
    attempt_id: str, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)
):
    return assessment_service.submit_attempt(db, user.id, attempt_id)


@router.get("/attempts/{attempt_id}/result", response_model=s.AttemptResultOut)
def attempt_result(
    attempt_id: str, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)
):
    return assessment_service.read_result(db, user.id, attempt_id)


@router.get("/profiles/me/attempts", response_model=list[s.AttemptHistoryOut])
def attempt_history(
    limit: int = 50, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)
):
    return assessment_service.history(db, user.id, limit=min(max(limit, 1), 100))


@router.get("/profiles/me/performance", response_model=s.PerformanceOut)
def performance(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    return assessment_service.performance(db, user.id)
