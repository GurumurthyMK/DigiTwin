"""Product routes: dashboard overview bundle, mentor explainer, notifications.

Overview exists to keep critical screens at ONE round-trip (measured: dashboard
needed 4+ parallel calls). Everything here reads; nothing here writes student
state except notification read-state and prefs.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import engine, features, mentor
from app.api.deps import get_current_user
from app.db import models
from app.db.session import get_db
from app.schemas import v1 as s
from app.services import notify_service
from app.services.profile_service import get_my_profile

router = APIRouter(tags=["product"])


@router.get("/profiles/me/overview", response_model=s.OverviewOut)
def overview(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    profile = get_my_profile(db, user.id)
    feats = features.build_features(db, profile.id)
    tw = feats["twin"]
    plan = engine.recommendations(feats)
    urgent = [i for i in engine.insights(feats) if i["priority"] <= 2][:3]
    live = db.scalars(
        select(models.Attempt)
        .where(models.Attempt.profile_id == profile.id, models.Attempt.status == "in_progress")
        .order_by(models.Attempt.created_at.desc())
        .limit(3)
    ).all()
    latest = db.scalars(
        select(models.TwinSnapshot)
        .where(models.TwinSnapshot.profile_id == profile.id)
        .order_by(models.TwinSnapshot.created_at.desc())
        .limit(1)
    ).first()
    return s.OverviewOut(
        display_name=profile.full_name or user.email,
        onboarding_completed=profile.onboarding_completed,
        overall_mastery=tw["overall_mastery"],
        overall_accuracy=tw["overall_accuracy"],
        trend_direction=tw["trend_direction"],
        latest_change=latest.summary if latest else None,
        attempts_count=tw["attempts_count"],
        lessons_completed=tw["lessons_completed"],
        today=plan["today"],
        urgent=urgent,
        in_progress=[
            s.OverviewAttemptOut(
                id=a.id, assessment_title=a.assessment.title, attempt_number=a.attempt_number
            )
            for a in live
        ],
        unread_notifications=notify_service.unread_count(db, profile.id),
    )


@router.get("/profiles/me/mentor/questions", response_model=s.MentorQuestionsOut)
def mentor_questions():
    return s.MentorQuestionsOut(
        questions=[s.MentorFollowUp(key=k, question=q) for k, q in mentor.QUESTIONS]
    )


@router.get("/profiles/me/mentor/answers", response_model=s.MentorAnswerOut)
def mentor_answer(
    question: str, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)
):
    profile = get_my_profile(db, user.id)
    return mentor.answer(features.build_features(db, profile.id), question)


@router.get("/profiles/me/notifications", response_model=list[s.NotificationOut])
def notifications(
    limit: int = 30, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)
):
    profile = get_my_profile(db, user.id)
    return notify_service.list_all(db, profile.id, limit=min(max(limit, 1), 100))


@router.put("/profiles/me/notifications/{notification_id}/read")
def notification_read(
    notification_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    profile = get_my_profile(db, user.id)
    if notification_id.startswith("live-"):
        return {"ok": True, "live": True}  # live reminders aren't stored; nothing to mark
    from app.core.errors import AppError

    if not notify_service.mark_read(db, profile.id, notification_id):
        raise AppError("not_found", "Notification not found.", 404)
    return {"ok": True}


@router.get("/profiles/me/notification-prefs", response_model=s.NotificationPrefsOut)
def notification_prefs(
    db: Session = Depends(get_db), user: models.User = Depends(get_current_user)
):
    profile = get_my_profile(db, user.id)
    return notify_service.get_prefs(db, profile.id)


@router.put("/profiles/me/notification-prefs", response_model=s.NotificationPrefsOut)
def notification_prefs_update(
    body: s.NotificationPrefsIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    profile = get_my_profile(db, user.id)
    return notify_service.set_prefs(db, profile.id, **body.model_dump())
