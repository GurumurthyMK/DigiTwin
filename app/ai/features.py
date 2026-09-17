"""Feature engineering: one honest feature bundle per student, built only from
stored rows (twin recompute + submitted attempts + progress + goals). No external
data, no synthetic rows. Documented in docs/ai-models.md."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db import models
from app.services import twin_service


def build_features(db: Session, profile_id: str) -> dict:
    twin = twin_service.compute_profile_twin(db, profile_id)
    attempts = db.scalars(
        select(models.Attempt)
        .where(models.Attempt.profile_id == profile_id, models.Attempt.status == "submitted")
        .order_by(models.Attempt.submitted_at, models.Attempt.created_at)
        .options(selectinload(models.Attempt.assessment))
    ).all()
    accs = [a.accuracy for a in attempts if a.accuracy is not None]
    in_progress = db.scalar(
        select(func.count())
        .select_from(models.Attempt)
        .where(models.Attempt.profile_id == profile_id, models.Attempt.status == "in_progress")
    )
    overtime = sum(1 for a in attempts if a.overtime)
    goals = db.scalars(
        select(models.CareerGoal).where(models.CareerGoal.profile_id == profile_id)
    ).all()
    # Incomplete lessons on evidenced topics (study-next candidates with time costs).
    incomplete = db.scalars(
        select(models.ContentItem)
        .outerjoin(
            models.LearningProgress,
            (models.LearningProgress.content_item_id == models.ContentItem.id)
            & (models.LearningProgress.profile_id == profile_id),
        )
        .where(
            (models.LearningProgress.status.is_(None))
            | (models.LearningProgress.status != "completed")
        )
        .order_by(models.ContentItem.topic_id, models.ContentItem.order_index)
    ).all()
    # Per-topic recent answer outcomes (chronological, last 5): explanations must
    # cite the TOPIC's own recent work, never global accuracies from elsewhere.
    outcome_rows = db.execute(
        select(
            models.Assessment.topic_id,
            models.Answer.is_correct,
            models.Attempt.submitted_at,
            models.Answer.created_at,
        )
        .select_from(models.Answer)
        .join(models.Question, models.Question.id == models.Answer.question_id)
        .join(models.Attempt, models.Attempt.id == models.Answer.attempt_id)
        .join(models.Assessment, models.Assessment.id == models.Question.assessment_id)
        .where(models.Attempt.profile_id == profile_id, models.Attempt.status == "submitted")
        .order_by(models.Attempt.submitted_at, models.Answer.created_at)
    ).all()
    topic_recent: dict[str, list[bool]] = {}
    for tid, correct, _, _ in outcome_rows:
        if tid:
            topic_recent.setdefault(tid, []).append(bool(correct))
    topic_recent = {tid: seq[-5:] for tid, seq in topic_recent.items()}
    return {
        "twin": twin,
        "accuracies": accs,
        "topic_recent": topic_recent,
        "attempts": [
            {
                "id": a.id,
                "assessment_id": a.assessment_id,
                "title": a.assessment.title,
                "accuracy": a.accuracy,
                "overtime": a.overtime,
                "time_taken_seconds": a.time_taken_seconds,
                "attempt_number": a.attempt_number,
            }
            for a in attempts
        ],
        "in_progress_count": in_progress or 0,
        "overtime_count": overtime,
        "goals": [{"id": g.id, "title": g.title} for g in goals],
        "incomplete_lessons": [
            {
                "id": c.id,
                "topic_id": c.topic_id,
                "title": c.title,
                "duration_minutes": c.duration_minutes,
            }
            for c in incomplete
        ],
    }
