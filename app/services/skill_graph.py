"""Skill Graph evidence semantics (V2-A2): validation only, no scoring.

A QuestionSkill edge means exactly this:

    "A submitted answer to this question provides evidence about this skill."

It is content metadata. It is NOT a mastery score, NOT a confidence score,
NOT a recommendation signal, and NOT an ML feature. Validation here never
feeds the Twin; the V2-A3 evidence recorder below IS consumed by the twin
skill track (V2-B2) — TwinSkillProficiency still derives its self-report
track solely from ProfileSkill self-reports (see twin_service).

Related rules established here:
- Multi-skill: a question with N mappings provides evidence to EACH mapped
  skill, with no weighting and no differing strengths.
- Unmapped questions stay fully valid: grading, topic/subject evidence, and
  Twin flow are unchanged; they simply provide no skill-level evidence.
- Skills without questions stay valid (student-declared, future content).
- ProfileSkill ("the student says they have this skill") and QuestionSkill
  ("this question can provide evidence about this skill") are distinct
  concepts and are never merged.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import models


def evidence_skills_for_question(db: Session, question_id: str) -> list[models.Skill]:
    """Skills a submitted answer to this question provides evidence about.

    One entry per mapped skill, no weights. Empty when the question is
    unmapped. Deterministic order (skill name) for stable consumers.
    """
    return list(
        db.scalars(
            select(models.Skill)
            .join(models.QuestionSkill, models.QuestionSkill.skill_id == models.Skill.id)
            .where(models.QuestionSkill.question_id == question_id)
            .order_by(models.Skill.name)
        ).all()
    )


def validate_mapping(
    db: Session, question: models.Question | None, skill: models.Skill | None
) -> list[str]:
    """Deterministic pass/fail checks for one edge. Empty list = valid."""
    violations: list[str] = []
    if question is None:
        return ["unknown_question"]
    if skill is None:
        return ["unknown_skill"]
    if skill.topic_id is None or skill.topic is None:
        # The evidence chain skill -> topic -> subject must be complete.
        violations.append("skill_without_topic")
        return violations
    topic_id = question.assessment.topic_id if question.assessment else None
    if topic_id is not None:
        if skill.topic_id != topic_id:
            violations.append("topic_mismatch")
    elif (
        question.assessment is not None and skill.topic.subject_id != question.assessment.subject_id
    ):
        # Subject-level assessment: fall back to the subject link.
        violations.append("subject_mismatch")
    dupes = db.scalar(
        select(func.count())
        .select_from(models.QuestionSkill)
        .where(
            models.QuestionSkill.question_id == question.id,
            models.QuestionSkill.skill_id == skill.id,
        )
    )
    if dupes is not None and dupes > 1:
        violations.append("duplicate_edge")
    return violations


def audit_skill_graph(db: Session) -> list[dict]:
    """Validate every QuestionSkill edge. Empty list = full pass."""
    findings: list[dict] = []
    edges = db.scalars(select(models.QuestionSkill)).all()
    for edge in edges:
        violations = validate_mapping(db, edge.question, edge.skill)
        if violations:
            findings.append(
                {
                    "question_id": edge.question_id,
                    "skill_id": edge.skill_id,
                    "violations": violations,
                }
            )
    return findings


def record_submission_evidence(db: Session, attempt: models.Attempt) -> int:
    """Create one SkillEvidence row per (graded answer x mapped skill).

    V2-A3 evidence engine. Call only after server-authoritative grading has
    stamped every answer of a submitted attempt, inside the same transaction
    (submit_attempt): a rollback wipes these rows with everything else.
    Draft/in-progress answers (is_correct None) and unmapped questions yield
    nothing. Existing rows are skipped, so the idempotent re-submit path and
    the unique triple can never double-count. Returns rows created.
    """
    answers = db.scalars(select(models.Answer).where(models.Answer.attempt_id == attempt.id)).all()
    if not answers:
        return 0
    skill_ids_by_question: dict[str, list[str]] = {}
    for link in db.scalars(
        select(models.QuestionSkill).where(
            models.QuestionSkill.question_id.in_([a.question_id for a in answers])
        )
    ).all():
        skill_ids_by_question.setdefault(link.question_id, []).append(link.skill_id)
    if not skill_ids_by_question:
        return 0
    existing = set(
        db.execute(
            select(
                models.SkillEvidence.attempt_id,
                models.SkillEvidence.question_id,
                models.SkillEvidence.skill_id,
            ).where(models.SkillEvidence.attempt_id == attempt.id)
        ).all()
    )
    created = 0
    for ans in answers:
        if ans.is_correct is None:
            continue  # not authoritative yet: drafts never produce evidence
        for skill_id in skill_ids_by_question.get(ans.question_id, []):
            key = (attempt.id, ans.question_id, skill_id)
            if key in existing:
                continue
            db.add(
                models.SkillEvidence(
                    profile_id=attempt.profile_id,
                    skill_id=skill_id,
                    question_id=ans.question_id,
                    attempt_id=attempt.id,
                    is_correct=ans.is_correct,
                )
            )
            existing.add(key)
            created += 1
    return created
