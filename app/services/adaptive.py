"""Set 2 adaptive recommendations: Twin-driven, deterministic, explainable.

The Twin remains authoritative. This module only READS Twin state
(mastery, confidence, trend, retention, evidence), assessment history,
lessons, and past recommendation feedback — it never writes learning
state. No LLMs, no ML, no fabricated behavior.

Priority rules (deterministic, fixed — audit in tests):
  P1 (act now):
    - weak_skill / weak_topic: assessed mastery < 0.45 (lowest first)
    - declining: skill trend == declining
    - retry: low mastery (<=0.45) + recent evidence (fresh/fading)
  P2 (act soon):
    - refresh: retention stale / very_stale (oldest first)
    - verify_strength: high mastery (>=0.70) + stale evidence
    - continue: unfinished lesson on a weak/evidenced topic
  P3 (maintain):
    - consolidate: recently strengthened (improving + fresh + mastery>=0.5)
    - review / starter fallbacks

Stable ordering: (priority, rule_rank, title, rec_key). Same Twin state
+ same feedback history => byte-identical output (minus generated_at).

Adaptation (feedback as future signal, Twin still decides):
  - completed within COMPLETED_SUPPRESS_DAYS (7) => suppressed
  - dismissed/skipped within DISMISSED_SUPPRESS_DAYS (14) => suppressed
  - accepted/started does NOT suppress (still actionable, marked started)
  - stale recs disappear naturally once fresh evidence arrives (rule no
    longer fires) — no separate resolution table
  - refs validated live on every generation; dangling refs are skipped
    (resolve safely), never fabricated

Each recommendation carries:
  kind (stable rule id), rec_key (stable identity "rule:<subject>:<id>"),
  title, reason (grounded numbers), evidence[], priority (1..3),
  est_minutes, refs{subject_id?,topic_id?,assessment_id?,content_id?,
  skill_id?}, status (active|started|completed|dismissed from feedback),
  is_stale (refs dangling — never emitted, documented), generated_at.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import models

ENGINE = "set2-adaptive-1.0"

# Thresholds mirror Set 1 analytics (single source of truth lives here for
# rec generation; analytics module keeps its own identical constants).
LOW_MASTERY_BELOW = 0.45
HIGH_MASTERY_AT = 0.70

# Feedback adaptation windows (days) — fixed, documented, testable.
COMPLETED_SUPPRESS_DAYS = 7
DISMISSED_SUPPRESS_DAYS = 14

VALID_STATUSES = {"accepted", "started", "completed", "dismissed", "skipped"}
# Dismiss-like statuses that suppress regeneration.
_SUPPRESSING_DISMISS = {"dismissed", "skipped"}

# Rule rank inside a priority bucket (lower = earlier). Guarantees stable
# competing-priority ordering independent of dict iteration.
_RULE_RANK = {
    "weak_skill": 0,
    "weak_topic": 1,
    "declining": 2,
    "retry": 3,
    "refresh": 4,
    "verify_strength": 5,
    "continue": 6,
    "consolidate": 7,
    "starter": 8,
    "review": 9,
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


# ------------------------------------------------------------------
# Ref resolution (all server-side, all validated — never fabricated)
# ------------------------------------------------------------------

def _first_assessment_for_topic(db: Session, topic_id: str) -> models.Assessment | None:
    return db.scalar(
        select(models.Assessment)
        .where(models.Assessment.topic_id == topic_id, models.Assessment.is_published.is_(True))
        .order_by(models.Assessment.title, models.Assessment.id)
        .limit(1)
    )


def _first_lesson_for_topic(db: Session, topic_id: str) -> models.ContentItem | None:
    return db.scalar(
        select(models.ContentItem)
        .where(models.ContentItem.topic_id == topic_id)
        .order_by(models.ContentItem.order_index, models.ContentItem.id)
        .limit(1)
    )


def _topic_for_skill(db: Session, skill_id: str) -> models.Topic | None:
    skill = db.get(models.Skill, skill_id)
    if skill is None or skill.topic_id is None:
        return None
    return db.get(models.Topic, skill.topic_id)


def validate_refs(db: Session, refs: dict) -> list[str]:
    """Return list of problems; empty = all present refs resolve live."""
    problems: list[str] = []
    if not isinstance(refs, dict):
        return ["refs_must_be_object"]
    for key, model, label in (
        ("subject_id", models.Subject, "unknown_subject"),
        ("topic_id", models.Topic, "unknown_topic"),
        ("assessment_id", models.Assessment, "unknown_assessment"),
        ("content_id", models.ContentItem, "unknown_content"),
        ("skill_id", models.Skill, "unknown_skill"),
    ):
        val = refs.get(key)
        if val is not None and db.get(model, val) is None:
            problems.append(label)
    return problems


def resolve_skill_refs(db: Session, skill_id: str) -> dict | None:
    """Actionable refs for a skill: topic + assessment + lesson. None when the
    skill has no curriculum anchor (self-report-only) — no rec is fabricated."""
    topic = _topic_for_skill(db, skill_id)
    if topic is None:
        return None
    out: dict = {"skill_id": skill_id, "topic_id": topic.id}
    if topic.subject_id:
        out["subject_id"] = topic.subject_id
    assessment = _first_assessment_for_topic(db, topic.id)
    if assessment is not None:
        out["assessment_id"] = assessment.id
    lesson = _first_lesson_for_topic(db, topic.id)
    if lesson is not None:
        out["content_id"] = lesson.id
    # Actionable means at least a topic anchor; assessment/lesson best-effort.
    return out


def resolve_topic_refs(db: Session, topic_id: str) -> dict | None:
    topic = db.get(models.Topic, topic_id)
    if topic is None:
        return None
    out: dict = {"topic_id": topic_id}
    if topic.subject_id:
        out["subject_id"] = topic.subject_id
    assessment = _first_assessment_for_topic(db, topic_id)
    if assessment is not None:
        out["assessment_id"] = assessment.id
    lesson = _first_lesson_for_topic(db, topic_id)
    if lesson is not None:
        out["content_id"] = lesson.id
    return out


# ------------------------------------------------------------------
# Feedback history (append-only; latest per rec_key wins)
# ------------------------------------------------------------------

def latest_status_by_key(db: Session, profile_id: str) -> dict[str, dict]:
    """Latest feedback row per rec_key for this profile (profile-scoped)."""
    rows = db.scalars(
        select(models.RecommendationFeedback)
        .where(models.RecommendationFeedback.profile_id == profile_id)
        .order_by(models.RecommendationFeedback.created_at, models.RecommendationFeedback.id)
    ).all()
    latest: dict[str, dict] = {}
    for r in rows:
        latest[r.rec_key] = {"status": r.status, "created_at": r.created_at, "kind": r.kind}
    return latest


def suppressed_keys(db: Session, profile_id: str, now: datetime | None = None) -> set[str]:
    """rec_keys currently suppressed by completed/dismissed feedback windows."""
    now = now or _utcnow()
    now_a = _as_aware(now) or now
    rows = db.scalars(
        select(models.RecommendationFeedback).where(
            models.RecommendationFeedback.profile_id == profile_id
        )
    ).all()
    # Keep only the latest row per key, then apply windows.
    by_key: dict[str, models.RecommendationFeedback] = {}
    for r in rows:
        prev = by_key.get(r.rec_key)
        if prev is None or (_as_aware(r.created_at) or r.created_at) >= (
            _as_aware(prev.created_at) or prev.created_at
        ):
            by_key[r.rec_key] = r
    out: set[str] = set()
    for key, r in by_key.items():
        created = _as_aware(r.created_at) or now_a
        age_days = (now_a - created).total_seconds() / 86400
        if r.status == "completed" and age_days <= COMPLETED_SUPPRESS_DAYS:
            out.add(key)
        elif r.status in _SUPPRESSING_DISMISS and age_days <= DISMISSED_SUPPRESS_DAYS:
            out.add(key)
    return out


def record_feedback(
    db: Session,
    profile_id: str,
    rec_key: str,
    kind: str,
    refs: dict,
    action: str,
    title: str = "",
    now: datetime | None = None,
) -> models.RecommendationFeedback:
    """Append one feedback event. Validates refs + action server-side.

    Raises AppError(422) on invalid refs/action. Caller commits (API layer).
    History is preserved: never updates existing rows.
    """
    from app.core.errors import AppError

    if action not in VALID_STATUSES:
        raise AppError("invalid_feedback", f"Unknown action '{action}'.", 422)
    if not rec_key or len(rec_key) > 160:
        raise AppError("invalid_feedback", "rec_key must be 1..160 chars.", 422)
    problems = validate_refs(db, refs)
    if problems:
        raise AppError("invalid_reference", f"Unknown refs: {', '.join(problems)}.", 422)
    import json as _json

    row = models.RecommendationFeedback(
        profile_id=profile_id,
        rec_key=rec_key,
        kind=kind or "unknown",
        title=(title or "")[:200],
        refs_json=_json.dumps(refs or {}),
        status=action,
    )
    if now is not None:
        row.created_at = now
        row.updated_at = now
    db.add(row)
    db.flush()
    return row


def feedback_history(db: Session, profile_id: str, limit: int = 50) -> list[dict]:
    import json as _json

    rows = db.scalars(
        select(models.RecommendationFeedback)
        .where(models.RecommendationFeedback.profile_id == profile_id)
        .order_by(
            models.RecommendationFeedback.created_at.desc(),
            models.RecommendationFeedback.id.desc(),
        )
        .limit(min(max(limit, 1), 100))
    ).all()
    return [
        {
            "id": r.id,
            "rec_key": r.rec_key,
            "kind": r.kind,
            "title": r.title,
            "refs": _json.loads(r.refs_json or "{}"),
            "status": r.status,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


# ------------------------------------------------------------------
# Adaptive generation (Twin-driven)
# ------------------------------------------------------------------

def _rec(
    kind: str,
    rec_key: str,
    title: str,
    reason: str,
    evidence: list[dict],
    priority: int,
    est_minutes: int,
    refs: dict,
    status: str | None = None,
) -> dict:
    return {
        "kind": kind,
        "rec_key": rec_key,
        "title": title,
        "reason": reason,
        "evidence": evidence,
        "priority": priority,
        "est_minutes": est_minutes,
        "refs": refs,
        "status": status or "active",
        "is_stale": False,
        "generated_at": _utcnow().isoformat(),
    }


def build_adaptive_recommendations(
    db: Session, profile_id: str, now: datetime | None = None
) -> dict:
    """Authoritative adaptive plan. Centralized: clients never decide."""
    from app.services import retention as _ret
    from app.services.twin_service import compute_profile_twin

    now = now or _utcnow()
    twin = compute_profile_twin(db, profile_id)
    fresh = _ret.retention_for_profile(db, profile_id, now=now)
    suppressed = suppressed_keys(db, profile_id, now=now)
    latest = latest_status_by_key(db, profile_id)

    # Incomplete lessons (unfinished learning) with durations.
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
    lessons_by_topic: dict[str, list[models.ContentItem]] = {}
    for lesson in incomplete:
        lessons_by_topic.setdefault(lesson.topic_id, []).append(lesson)

    candidates: list[dict] = []

    def _status_for(key: str) -> str:
        st = latest.get(key, {}).get("status")
        return st if st in VALID_STATUSES else "active"

    # ---- skill rules (Twin evidence track) ----
    for skid, sk in twin["skills"].items():
        f = fresh.get(skid)
        if f is None:
            f = {"retention_state": "unknown", "retention_score": None,
                 "days_since_last_evidence": None}
        mastery, trend = sk["mastery"], sk["trend"]
        ev = sk["skill_evidence_count"]
        if mastery is None:
            continue  # self-report-only: no assessed signal, no rec
        refs = resolve_skill_refs(db, skid)
        if refs is None:
            continue  # no curriculum anchor — never fabricate
        name = sk["name"]
        evd = [{
            "kind": "skill", "id": skid, "label": name,
            "detail": f"mastery {mastery:.0%} over {ev} evidence, trend {trend or 'unknown'}, freshness {f['retention_state']}",
        }]
        days = f["days_since_last_evidence"]
        age = f"{int(days)}d ago" if days is not None else "no assessed evidence"

        # P1 weak
        if mastery < LOW_MASTERY_BELOW:
            key = f"weak_skill:{skid}"
            candidates.append(_rec(
                "weak_skill", key, f"Rebuild: {name}",
                f"Mastery {mastery:.0%} over {ev} answers — lowest in your map ({age}).",
                evd, 1, 20, refs, _status_for(key)))
        # P1 declining (even when mastery not yet low)
        if trend == "declining":
            key = f"declining:{skid}"
            candidates.append(_rec(
                "declining", key, f"Steady: {name}",
                f"Trend declining (slope {sk['trend_slope']:+.3f}) at mastery {mastery:.0%} over {ev} answers.",
                evd, 1, 15, refs, _status_for(key)))
        # P1 retry: low + recent (supports a new attempt right now)
        if mastery <= LOW_MASTERY_BELOW and f["retention_state"] in ("fresh", "fading"):
            key = f"retry:{skid}"
            candidates.append(_rec(
                "retry", key, f"Retry now: {name}",
                f"Low mastery {mastery:.0%} with recent evidence ({age}) — a fresh attempt can move it.",
                evd, 1, 10, refs, _status_for(key)))
        # P2 refresh stale
        if f["retention_state"] in ("stale", "very_stale"):
            key = f"refresh:{skid}"
            candidates.append(_rec(
                "refresh", key, f"Refresh: {name}",
                f"No supporting evidence for {age}; mastery {mastery:.0%} may be outdated.",
                evd, 2, 15, refs, _status_for(key)))
        # P2 verify high+stale
        if mastery >= HIGH_MASTERY_AT and f["retention_state"] in ("stale", "very_stale"):
            key = f"verify_strength:{skid}"
            candidates.append(_rec(
                "verify_strength", key, f"Verify strength: {name}",
                f"High mastery {mastery:.0%} but evidence is {f['retention_state']} ({age}) — confirm it still holds.",
                evd, 2, 10, refs, _status_for(key)))
        # P3 consolidate recently strengthened
        if trend == "improving" and f["retention_state"] == "fresh" and mastery >= 0.5:
            key = f"consolidate:{skid}"
            candidates.append(_rec(
                "consolidate", key, f"Lock in: {name}",
                f"Improving (slope {sk['trend_slope']:+.3f}) with fresh evidence ({age}) at {mastery:.0%} — consolidate the gain.",
                evd, 3, 10, refs, _status_for(key)))

    # ---- topic rules (weak topics not covered by skill rules) ----
    for tid, t in twin["topics"].items():
        if t["evidence_count"] < 2 or t["mastery"] >= LOW_MASTERY_BELOW:
            continue
        refs = resolve_topic_refs(db, tid)
        if refs is None:
            continue
        key = f"weak_topic:{tid}"
        lesson = (lessons_by_topic.get(tid) or [None])[0]
        lesson_refs = dict(refs)
        if lesson is not None:
            lesson_refs["content_id"] = lesson.id
        candidates.append(_rec(
            "weak_topic", key, f"Revise: {t['title']}",
            f"Mastery {t['mastery']:.0%} over {t['evidence_count']} answers — lowest in your map.",
            [{"kind": "topic", "id": tid, "label": t["title"],
              "detail": f"mastery {t['mastery']:.0%} over {t['evidence_count']} answers"}],
            1, (lesson.duration_minutes or 10) if lesson else 15,
            lesson_refs, _status_for(key)))

    # ---- unfinished learning on evidenced topics ----
    for tid, lessons in lessons_by_topic.items():
        topic = db.get(models.Topic, tid)
        if topic is None:
            continue
        t = twin["topics"].get(tid)
        # Only surface when the topic has evidence or is weak — otherwise the
        # starter fallback below owns the no-evidence case.
        if t is None:
            continue
        lesson = lessons[0]
        refs = resolve_topic_refs(db, tid) or {"topic_id": tid}
        refs["content_id"] = lesson.id
        key = f"continue:{tid}:{lesson.id}"
        candidates.append(_rec(
            "continue", key, f"Continue: {lesson.title}",
            f"Unfinished lesson in '{topic.title}' (mastery {t['mastery']:.0%}).",
            [{"kind": "topic", "id": tid, "label": topic.title,
              "detail": f"mastery {t['mastery']:.0%}"}],
            2, lesson.duration_minutes or 10, refs, _status_for(key)))

    # ---- fallbacks (only when nothing actionable fired) ----
    if not candidates:
        if twin["attempts_count"] == 0:
            for lesson in incomplete[:3]:
                refs = {"topic_id": lesson.topic_id, "content_id": lesson.id}
                topic = db.get(models.Topic, lesson.topic_id)
                if topic and topic.subject_id:
                    refs["subject_id"] = topic.subject_id
                key = f"starter:{lesson.id}"
                candidates.append(_rec(
                    "starter", key, f"Start: {lesson.title}",
                    "No graded evidence yet — this starter plan walks your topics in order.",
                    [], 1, lesson.duration_minutes or 10, refs, _status_for(key)))
            if not candidates:
                candidates.append(_rec(
                    "starter", "starter:quiz", "Take your first quiz",
                    "The engine needs graded evidence before it can personalize. Any quiz counts.",
                    [], 1, 10, {}, _status_for("starter:quiz")))
        else:
            # Spaced review of the strongest topic.
            best = max(twin["topics"].values(), key=lambda x: x["mastery"], default=None)
            if best is not None:
                tid = next(tid for tid, v in twin["topics"].items() if v is best)
                refs = resolve_topic_refs(db, tid) or {}
                key = f"review:{tid}"
                candidates.append(_rec(
                    "review", key, "Spaced review: your strongest topic",
                    f"No weak areas right now — protect {best['mastery']:.0%} in '{best['title']}' with light review.",
                    [], 3, 10, refs, _status_for(key)))
            else:
                candidates.append(_rec(
                    "review", "review:general", "Spaced review",
                    "No weak areas right now — light review protects the lead.", [], 3, 10, {},
                    _status_for("review:general")))

    # ---- adaptation: suppress completed/dismissed, drop dangling refs ----
    live: list[dict] = []
    for c in candidates:
        if c["rec_key"] in suppressed:
            continue
        if validate_refs(db, c["refs"]):
            continue  # topic/assessment deleted → resolve safely by skipping
        live.append(c)

    live.sort(key=lambda r: (r["priority"], _RULE_RANK.get(r["kind"], 99), r["title"], r["rec_key"]))
    return {"today": live[:3], "queue": live[3:], "engine": ENGINE,
            "generated_at": now.isoformat()}


def recommendation_snapshot(db: Session, profile_id: str, rec_key: str) -> dict | None:
    """Current generated rec with this key (for feedback title/kind echo)."""
    plan = build_adaptive_recommendations(db, profile_id)
    for r in plan["today"] + plan["queue"]:
        if r["rec_key"] == rec_key:
            return r
    return None
