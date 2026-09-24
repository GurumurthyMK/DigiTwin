"""Set 3 grounded Mentor context: one centralized, server-side, profile-scoped
snapshot of everything the Mentor may claim.

Layers (never mixed):
- observed: rows that exist (attempts, answers, progress, goals, profile fields)
- derived: Twin recompute (mastery/confidence/trend/retention/analytics/evolution)
- recommendations: current adaptive plan + recent feedback outcomes
- interpretation: reserved for the engine — context carries NO opinions

The context is the ONLY data any provider (fallback or LLM) may use. It
contains no secrets, tokens, hashes, or other students' data, so it is safe
to hand to an external model provider verbatim.
"""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import models


def _utcnow() -> datetime:
    return datetime.now(UTC)


def build_mentor_context(db: Session, profile_id: str, now: datetime | None = None) -> dict:
    """Assemble the grounded context for one student. All queries filtered by
    profile_id — another student's rows can never enter."""
    from app.services import adaptive, retention
    from app.services import twin_analytics as analytics
    from app.services import twin_service

    now = now or _utcnow()

    profile = db.get(models.StudentProfile, profile_id)
    user_email = None
    display_name = ""
    if profile is not None:
        display_name = profile.full_name or ""
        user = db.get(models.User, profile.user_id) if profile.user_id else None
        user_email = user.email if user else None

    goals = db.scalars(
        select(models.CareerGoal).where(models.CareerGoal.profile_id == profile_id)
    ).all()

    attempts = db.scalars(
        select(models.Attempt)
        .where(models.Attempt.profile_id == profile_id, models.Attempt.status == "submitted")
        .order_by(models.Attempt.submitted_at, models.Attempt.created_at)
    ).all()
    recent_attempts = [
        {
            "assessment_id": a.assessment_id,
            "attempt_number": a.attempt_number,
            "score": a.score,
            "max_score": a.max_score,
            "accuracy": a.accuracy,
            "overtime": a.overtime,
            "submitted_at": a.submitted_at.isoformat() if a.submitted_at else None,
        }
        for a in attempts[-5:]
    ]

    progress_rows = db.scalars(
        select(models.LearningProgress).where(
            models.LearningProgress.profile_id == profile_id
        )
    ).all()
    progress = [
        {"content_item_id": p.content_item_id, "status": p.status} for p in progress_rows
    ]

    twin = twin_service.read_twin(db, profile_id, now=now)
    derived_analytics = analytics.build_analytics(db, profile_id, now=now)
    evolution = twin_service.list_evolution_events(db, profile_id, limit=10)
    fresh_map = retention.retention_for_profile(db, profile_id, now=now)
    plan = adaptive.build_adaptive_recommendations(db, profile_id, now=now)
    history = adaptive.feedback_history(db, profile_id, limit=10)

    # Display names only for skills this student actually touches (Twin rows +
    # retention keys + self-reports) — never a full-table scan.
    needed_ids = (
        {s["skill_id"] for s in twin["skills"]}
        | set(fresh_map.keys())
        | {
            link.skill_id
            for link in db.scalars(
                select(models.ProfileSkill).where(
                    models.ProfileSkill.profile_id == profile_id
                )
            ).all()
        }
    )
    skill_names: dict[str, str] = {}
    if needed_ids:
        for s in db.scalars(
            select(models.Skill).where(models.Skill.id.in_(sorted(needed_ids)))
        ).all():
            skill_names[s.id] = s.name

    observed = {
        "display_name": display_name,
        "email": user_email,
        "goals": [{"title": g.title, "status": g.status} for g in goals],
        "submitted_attempts": len(attempts),
        "recent_attempts": recent_attempts,
        "learning_progress": progress,
        "has_evidence": twin["has_evidence"],
    }
    derived = {
        "overall_mastery": twin["overall_mastery"],
        "overall_accuracy": twin["overall_accuracy"],
        "consistency": twin["consistency"],
        "trend_direction": twin["trend_direction"],
        "trend_slope": twin["trend_slope"],
        "attempts_count": twin["attempts_count"],
        "total_answers": twin["total_answers"],
        "correct_answers": twin["correct_answers"],
        "lessons_completed": twin["lessons_completed"],
        "subjects": twin["subjects"],
        "topics": twin["topics"],
        "skills": twin["skills"],
        "retention_by_skill": {
            sid: {
                "retention_state": f["retention_state"],
                "retention_score": f["retention_score"],
                "days_since_last_evidence": f["days_since_last_evidence"],
                "last_evidence_at": f["last_evidence_at"],
            }
            for sid, f in fresh_map.items()
        },
        "analytics": {
            "observed": derived_analytics["observed"],
            "derived": {
                "coverage": derived_analytics["derived"]["coverage"],
                "overall_trend": derived_analytics["derived"]["overall_trend"],
            },
            "interpretation": derived_analytics["interpretation"],
        },
        "recent_evolution": evolution,
        "latest_change": twin["latest_change"],
    }
    recommendations = {
        "today": plan["today"],
        "queue": plan["queue"],
        "engine": plan["engine"],
        "recent_feedback": history,
    }
    return {
        "profile_id": profile_id,
        "observed": observed,
        "derived": derived,
        "recommendations": recommendations,
        "skill_names": skill_names,
        "generated_at": now.isoformat(),
    }
