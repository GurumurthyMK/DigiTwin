"""Evidence freshness / retention signal (Set 1).

Deterministic, explainable, bounded. NOT a medically validated memory model.
Terminology is deliberately "evidence freshness / retention signal".

Model
-----
Input: SkillEvidence rows for one skill (chronological). Only assessed
evidence enters: self_report-only skills have no rows → unknown, never 0%.

Output per skill:
- retention_state: unknown | fresh | fading | stale | very_stale
- retention_score: None when unknown, else 0..1 (higher = fresher)
- days_since_last_evidence: None when unknown, else >=0 (float days)
- last_evidence_at: ISO string | None

Thresholds (fixed, testable — see tests):
  FRESH_DAYS = 7
  FADING_DAYS = 21
  STALE_DAYS = 60

  0 .. 7          → fresh       (recent supporting evidence)
  7 .. 21         → fading      (progressively less fresh)
 21 .. 60         → stale
  >60             → very_stale
  no evidence     → unknown     (not 0%, not fabricated)

Score (bounded 0..1, linear decay to Stale bound):
  score = max(0, 1 - days/60) rounded to 4 decimals.
  fresh therefore 0.8833..1.0, fading 0.65..0.883, stale 0..0.65,
  very_stale 0.0.

Why linear: explainable, monotonic, reversible (same inputs → same
output), no hidden curvature, hand-verifiable in tests.

Time source: caller supplies `now` (UTC aware). When absent, utcnow is
used server-side but tests inject a fixed stamp to avoid flakiness.

Multi-skill questions: each (answer x skill) row is independent; this
module never weights or merges skills — one question with N mappings
produces N independent freshness streams.

Self-report-only: explicitly unknown — we never synthesize assessed
evidence for declared skills.

Must not mutate mastery values; this is a separate dimension consumed
by twin_service + twin_analytics, not by topic/subject rollups.
"""

from datetime import UTC, datetime

# Bounded thresholds — documented in module docstring for audit.
FRESH_DAYS = 7
FADING_DAYS = 21
STALE_DAYS = 60

# Backward compat alias for older drafts referring to "aging".
AGING_DAYS = FADING_DAYS

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models
from app.services import skill_mastery


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _days_since(last: datetime | None, now: datetime) -> float | None:
    if last is None:
        return None
    # Ensure both are aware UTC for arithmetic.
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    delta = (now - last).total_seconds() / 86400
    return max(0.0, round(delta, 4))


def freshness_from_days(days: float | None) -> tuple[str, float | None]:
    """Pure mapping days → (state, score). None days → unknown/None."""
    if days is None:
        return "unknown", None
    if days <= FRESH_DAYS:
        score = max(0.0, 1 - days / STALE_DAYS)
        return "fresh", round(score, 4)
    if days <= FADING_DAYS:
        score = max(0.0, 1 - days / STALE_DAYS)
        return "fading", round(score, 4)
    if days <= STALE_DAYS:
        score = max(0.0, 1 - days / STALE_DAYS)
        return "stale", round(score, 4)
    return "very_stale", 0.0


def freshness_for_skill_rows(
    rows: list[models.SkillEvidence], now: datetime | None = None
) -> dict:
    """Full freshness dict from a skill's evidence rows.

    Rows should be chronological; we pick the latest created_at.
    Returns dict with retention_state, retention_score, days_since_last_evidence,
    last_evidence_at, evidence_count, supporting_evidence_count (is_correct True).
    """
    now = now or _utcnow()
    if not rows:
        return {
            "retention_state": "unknown",
            "retention_score": None,
            "days_since_last_evidence": None,
            "last_evidence_at": None,
            "evidence_count": 0,
            "supporting_evidence_count": 0,
        }
    ordered = skill_mastery.order_evidence(rows)
    # Last evidence is any assessed evidence; freshness refreshes on any new row
    # (supporting vs non-supporting distinction is surfaced via counts but does
    # not gate the clock — a recent attempt, even incorrect, shows the skill was
    # recently demonstrated; correctness lives in mastery).
    last = ordered[-1]
    last_at: datetime | None = last.created_at
    # Normalize naive timestamps (SQLite) to UTC-aware for comparison.
    if last_at is not None and last_at.tzinfo is None:
        last_at = last_at.replace(tzinfo=UTC)
    days = _days_since(last_at, now)
    state, score = freshness_from_days(days)
    # supporting evidence = correct answers for human context, not gating freshness.
    supp = sum(1 for r in ordered if bool(r.is_correct))
    return {
        "retention_state": state,
        "retention_score": score,
        "days_since_last_evidence": days,
        "last_evidence_at": last_at.isoformat() if last_at is not None else None,
        "evidence_count": len(ordered),
        "supporting_evidence_count": supp,
    }


def retention_for_profile(
    db: Session, profile_id: str, now: datetime | None = None
) -> dict[str, dict]:
    """Per-skill retention for a student, keyed by skill_id.

    Only skills with at least one SkillEvidence row appear here as fresh/fading/
    stale/very_stale; self_report-only skills are absent (caller maps absence to
    unknown). Profile-scoped: never leaks another student's evidence.
    """
    now = now or _utcnow()
    rows = db.scalars(
        select(models.SkillEvidence).where(models.SkillEvidence.profile_id == profile_id)
    ).all()
    by_skill: dict[str, list[models.SkillEvidence]] = {}
    for r in rows:
        by_skill.setdefault(r.skill_id, []).append(r)
    out: dict[str, dict] = {}
    for sid, skill_rows in by_skill.items():
        out[sid] = freshness_for_skill_rows(skill_rows, now=now)
    return out


def retention_for_skill(
    db: Session, profile_id: str, skill_id: str, now: datetime | None = None
) -> dict:
    """Single-skill convenience wrapper; returns unknown when no rows."""
    rows = db.scalars(
        select(models.SkillEvidence).where(
            models.SkillEvidence.profile_id == profile_id,
            models.SkillEvidence.skill_id == skill_id,
        )
    ).all()
    return freshness_for_skill_rows(rows, now=now)
