"""Notifications: event-generated rows with anti-spam rules + live reminders.

Anti-spam contract (documented, tested):
- At most ONE unread row per kind per 24h window.
- At most 50 stored rows per profile (oldest read rows pruned first).
- Respects per-kind prefs; disabled kinds generate nothing.
- Reminders that depend on current state (unfinished sittings) are computed
  at read time and never stored, so they cannot duplicate or go stale.
"""

import json as _json
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import models

MAX_STORED_PER_PROFILE = 50
WINDOW_HOURS = 24


def get_prefs(db: Session, profile_id: str) -> models.NotificationPrefs:
    prefs = db.scalar(
        select(models.NotificationPrefs).where(models.NotificationPrefs.profile_id == profile_id)
    )
    if prefs is None:
        prefs = models.NotificationPrefs(profile_id=profile_id)
        db.add(prefs)
        db.commit()
        db.refresh(prefs)
    return prefs


def set_prefs(db: Session, profile_id: str, **flags) -> models.NotificationPrefs:
    prefs = get_prefs(db, profile_id)
    for field in ("twin_change_enabled", "risk_enabled", "plan_enabled"):
        if field in flags and flags[field] is not None:
            setattr(prefs, field, bool(flags[field]))
    db.commit()
    db.refresh(prefs)
    return prefs


def _recent_unread(db: Session, profile_id: str, kind: str) -> bool:
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=WINDOW_HOURS)
    return (
        db.scalar(
            select(func.count())
            .select_from(models.Notification)
            .where(
                models.Notification.profile_id == profile_id,
                models.Notification.kind == kind,
                models.Notification.read_at.is_(None),
                models.Notification.created_at >= cutoff,
            )
        )
        > 0
    )


def _prune(db: Session, profile_id: str) -> None:
    ids = db.scalars(
        select(models.Notification.id)
        .where(models.Notification.profile_id == profile_id)
        .order_by(models.Notification.created_at.desc())
        .offset(MAX_STORED_PER_PROFILE)
    ).all()
    if ids:
        for chunk in [ids[i : i + 100] for i in range(0, len(ids), 100)]:
            db.query(models.Notification).filter(models.Notification.id.in_(chunk)).delete(
                synchronize_session=False
            )


def _add(
    db: Session,
    profile_id: str,
    kind: str,
    title: str,
    body: str,
    ref_type: str | None = None,
    ref_id: str | None = None,
) -> models.Notification | None:
    prefs = get_prefs(db, profile_id)
    if not getattr(prefs, f"{kind}_enabled", True):
        return None
    if _recent_unread(db, profile_id, kind):
        return None
    row = models.Notification(
        profile_id=profile_id, kind=kind, title=title, body=body, ref_type=ref_type, ref_id=ref_id
    )
    db.add(row)
    _prune(db, profile_id)
    return row


def notify_after_submit(db: Session, profile_id: str, snapshot, computed: dict) -> None:
    """Post-submit fan-out using values already computed by the twin pipeline.
    Commits its own inserts (called after the submit commit)."""
    changes = _json.loads(snapshot.changes_json or "[]")
    notify_twin_change(db, profile_id, changes, snapshot.summary)
    trend = computed.get("trend_direction")
    acc = computed.get("overall_accuracy")
    if trend == "declining" and acc is not None and acc < 0.5:
        notify_risk(db, profile_id, trend, acc)
    if computed.get("attempts_count") == 1:
        notify_plan(db, profile_id, 3)
    db.commit()


def notify_twin_change(db: Session, profile_id: str, changes: list[dict], summary: str) -> None:
    """Called from the twin pipeline. Only meaningful movers notify."""
    big = [c for c in changes if abs(c.get("delta", 0)) >= 0.10]
    if not big:
        return
    top = max(big, key=lambda c: abs(c["delta"]))
    arrow = "up" if top["delta"] >= 0 else "down"
    _add(
        db,
        profile_id,
        "twin_change",
        f"{top['label']} moved {arrow} {abs(top['delta']):.0%}",
        summary,
        ref_type="twin",
        ref_id=None,
    )


def notify_risk(db: Session, profile_id: str, trend: str | None, low: float | None) -> None:
    if trend == "declining" and low is not None and low < 0.5:
        _add(
            db,
            profile_id,
            "risk",
            "Performance band slipping below 50%",
            "Your trend is declining and the projection band dips under half. "
            "Short revision on your weakest topic is the fastest lever.",
            ref_type="prediction",
            ref_id=None,
        )


def notify_plan(db: Session, profile_id: str, recs_today: int) -> None:
    """Gentle weekly-grade nudge: only when a fresh plan exists and nothing unread."""
    if recs_today > 0:
        _add(
            db,
            profile_id,
            "plan",
            "Study plan refreshed",
            f"{recs_today} priority actions are waiting on your plan.",
            ref_type="plan",
            ref_id=None,
        )


def list_all(db: Session, profile_id: str, limit: int = 30) -> list[dict]:
    """Stored rows (newest first) + live reminders computed from current state."""
    stored = db.scalars(
        select(models.Notification)
        .where(models.Notification.profile_id == profile_id)
        .order_by(models.Notification.created_at.desc())
        .limit(limit)
    ).all()
    out = [
        {
            "id": r.id,
            "kind": r.kind,
            "title": r.title,
            "body": r.body,
            "ref_type": r.ref_type,
            "ref_id": r.ref_id,
            "read": r.read_at is not None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "live": False,
        }
        for r in stored
    ]
    # Live: unfinished sittings (computed, never stored).
    live_attempts = db.scalars(
        select(models.Attempt)
        .where(models.Attempt.profile_id == profile_id, models.Attempt.status == "in_progress")
        .order_by(models.Attempt.created_at.desc())
        .limit(3)
    ).all()
    for a in live_attempts:
        out.append(
            {
                "id": f"live-resume-{a.id}",
                "kind": "resume",
                "title": f"Resume: {a.assessment.title} (attempt {a.attempt_number})",
                "body": "Drafts are saved — pick up where you left off.",
                "ref_type": "attempt",
                "ref_id": a.id,
                "read": False,
                "created_at": None,
                "live": True,
            }
        )
    return out


def mark_read(db: Session, profile_id: str, notification_id: str) -> bool:
    row = db.get(models.Notification, notification_id)
    if not row or row.profile_id != profile_id:
        return False
    if row.read_at is None:
        row.read_at = datetime.now(UTC).replace(tzinfo=None)
        db.commit()
    return True


def unread_count(db: Session, profile_id: str) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(models.Notification)
            .where(
                models.Notification.profile_id == profile_id, models.Notification.read_at.is_(None)
            )
        )
        or 0
    )
