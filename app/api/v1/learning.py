"""Learning routes: subjects -> topics -> content + per-student progress."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db import models
from app.db.session import get_db
from app.schemas import v1 as s
from app.services import learning_service
from app.services.profile_service import get_my_profile

router = APIRouter(tags=["learning"])


@router.get("/subjects/{subject_id}/topics", response_model=list[s.TopicOut])
def subject_topics(
    subject_id: str, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)
):
    profile = get_my_profile(db, user.id)
    return learning_service.list_topics(db, subject_id, profile.id)


@router.get("/topics/{topic_id}", response_model=s.TopicOut)
def topic_detail(
    topic_id: str, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)
):
    t = learning_service.get_topic(db, topic_id)
    profile = get_my_profile(db, user.id)
    mine = learning_service.list_topics(db, t.subject_id, profile.id)
    match = next((m for m in mine if m.id == topic_id), None)
    return match or s.TopicOut(
        id=t.id,
        subject_id=t.subject_id,
        title=t.title,
        description=t.description,
        order_index=t.order_index,
        content_count=len(t.content_items),
    )


@router.get("/topics/{topic_id}/content", response_model=list[s.ContentOut])
def topic_content(
    topic_id: str, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)
):
    get_my_profile(db, user.id)  # auth + membership gate
    t = learning_service.get_topic(db, topic_id)
    return sorted(t.content_items, key=lambda c: c.order_index)


@router.get("/profiles/me/progress", response_model=list[s.ProgressOut])
def read_progress(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    rows = learning_service.my_progress(db, user.id)
    return [s.ProgressOut(content_item_id=r.content_item_id, status=r.status) for r in rows]


@router.put("/profiles/me/progress/{content_id}", response_model=s.ProgressOut)
def write_progress(
    content_id: str,
    body: s.ProgressUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    row = learning_service.set_progress(db, user.id, content_id, body.status)
    return s.ProgressOut(content_item_id=row.content_item_id, status=row.status)


@router.get("/profiles/me/assessments", response_model=list[s.AssessmentSummary])
def my_assessments(
    subject_id: str | None = None,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    from app.services import assessment_service

    profile = get_my_profile(db, user.id)
    return assessment_service.list_assessments(db, profile.id, subject_id=subject_id)
