"""Digital twin pipeline. All metrics are DERIVED from stored evidence
(submitted attempts/answers + learning progress) — see docs/twin-metrics.md.

Properties: deterministic (same evidence => same state), reproducible
(full recompute from raw rows on every update), append-only history.
The pipeline never writes to attempts/answers.
"""

import json
import statistics
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db import models

# EWMA weight for each new answer outcome when building topic mastery.
ALPHA = 0.35
# Neutral prior before any evidence.
PRIOR_MASTERY = 0.5
# Trend slope thresholds (accuracy points per attempt).
TREND_EPS = 0.02
# Self-reported skill level -> prior proficiency.
SKILL_PRIORS = {"beginner": 0.25, "intermediate": 0.5, "advanced": 0.75}
# Min |delta| recorded as a snapshot change (avoids float-noise entries).
DELTA_EPS = 0.005


def _now():
    return datetime.now(UTC)


def _evidence(db: Session, profile_id: str) -> list[models.Attempt]:
    """All submitted attempts with answers+assessments, chronological."""
    return db.scalars(
        select(models.Attempt)
        .where(models.Attempt.profile_id == profile_id, models.Attempt.status == "submitted")
        .order_by(models.Attempt.submitted_at, models.Attempt.created_at)
        .options(
            selectinload(models.Attempt.answers),
            selectinload(models.Attempt.assessment).selectinload(models.Assessment.subject),
            selectinload(models.Attempt.assessment).selectinload(models.Assessment.topic),
        )
    ).all()


def _topic_masteries(db: Session, attempts: list[models.Attempt]) -> dict[str, dict]:
    """EWMA per topic over answer outcomes in chronological order."""
    per_topic: dict[str, list[float]] = {}
    for attempt in attempts:
        topic_id = attempt.assessment.topic_id
        if not topic_id:
            continue
        for ans in sorted(attempt.answers, key=lambda a: a.created_at):
            outcome = 1.0 if ans.is_correct else 0.0
            per_topic.setdefault(topic_id, []).append(outcome)
    out = {}
    for tid, outcomes in per_topic.items():
        m = PRIOR_MASTERY
        for o in outcomes:
            m = ALPHA * o + (1 - ALPHA) * m
        topic = db.get(models.Topic, tid)
        out[tid] = {
            "mastery": round(m, 4),
            "evidence_count": len(outcomes),
            "title": topic.title if topic else tid,
            "subject_id": topic.subject_id if topic else None,
        }
    return out


def _subject_masteries(
    db: Session, attempts: list[models.Attempt], topics: dict[str, dict]
) -> dict[str, dict]:
    by_subject: dict[str, list[str]] = {}
    for tid, t in topics.items():
        if t["subject_id"]:
            by_subject.setdefault(t["subject_id"], []).append(tid)
    out = {}
    for sid, tids in by_subject.items():
        ev = sum(topics[t]["evidence_count"] for t in tids)
        m = (
            sum(topics[t]["mastery"] * topics[t]["evidence_count"] for t in tids) / ev
            if ev
            else None
        )
        if m is not None:
            subj = db.get(models.Subject, sid)
            out[sid] = {
                "mastery": round(m, 4),
                "evidence_count": ev,
                "code": subj.code if subj else sid,
                "name": subj.name if subj else sid,
            }
    # Subject-direct assessments (no topic): fall back to their raw accuracy.
    direct: dict[str, list[float]] = {}
    for attempt in attempts:
        if (
            attempt.assessment.topic_id is None
            and attempt.assessment.subject_id
            and attempt.accuracy is not None
        ):
            direct.setdefault(attempt.assessment.subject_id, []).append(attempt.accuracy)
    for sid, accs in direct.items():
        if sid not in out and accs:
            subj = db.get(models.Subject, sid)
            out[sid] = {
                "mastery": round(sum(accs) / len(accs), 4),
                "evidence_count": len(accs),
                "code": subj.code if subj else sid,
                "name": subj.name if subj else sid,
            }
    return out


def _trend(accs: list[float]) -> tuple[str | None, float | None]:
    window = accs[-10:]
    if len(window) < 3:
        return None, None
    n = len(window)
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(window) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, window)) / denom if denom else 0.0
    slope = round(slope, 4)
    direction = (
        "improving" if slope > TREND_EPS else "declining" if slope < -TREND_EPS else "stable"
    )
    return direction, slope


def compute_profile_twin(db: Session, profile_id: str) -> dict:
    """Pure recompute from evidence. Same rows in => same dict out."""
    attempts = _evidence(db, profile_id)
    topics = _topic_masteries(db, attempts)
    subjects = _subject_masteries(db, attempts, topics)

    total = sum(len(a.answers) for a in attempts)
    correct = sum(1 for a in attempts for ans in a.answers if ans.is_correct)
    overall_acc = round(correct / total, 4) if total else None
    overall_mastery = None
    if subjects:
        ev = sum(s["evidence_count"] for s in subjects.values())
        overall_mastery = (
            round(sum(s["mastery"] * s["evidence_count"] for s in subjects.values()) / ev, 4)
            if ev
            else None
        )

    accs = [a.accuracy for a in attempts if a.accuracy is not None]
    recent = accs[-10:]
    consistency = (
        round(max(0.0, min(1.0, 1 - statistics.stdev(recent))), 4) if len(recent) >= 2 else None
    )
    direction, slope = _trend(accs)

    # Skill proficiency: self-report prior pulled toward observed accuracy.
    links = db.scalars(
        select(models.ProfileSkill).where(models.ProfileSkill.profile_id == profile_id)
    ).all()
    k = min(total, 20) / 2  # evidence weight 0..10
    skills = {}
    for link in links:
        prior = SKILL_PRIORS.get(link.level, 0.25)
        prof = (
            round((2 * prior + k * overall_acc) / (2 + k), 4) if overall_acc is not None else prior
        )
        skills[link.skill_id] = {
            "proficiency": prof,
            "evidence_count": total,
            "name": link.skill.name,
            "level": link.level,
            # V2-B2 provenance + evidence track (filled below when evidence exists).
            "source": "self_report",
            "mastery": None,
            "confidence": None,
            "skill_evidence_count": 0,
            "correct_count": 0,
            "incorrect_count": 0,
            "trend": None,
            "trend_slope": None,
            "last_updated": None,
        }
    # V2-B2: evidence-derived state merges in WITHOUT touching the
    # self-report track above. Profile-scoped: shared taxonomy skills can
    # never leak another student's evidence into this twin.
    from app.services import skill_mastery

    derived = skill_mastery.skill_states_for_profile(db, profile_id)
    if derived:
        names = {
            s.id: s.name
            for s in db.scalars(
                select(models.Skill).where(models.Skill.id.in_(list(derived)))
            ).all()
        }
        for skid, st in derived.items():
            if skid in skills:
                skills[skid].update(
                    source="self_report+assessed",
                    mastery=st["mastery"],
                    confidence=st["confidence"],
                    skill_evidence_count=st["evidence_count"],
                    correct_count=st["correct_count"],
                    incorrect_count=st["incorrect_count"],
                    trend=st["trend"],
                    trend_slope=st["trend_slope"],
                    last_updated=st["last_updated"],
                )
            else:
                # Case C: evidence-discovered skill, never self-reported.
                skills[skid] = {
                    "proficiency": None,
                    "evidence_count": total,
                    "name": names.get(skid, skid),
                    "level": None,
                    "source": "assessed",
                    "mastery": st["mastery"],
                    "confidence": st["confidence"],
                    "skill_evidence_count": st["evidence_count"],
                    "correct_count": st["correct_count"],
                    "incorrect_count": st["incorrect_count"],
                    "trend": st["trend"],
                    "trend_slope": st["trend_slope"],
                    "last_updated": st["last_updated"],
                }

    lessons = db.scalar(
        select(func.count())
        .select_from(models.LearningProgress)
        .where(
            models.LearningProgress.profile_id == profile_id,
            models.LearningProgress.status == "completed",
        )
    )

    return {
        "overall_mastery": overall_mastery,
        "overall_accuracy": overall_acc,
        "consistency": consistency,
        "trend_direction": direction,
        "trend_slope": slope,
        "total_answers": total,
        "correct_answers": correct,
        "attempts_count": len(attempts),
        "lessons_completed": lessons or 0,
        "subjects": subjects,
        "topics": topics,
        "skills": skills,
    }


def _persist(
    db: Session, profile_id: str, computed: dict, trigger_type: str, trigger_id: str | None
) -> models.TwinSnapshot:
    """Diff vs stored state, upsert everything, append snapshot. Caller commits."""
    state = db.scalar(select(models.TwinState).where(models.TwinState.profile_id == profile_id))
    old_overall = state.overall_mastery if state else None
    if state is None:
        state = models.TwinState(profile_id=profile_id)
        db.add(state)
        db.flush()
    old_dims: dict[tuple[str, str], float] = {}
    for row in db.scalars(
        select(models.TwinSubjectMastery).where(models.TwinSubjectMastery.profile_id == profile_id)
    ):
        old_dims[("subject", row.subject_id)] = row.mastery
    for row in db.scalars(
        select(models.TwinTopicMastery).where(models.TwinTopicMastery.profile_id == profile_id)
    ):
        old_dims[("topic", row.topic_id)] = row.mastery
    old_skill_mastery: dict[str, float | None] = {}
    old_skill_confidence: dict[str, float | None] = {}
    old_skill_trend: dict[str, str | None] = {}
    for row in db.scalars(
        select(models.TwinSkillProficiency).where(
            models.TwinSkillProficiency.profile_id == profile_id
        )
    ):
        old_dims[("skill", row.skill_id)] = row.proficiency
        old_skill_mastery[row.skill_id] = row.mastery
        old_skill_confidence[row.skill_id] = row.confidence
        old_skill_trend[row.skill_id] = row.trend

    def upsert(model, ref_field: str, ref_id: str, value: float, evidence: int) -> None:
        row = db.scalar(
            select(model).where(model.profile_id == profile_id, getattr(model, ref_field) == ref_id)
        )
        if row is None:
            row = model(profile_id=profile_id, **{ref_field: ref_id})
            db.add(row)
        row.mastery = value
        row.evidence_count = evidence

    # V2-C1 evolution events: one structured record per metric that actually
    # moved (same >= DELTA_EPS rule as `changes`). Collected alongside the
    # legacy entries below, then persisted against the snapshot at the end —
    # same transaction, so failed submits leave no partial history.
    events: list[dict] = []

    def _record(
        dimension: str,
        ref: str,
        label: str,
        metric: str,
        old: float | None,
        new: float | None,
        old_label: str | None = None,
        new_label: str | None = None,
    ) -> None:
        events.append(
            {
                "dimension": dimension,
                "ref": ref,
                "label": label,
                "metric": metric,
                "old": old,
                "new": new,
                "old_label": old_label,
                "new_label": new_label,
            }
        )

    changes: list[dict] = []
    for sid, s in computed["subjects"].items():
        key = ("subject", sid)
        if key in old_dims and abs(s["mastery"] - old_dims[key]) >= DELTA_EPS:
            changes.append(
                {
                    "dimension": "subject",
                    "ref": s["code"],
                    "label": s["name"],
                    "old": old_dims[key],
                    "new": s["mastery"],
                    "delta": round(s["mastery"] - old_dims[key], 4),
                }
            )
            _record("subject", sid, s["name"], "mastery", old_dims[key], s["mastery"])
        upsert(models.TwinSubjectMastery, "subject_id", sid, s["mastery"], s["evidence_count"])
    for tid, t in computed["topics"].items():
        key = ("topic", tid)
        if key in old_dims and abs(t["mastery"] - old_dims[key]) >= DELTA_EPS:
            changes.append(
                {
                    "dimension": "topic",
                    "ref": tid,
                    "label": t["title"],
                    "old": old_dims[key],
                    "new": t["mastery"],
                    "delta": round(t["mastery"] - old_dims[key], 4),
                }
            )
            _record("topic", tid, t["title"], "mastery", old_dims[key], t["mastery"])
        upsert(models.TwinTopicMastery, "topic_id", tid, t["mastery"], t["evidence_count"])
    for skid, sk in computed["skills"].items():
        key = ("skill", skid)
        # Self-report track: unchanged rule, guarded for assessed-only rows
        # whose proficiency is None (never diffed, never fabricated).
        old_prof = old_dims.get(key)
        if (
            key in old_dims
            and old_prof is not None
            and sk["proficiency"] is not None
            and abs(sk["proficiency"] - old_prof) >= DELTA_EPS
        ):
            changes.append(
                {
                    "dimension": "skill",
                    "ref": skid,
                    "label": sk["name"],
                    "old": old_prof,
                    "new": sk["proficiency"],
                    "delta": round(sk["proficiency"] - old_prof, 4),
                }
            )
            _record("skill", skid, sk["name"], "proficiency", old_prof, sk["proficiency"])
        # Evidence-derived track: same established rule applied to the new
        # persisted dimension (first appearance upserts silently, like every
        # other new dimension). Entry shape is identical to proficiency
        # moves; provenance lives on the skill rows, not the delta log.
        old_mastery = old_skill_mastery.get(skid)
        if (
            old_mastery is not None
            and sk["mastery"] is not None
            and abs(sk["mastery"] - old_mastery) >= DELTA_EPS
        ):
            changes.append(
                {
                    "dimension": "skill",
                    "ref": skid,
                    "label": sk["name"],
                    "old": old_mastery,
                    "new": sk["mastery"],
                    "delta": round(sk["mastery"] - old_mastery, 4),
                }
            )
            _record("skill", skid, sk["name"], "mastery", old_mastery, sk["mastery"])
        # Confidence track: events only (no changes_json counterpart — the
        # snapshot delta log keeps its exact established shape).
        old_conf = old_skill_confidence.get(skid)
        if (
            old_conf is not None
            and sk["confidence"] is not None
            and abs(sk["confidence"] - old_conf) >= DELTA_EPS
        ):
            _record("skill", skid, sk["name"], "confidence", old_conf, sk["confidence"])
        # Trend: events only on genuine direction transitions (both sides
        # known and different) — slope wobble without a direction change is
        # noise, not evolution.
        old_trend = old_skill_trend.get(skid)
        if old_trend is not None and sk["trend"] is not None and sk["trend"] != old_trend:
            _record(
                "skill",
                skid,
                sk["name"],
                "trend",
                None,
                None,
                old_label=old_trend,
                new_label=sk["trend"],
            )
        row = db.scalar(
            select(models.TwinSkillProficiency).where(
                models.TwinSkillProficiency.profile_id == profile_id,
                models.TwinSkillProficiency.skill_id == skid,
            )
        )
        if row is None:
            row = models.TwinSkillProficiency(profile_id=profile_id, skill_id=skid)
            db.add(row)
        row.proficiency = sk["proficiency"]
        row.evidence_count = sk["evidence_count"]
        row.source = sk["source"]
        row.mastery = sk["mastery"]
        row.confidence = sk["confidence"]
        row.skill_evidence_count = sk["skill_evidence_count"]
        row.correct_count = sk["correct_count"]
        row.incorrect_count = sk["incorrect_count"]
        row.trend = sk["trend"]
        row.trend_slope = sk["trend_slope"]
        last_updated = sk["last_updated"]
        row.last_updated = (
            datetime.fromisoformat(last_updated) if last_updated is not None else None
        )

    state.overall_mastery = computed["overall_mastery"]
    state.overall_accuracy = computed["overall_accuracy"]
    state.consistency = computed["consistency"]
    state.trend_direction = computed["trend_direction"]
    state.trend_slope = computed["trend_slope"]
    state.total_answers = computed["total_answers"]
    state.correct_answers = computed["correct_answers"]
    state.attempts_count = computed["attempts_count"]
    state.version += 1

    summary = _summarize(trigger_type, trigger_id, db, computed, changes, old_overall)
    snap = models.TwinSnapshot(
        profile_id=profile_id,
        created_at=_now(),
        trigger_type=trigger_type,
        trigger_id=trigger_id,
        overall_mastery=computed["overall_mastery"],
        overall_accuracy=computed["overall_accuracy"],
        changes_json=json.dumps(changes),
        summary=summary,
    )
    db.add(snap)
    db.flush()  # assign snap.id (and pending rows) before events reference it
    # V2-C1: one structured event per recorded move, deterministic order,
    # same transaction. Empty `events` means no meaningful change: snapshot
    # still appended (established rule), but no evolution record is faked.
    attempt_id = trigger_id if trigger_type == "assessment_submitted" else None
    for ev in sorted(events, key=lambda e: (e["dimension"], e["ref"], e["metric"])):
        db.add(
            models.TwinEvolutionEvent(
                profile_id=profile_id,
                snapshot_id=snap.id,
                created_at=snap.created_at,
                trigger_type=trigger_type,
                trigger_id=trigger_id,
                attempt_id=attempt_id,
                dimension=ev["dimension"],
                ref=ev["ref"],
                label=ev["label"],
                metric=ev["metric"],
                old_value=ev["old"],
                new_value=ev["new"],
                old_label=ev["old_label"],
                new_label=ev["new_label"],
            )
        )
    return snap


def _summarize(
    trigger_type: str,
    trigger_id: str | None,
    db: Session,
    computed: dict,
    changes: list[dict],
    old_overall: float | None,
) -> str:
    """Template-built from stored rows only — no invented causes."""
    if trigger_type == "assessment_submitted" and trigger_id:
        attempt = db.get(models.Attempt, trigger_id)
        if attempt:
            acc = (
                f"{round((attempt.accuracy or 0) * 100)}%"
                if attempt.accuracy is not None
                else "n/a"
            )
            base = (
                f"Attempt {attempt.attempt_number} on '{attempt.assessment.title}' "
                f"scored {attempt.score}/{attempt.max_score} ({acc}). "
            )
        else:
            base = "Assessment submitted. "
    elif trigger_type == "profile_updated":
        base = "Self-reported skills updated. "
    else:
        base = "Learning progress updated. "
    if computed["overall_mastery"] is None:
        return base + "Not enough evidence for mastery yet."
    move = ""
    if old_overall is not None and abs(computed["overall_mastery"] - old_overall) >= DELTA_EPS:
        arrow = "rose" if computed["overall_mastery"] > old_overall else "fell"
        move = f"Overall mastery {arrow} {old_overall:.0%} → {computed['overall_mastery']:.0%}. "
    tops = sorted(changes, key=lambda c: abs(c["delta"]), reverse=True)[:2]
    detail = " ".join(f"{c['label']} moved {c['old']:.0%} → {c['new']:.0%}." for c in tops)
    return (base + move + detail).strip()


def update_after_submit(
    db: Session, profile_id: str, attempt: models.Attempt
) -> tuple[models.TwinSnapshot, dict]:
    """Pipeline step 4-7 of submit: extract, update, persist, snapshot. No commit here."""
    computed = compute_profile_twin(db, profile_id)
    return (
        _persist(db, profile_id, computed, "assessment_submitted", attempt.id),
        computed,
    )


def update_after_profile_change(
    db: Session, profile_id: str, skill_id: str | None
) -> tuple[models.TwinSnapshot, dict]:
    """V2-C1: recompute + persist + snapshot after a self-report skill change.

    Same atomicity contract as submits (caller commits). trigger_id carries
    the affected skill id; attempt references stay NULL, which marks these
    events as originating from the profile/self-report track.
    """
    computed = compute_profile_twin(db, profile_id)
    return (
        _persist(db, profile_id, computed, "profile_updated", skill_id),
        computed,
    )


def list_evolution_events(db: Session, profile_id: str, limit: int = 50) -> list[dict]:
    """Profile-scoped evolution history, newest first, bounded. Never leaks
    another student's events: every row is filtered by profile_id."""
    rows = db.scalars(
        select(models.TwinEvolutionEvent)
        .where(models.TwinEvolutionEvent.profile_id == profile_id)
        .order_by(models.TwinEvolutionEvent.created_at.desc(), models.TwinEvolutionEvent.id.desc())
        .limit(min(max(limit, 1), 100))
    ).all()
    return [
        {
            "id": r.id,
            "profile_id": r.profile_id,
            "snapshot_id": r.snapshot_id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "trigger_type": r.trigger_type,
            "trigger_id": r.trigger_id,
            "attempt_id": r.attempt_id,
            "dimension": r.dimension,
            "ref": r.ref,
            "label": r.label,
            "metric": r.metric,
            "old_value": r.old_value,
            "new_value": r.new_value,
            "old_label": r.old_label,
            "new_label": r.new_label,
        }
        for r in rows
    ]


def read_twin(db: Session, profile_id: str, now: datetime | None = None) -> dict:
    """Persisted state + dimension rows + behavior counters (single payload for clients).

    Retention is computed centrally here (server-authoritative): Web and Mobile
    must never calculate different twin states. `now` may be injected for
    deterministic testing; otherwise the server clock is used.
    """
    computed = compute_profile_twin(db, profile_id)
    state = db.scalar(select(models.TwinState).where(models.TwinState.profile_id == profile_id))
    latest = db.scalars(
        select(models.TwinSnapshot)
        .where(models.TwinSnapshot.profile_id == profile_id)
        .order_by(models.TwinSnapshot.created_at.desc())
        .limit(1)
    ).first()
    # Centralized retention: one calculation for both clients.
    from app.services import retention as _ret

    fresh_map = _ret.retention_for_profile(db, profile_id, now=now)
    def _skill_row(skid: str, sk: dict) -> dict:
        f = fresh_map.get(skid)
        if f is None:
            f = {
                "retention_state": "unknown",
                "retention_score": None,
                "days_since_last_evidence": None,
                "last_evidence_at": None,
            }
        return {
            "skill_id": skid,
            "name": sk["name"],
            "level": sk["level"],
            "proficiency": sk["proficiency"],
            "evidence_count": sk["evidence_count"],
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
        }
    return {
        "has_evidence": computed["attempts_count"] > 0,
        "version": state.version if state else 0,
        "updated_at": state.updated_at.isoformat() if state else None,
        "overall_mastery": computed["overall_mastery"],
        "overall_accuracy": computed["overall_accuracy"],
        "consistency": computed["consistency"],
        "trend_direction": computed["trend_direction"],
        "trend_slope": computed["trend_slope"],
        "total_answers": computed["total_answers"],
        "correct_answers": computed["correct_answers"],
        "attempts_count": computed["attempts_count"],
        "lessons_completed": computed["lessons_completed"],
        "subjects": [
            {
                "subject_id": sid,
                "code": s["code"],
                "name": s["name"],
                "mastery": s["mastery"],
                "evidence_count": s["evidence_count"],
            }
            for sid, s in sorted(
                computed["subjects"].items(), key=lambda kv: kv[1]["mastery"], reverse=True
            )
        ],
        "topics": [
            {
                "topic_id": tid,
                "title": t["title"],
                "subject_id": t["subject_id"],
                "mastery": t["mastery"],
                "evidence_count": t["evidence_count"],
            }
            for tid, t in sorted(
                computed["topics"].items(), key=lambda kv: kv[1]["mastery"], reverse=True
            )
        ],
        "skills": [
            _skill_row(skid, sk)
            for skid, sk in sorted(
                computed["skills"].items(),
                key=lambda kv: (
                    kv[1]["proficiency"]
                    if kv[1]["proficiency"] is not None
                    else (kv[1]["mastery"] if kv[1]["mastery"] is not None else 0.0)
                ),
                reverse=True,
            )
        ],
        "latest_change": latest.summary if latest else None,
    }


def list_snapshots(db: Session, profile_id: str, limit: int = 20) -> list[dict]:
    rows = db.scalars(
        select(models.TwinSnapshot)
        .where(models.TwinSnapshot.profile_id == profile_id)
        .order_by(models.TwinSnapshot.created_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": r.id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "trigger_type": r.trigger_type,
            "trigger_id": r.trigger_id,
            "overall_mastery": r.overall_mastery,
            "overall_accuracy": r.overall_accuracy,
            "changes": json.loads(r.changes_json or "[]"),
            "summary": r.summary,
        }
        for r in rows
    ]
