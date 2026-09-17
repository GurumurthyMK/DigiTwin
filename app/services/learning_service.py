"""Learning-domain service: topics, content, progress. Read-mostly for students."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import AppError
from app.db import models
from app.schemas import v1 as s


def list_topics(db: Session, subject_id: str, profile_id: str | None = None) -> list[s.TopicOut]:
    subject = db.get(models.Subject, subject_id)
    if not subject:
        raise AppError("not_found", "Subject not found.", 404)
    topics = db.scalars(
        select(models.Topic)
        .where(models.Topic.subject_id == subject_id)
        .order_by(models.Topic.order_index)
        .options(selectinload(models.Topic.content_items))
    ).all()
    done_by_topic: dict[str, int] = {}
    if profile_id and topics:
        all_ids = [c.id for t in topics for c in t.content_items]
        if all_ids:
            rows = db.execute(
                select(models.ContentItem.topic_id, func.count())
                .join(
                    models.LearningProgress,
                    models.LearningProgress.content_item_id == models.ContentItem.id,
                )
                .where(
                    models.LearningProgress.profile_id == profile_id,
                    models.LearningProgress.content_item_id.in_(all_ids),
                    models.LearningProgress.status == "completed",
                )
                .group_by(models.ContentItem.topic_id)
            ).all()
            done_by_topic = dict(rows)
    out = []
    for t in topics:
        out.append(
            s.TopicOut(
                id=t.id,
                subject_id=t.subject_id,
                title=t.title,
                description=t.description,
                order_index=t.order_index,
                content_count=len(t.content_items),
                completed_count=done_by_topic.get(t.id, 0),
            )
        )
    return out


def get_topic(db: Session, topic_id: str) -> models.Topic:
    topic = db.get(models.Topic, topic_id)
    if not topic:
        raise AppError("not_found", "Topic not found.", 404)
    return topic


def set_progress(
    db: Session, user_id: str, content_id: str, status: str
) -> models.LearningProgress:
    from app.services.profile_service import get_my_profile

    profile = get_my_profile(db, user_id)
    item = db.get(models.ContentItem, content_id)
    if not item:
        raise AppError("not_found", "Content item not found.", 404)
    row = db.scalar(
        select(models.LearningProgress).where(
            models.LearningProgress.profile_id == profile.id,
            models.LearningProgress.content_item_id == content_id,
        )
    )
    if row:
        row.status = status
    else:
        row = models.LearningProgress(
            profile_id=profile.id, content_item_id=content_id, status=status
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    return row


def my_progress(db: Session, user_id: str) -> list[models.LearningProgress]:
    from app.services.profile_service import get_my_profile

    profile = get_my_profile(db, user_id)
    return db.scalars(
        select(models.LearningProgress).where(models.LearningProgress.profile_id == profile.id)
    ).all()
