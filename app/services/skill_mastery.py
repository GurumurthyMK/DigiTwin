"""Skill Mastery + Confidence engine (V2-A4/V2-B1): deterministic estimates
from SkillEvidence.

Concepts (do not confuse):
- Skill Evidence: what observations exist (V2-A3 rows, the ONLY input here).
- Skill Mastery: estimate of current ability given those observations.
  A continuous value in [0, 1]; never called a score.
- Skill Confidence: evidential support FOR the mastery estimate — how much
  coherent evidence backs it. NOT ability, NOT certainty about the future.

Deliberately NOT consumed by the Twin: TwinSkillProficiency still derives
solely from ProfileSkill self-reports. This engine exists alongside it until
a later instruction set integrates the two (see docs/decisions.md).

Properties: pure recompute from append-only rows (reproducible, rebuildable,
no materialized state), chronological order sensitivity for mastery,
bounded output.
"""

import math

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models

# Neutral mastery before any evidence (same starting point as topic mastery).
PRIOR_MASTERY = 0.5
# EWMA weight of each new observation. Topic mastery uses 0.35 over long
# mixed-concept answer streams; per-skill streams are short (often 1-10 rows)
# and every observation is directly about the skill, so 0.5 — equal weight to
# the new observation and all history — keeps the estimate responsive without
# overreacting, and yields hand-verifiable values (0.75/0.25 after one row).
SKILL_ALPHA = 0.5
# Trend methodology mirrors the Twin: OLS slope over the last <=10 outcomes,
# classified with the same epsilon; fewer than 3 points is not a trend.
TREND_WINDOW = 10
TREND_MIN_POINTS = 3
TREND_EPS = 0.02
# Confidence saturation scale: observations at which the quantity factor
# reaches 1 - 1/e (~0.63). Per-skill streams are short (a quiz sitting yields
# 2-3 rows), so k=5 gives modest support per sitting (n=2 -> 0.33, n=3 ->
# 0.45) rising toward certainty over repeated sittings (n=10 -> 0.86,
# n=20 -> 0.98), saturating so count alone can never inflate past 1.
CONFIDENCE_K = 5.0
# Numeric precision matches the Twin's round-4 convention.
_PRECISION = 4


def _order_key(row: models.SkillEvidence) -> tuple:
    # Epoch floats keep naive (SQLite) and aware (Postgres) timestamps mutually
    # comparable; the id tiebreak keeps the order total when stamps collide.
    stamp = row.created_at.timestamp() if row.created_at is not None else float("-inf")
    return (stamp, row.id)


def order_evidence(rows: list[models.SkillEvidence]) -> list[models.SkillEvidence]:
    """Authoritative chronological order: created_at, then id.

    Never database default row order. The id tiebreak keeps the order total
    and deterministic when timestamps collide.
    """
    return sorted(rows, key=_order_key)


def calculate_skill_mastery(outcomes: list[bool]) -> float:
    """EWMA over chronological outcomes from the neutral prior.

    Correct moves up, incorrect moves down, recent rows dominate, output is
    always within [0, 1]. Empty input returns the neutral prior.
    """
    m = PRIOR_MASTERY
    for correct in outcomes:
        m = SKILL_ALPHA * (1.0 if correct else 0.0) + (1 - SKILL_ALPHA) * m
    return round(m, _PRECISION)


def calculate_skill_trend(outcomes: list[bool]) -> tuple[str | None, float | None]:
    """OLS slope direction over recent outcomes: improving/declining/stable.

    (None, None) with fewer than 3 observations — a single observation is
    never classified as a trend.
    """
    window = [1.0 if o else 0.0 for o in outcomes[-TREND_WINDOW:]]
    if len(window) < TREND_MIN_POINTS:
        return None, None
    n = len(window)
    mean_x = sum(range(n)) / n
    mean_y = sum(window) / n
    denom = sum((x - mean_x) ** 2 for x in range(n))
    slope = (
        round(
            sum((x - mean_x) * (y - mean_y) for x, y in zip(range(n), window)) / denom, _PRECISION
        )
        if denom
        else 0.0
    )
    if slope > TREND_EPS:
        return "improving", slope
    if slope < -TREND_EPS:
        return "declining", slope
    return "stable", slope


def calculate_skill_confidence(outcomes: list[bool]) -> float | None:
    """Evidential support for the mastery estimate: quantity x coherence.

    quantity = 1 - exp(-n / CONFIDENCE_K): saturating support that grows with
    each observation but can never reach 1 on count alone (a single sitting
    of 2-3 rows yields ~0.33-0.45; certainty requires repeated sittings).
    coherence = majority share max(correct, incorrect) / n: unanimous streams
    count fully, contradictory streams count partially — without zeroing
    legitimate mixed performance, and symmetric in correctness so confidence
    never mirrors mastery (all-wrong supports its estimate exactly as much
    as all-right). Deliberately order-free: recency already lives in mastery.
    None when there is no evidence — no basis, no claim.
    """
    n = len(outcomes)
    if n == 0:
        return None
    majority = max(sum(outcomes), n - sum(outcomes)) / n
    return round((1 - math.exp(-n / CONFIDENCE_K)) * majority, _PRECISION)


def calculate_skill_state(skill_id: str, rows: list[models.SkillEvidence]) -> dict | None:
    """Full mastery + confidence state for one skill from its evidence rows.

    None when there is no evidence — no state is fabricated. Counts,
    correctness split, trend, and last_updated all derive from the rows.
    """
    ordered = order_evidence(rows)
    if not ordered:
        return None
    outcomes = [bool(r.is_correct) for r in ordered]
    trend, slope = calculate_skill_trend(outcomes)
    stamps = [r.created_at for r in ordered if r.created_at is not None]
    latest = max(stamps) if stamps else None
    return {
        "skill_id": skill_id,
        "mastery": calculate_skill_mastery(outcomes),
        "confidence": calculate_skill_confidence(outcomes),
        "evidence_count": len(ordered),
        "correct_count": sum(outcomes),
        "incorrect_count": len(ordered) - sum(outcomes),
        "trend": trend,
        "trend_slope": slope,
        "last_updated": latest.isoformat() if latest is not None else None,
    }


def skill_states_for_profile(db: Session, profile_id: str) -> dict[str, dict]:
    """Recompute every skill state for a student from SkillEvidence only.

    Each skill's stream is processed independently (no cross-skill
    contamination, no weighting). ProfileSkill, lessons, and topic/subject
    mastery are never read here. Skills without evidence are absent.
    """
    rows = db.scalars(
        select(models.SkillEvidence).where(models.SkillEvidence.profile_id == profile_id)
    ).all()
    by_skill: dict[str, list[models.SkillEvidence]] = {}
    for row in rows:
        by_skill.setdefault(row.skill_id, []).append(row)
    states = {}
    for skill_id, skill_rows in by_skill.items():
        state = calculate_skill_state(skill_id, skill_rows)
        if state is not None:
            states[skill_id] = state
    return states
