"""Advanced Twin analytics (Set 1): deterministic derived insights.

Layered output distinguishes:
- observed  — raw counts pulled from stored rows
- derived   — computed metrics (freshness, trajectories, coverage)
- interpretation — rule-based flags (reinforcement, stale-high-mastery, etc.)

No predictions, no causal claims. Every flag cites the rows behind it.
All calculations are centralized here; Web and Mobile consume the single
server response and never compute their own twin state.

Consumes: skill_mastery, retention, twin_service.compute_profile_twin.
Never writes to attempts/answers/twin tables.
"""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import models
from app.services import retention, skill_mastery
from app.services.twin_service import compute_profile_twin

# Interpretation thresholds — fixed, documented, testable.
REINFORCEMENT_MASTERY_BELOW = 0.45
HIGH_MASTERY_AT = 0.70
LOW_MASTERY_AT = 0.45

# Confidence trajectory requires at least this many observations to claim a
# direction; otherwise insufficient evidence.
CONFIDENCE_TREND_MIN = 3


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _coverage_for_profile(db: Session, profile_id: str, assessed_skill_ids: set[str]) -> dict:
    """Evidence coverage across the student's skill graph.

    Denominator: taxonomy skills (topic-bound) + self-reported skills that have
    a name in the shared Skill table. Simple ratio for transparency; no weighting.
    """
    total_taxonomy = db.scalar(
        select(func.count()).select_from(models.Skill).where(models.Skill.topic_id.is_not(None))
    ) or 0
    # Self-report distinct skill ids for this profile (may overlap taxonomy)
    self_ids = set(
        db.scalars(
            select(models.ProfileSkill.skill_id).where(
                models.ProfileSkill.profile_id == profile_id
            )
        ).all()
    )
    # Union for denominator: taxonomy + any extra self-report-only skills
    extra = len(self_ids - assessed_skill_ids) if total_taxonomy else len(self_ids)
    total_graph = int(total_taxonomy) + extra if total_taxonomy else max(len(assessed_skill_ids), 1)
    assessed = len(assessed_skill_ids)
    ratio = round(assessed / total_graph, 4) if total_graph else 0.0
    return {
        "total_graph_skills": total_graph,
        "taxonomy_skills": int(total_taxonomy),
        "self_report_extra": extra,
        "assessed_skills": assessed,
        "coverage_ratio": ratio,
        "assessed_skill_ids": sorted(assessed_skill_ids),
    }


def build_analytics(db: Session, profile_id: str, now: datetime | None = None) -> dict:
    """Single authoritative analytics payload for a student.

    `now` injects the retention clock; tests pass a frozen stamp to avoid
    flaky time-dependent assertions.
    """
    now = now or _utcnow()

    # Derived twin (recompute same as twin_service — authoritative source)
    twin = compute_profile_twin(db, profile_id)

    # Per-skill mastery/confidence states keyed by skill_id
    derived = skill_mastery.skill_states_for_profile(db, profile_id)

    # Per-skill retention keyed by skill_id
    fresh_map = retention.retention_for_profile(db, profile_id, now=now)

    # Skill names for display
    skill_names: dict[str, str] = {}
    if derived or fresh_map:
        needed = set(derived.keys()) | set(fresh_map.keys())
        # Also include self-report-only skills that have no evidence (unknown retention)
        for link in db.scalars(
            select(models.ProfileSkill).where(models.ProfileSkill.profile_id == profile_id)
        ).all():
            needed.add(link.skill_id)
        if needed:
            for s in db.scalars(select(models.Skill).where(models.Skill.id.in_(list(needed)))).all():
                skill_names[s.id] = s.name

    # --- observed ---------------------------------------------------------
    total_skills_in_graph = db.scalar(
        select(func.count()).select_from(models.Skill).where(models.Skill.topic_id.is_not(None))
    ) or 0
    observed = {
        "attempts_count": twin["attempts_count"],
        "total_answers": twin["total_answers"],
        "correct_answers": twin["correct_answers"],
        "lessons_completed": twin["lessons_completed"],
        "skill_evidence_rows": sum(v["skill_evidence_count"] for v in twin["skills"].values()),
        "taxonomy_skill_count": int(total_skills_in_graph),
    }

    # --- derived ----------------------------------------------------------
    # Skills with retention + mastery merged
    skill_rows: list[dict] = []
    for skid, sk in twin["skills"].items():
        f = fresh_map.get(skid)
        if f is None:
            # self_report-only or never-assessed row → unknown retention
            f = {
                "retention_state": "unknown",
                "retention_score": None,
                "days_since_last_evidence": None,
                "last_evidence_at": None,
                "evidence_count": 0,
                "supporting_evidence_count": 0,
            }
        # Include mastery trend already on sk; confidence trend is analogous
        # but we derive a simple confidence trajectory from evidence count / coherence
        # where evidence_count >= 3, otherwise insufficient.
        confidence_trajectory = None
        if sk["skill_evidence_count"] >= CONFIDENCE_TREND_MIN and sk["confidence"] is not None:
            # Reuse mastery trend direction as proxy when both tracks exist — both
            # derive from the same outcome stream; separate trajectory would require
            # historical confidence snapshots which we do not store.
            confidence_trajectory = sk["trend"]

        skill_rows.append(
            {
                "skill_id": skid,
                "name": sk["name"],
                "source": sk["source"],
                "mastery": sk["mastery"],
                "confidence": sk["confidence"],
                "skill_evidence_count": sk["skill_evidence_count"],
                "correct_count": sk["correct_count"],
                "incorrect_count": sk["incorrect_count"],
                "trend": sk["trend"],
                "trend_slope": sk["trend_slope"],
                "last_updated": sk["last_updated"],
                "retention_state": f["retention_state"],
                "retention_score": f["retention_score"],
                "days_since_last_evidence": f["days_since_last_evidence"],
                "last_evidence_at": f["last_evidence_at"],
                "confidence_trajectory": confidence_trajectory,
            }
        )

    # Coverage
    coverage = _coverage_for_profile(db, profile_id, set(derived.keys()))

    derived_block = {
        "skill_freshness": sorted(skill_rows, key=lambda r: (r["retention_score"] if r["retention_score"] is not None else -1), reverse=True),
        "mastery_trajectories": [
            {"skill_id": r["skill_id"], "name": r["name"], "trend": r["trend"], "trend_slope": r["trend_slope"]}
            for r in skill_rows
            if r["trend"] is not None
        ],
        "confidence_trajectories": [
            {"skill_id": r["skill_id"], "name": r["name"], "trajectory": r["confidence_trajectory"], "confidence": r["confidence"]}
            for r in skill_rows
            if r["confidence_trajectory"] is not None
        ],
        "coverage": coverage,
        "overall_trend": {"direction": twin["trend_direction"], "slope": twin["trend_slope"]},
    }

    # --- interpretation ---------------------------------------------------
    # Deterministic rule sets — each flag cites evidence, no fabricated causes.
    # Stable/improving/declining counts across skills with a trend.
    state_counts = {"improving": 0, "stable": 0, "declining": 0, "unknown": 0}
    for r in skill_rows:
        t = r["trend"]
        if t in state_counts:
            state_counts[t] += 1
        else:
            state_counts["unknown"] += 1

    # Skills needing reinforcement: low mastery (<0.45) or declining where mastery known
    needing = [
        r for r in skill_rows
        if r["mastery"] is not None and (r["mastery"] < REINFORCEMENT_MASTERY_BELOW or r["trend"] == "declining")
    ]
    # Recently strengthened: improving trend + fresh evidence (<=7d) + mastery decent
    recently = [
        r for r in skill_rows
        if r["trend"] == "improving" and r["retention_state"] == "fresh" and (r["mastery"] or 0) >= 0.5
    ]
    # High mastery but stale evidence
    high_stale = [
        r for r in skill_rows
        if (r["mastery"] or 0) >= HIGH_MASTERY_AT and r["retention_state"] in ("stale", "very_stale")
    ]
    # Low mastery with recent evidence
    low_recent = [
        r for r in skill_rows
        if (r["mastery"] or 0) <= LOW_MASTERY_AT and r["retention_state"] in ("fresh", "fading")
    ]

    def _flag_row(r: dict, why: str) -> dict:
        return {
            "skill_id": r["skill_id"],
            "name": r["name"],
            "mastery": r["mastery"],
            "retention_state": r["retention_state"],
            "days_since_last_evidence": r["days_since_last_evidence"],
            "trend": r["trend"],
            "evidence_count": r["skill_evidence_count"],
            "reason": why,
        }

    interpretation = {
        "state_counts": state_counts,
        "skills_needing_reinforcement": [
            _flag_row(r, f"mastery {r['mastery']:.0%} over {r['skill_evidence_count']} evidence, trend {r['trend'] or 'unknown'} — needs reinforcement.")
            if r["mastery"] is not None else _flag_row(r, "no assessed evidence yet.")
            for r in sorted(needing, key=lambda x: x["mastery"] or 0)
        ],
        "recently_strengthened": [
            _flag_row(r, f"improving trend ({r['trend_slope']:+.3f}) with fresh evidence ({r['days_since_last_evidence']}d ago).")
            for r in sorted(recently, key=lambda x: x["mastery"] or 0, reverse=True)
        ],
        "high_mastery_stale": [
            _flag_row(r, f"high mastery {r['mastery']:.0%} but evidence is {r['retention_state']} ({r['days_since_last_evidence']}d ago).")
            for r in sorted(high_stale, key=lambda x: x["days_since_last_evidence"] or 0, reverse=True)
        ],
        "low_mastery_recent": [
            _flag_row(r, f"low mastery {r['mastery']:.0%} with recent evidence ({r['days_since_last_evidence']}d ago) — supports a new attempt.")
            for r in sorted(low_recent, key=lambda x: x["mastery"] or 0)
        ],
        "disclaimer": "Rule-based flags from stored evidence, not a prediction. Review the cited rows before acting.",
    }

    return {
        "observed": observed,
        "derived": derived_block,
        "interpretation": interpretation,
        "generated_at": now.isoformat(),
    }
