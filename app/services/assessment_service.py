"""Assessment engine. THE backend is authoritative for state, scoring and results.

Clients never send scores. Correctness (`is_correct`) never leaves the server
until an attempt is submitted. Submitted attempts are append-only history.
"""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.errors import AppError
from app.db import models
from app.schemas import v1 as s


def _now() -> datetime:
    return datetime.now(UTC)


def _as_aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _profile_id(db: Session, user_id: str) -> str:
    from app.services.profile_service import get_my_profile

    return get_my_profile(db, user_id).id


def _get_assessment(
    db: Session, assessment_id: str, published_only: bool = True
) -> models.Assessment:
    a = db.get(models.Assessment, assessment_id)
    if not a or (published_only and not a.is_published):
        raise AppError("not_found", "Assessment not found.", 404)
    return a


def _own_attempt(db: Session, profile_id: str, attempt_id: str) -> models.Attempt:
    attempt = db.get(models.Attempt, attempt_id)
    if not attempt or attempt.profile_id != profile_id:
        raise AppError("not_found", "Attempt not found.", 404)
    return attempt


def _questions_with_options(db: Session, assessment_id: str) -> list[models.Question]:
    """One round-trip for questions + options (never N+1 per question)."""
    return db.scalars(
        select(models.Question)
        .where(models.Question.assessment_id == assessment_id)
        .order_by(models.Question.order_index)
        .options(selectinload(models.Question.options))
    ).all()


def _taking_view(db: Session, attempt: models.Attempt) -> s.AttemptOut:
    questions = _questions_with_options(db, attempt.assessment_id)
    selected = {a.question_id: a.selected_option_id for a in attempt.answers}
    assessment = db.get(models.Assessment, attempt.assessment_id)
    return s.AttemptOut(
        id=attempt.id,
        assessment_id=attempt.assessment_id,
        attempt_number=attempt.attempt_number,
        status=attempt.status,
        time_limit_seconds=assessment.time_limit_seconds if assessment else None,
        started_at=_as_aware(attempt.started_at),
        submitted_at=_as_aware(attempt.submitted_at),
        time_taken_seconds=attempt.time_taken_seconds,
        overtime=attempt.overtime,
        score=attempt.score,
        max_score=attempt.max_score,
        accuracy=attempt.accuracy,
        questions=[
            s.QuestionOut(
                id=q.id,
                kind=q.kind,
                prompt=q.prompt,
                points=q.points,
                options=[s.OptionOut(id=o.id, label=o.label) for o in q.options],
            )
            for q in questions
        ],
        selected=selected,
        server_now=_now(),
    )


def list_assessments(
    db: Session, profile_id: str, subject_id: str | None = None, topic_id: str | None = None
) -> list[s.AssessmentSummary]:
    stmt = select(models.Assessment).where(models.Assessment.is_published.is_(True))
    if subject_id:
        stmt = stmt.where(models.Assessment.subject_id == subject_id)
    if topic_id:
        stmt = stmt.where(models.Assessment.topic_id == topic_id)
    out = []
    assessments = db.scalars(
        stmt.order_by(models.Assessment.title).options(selectinload(models.Assessment.questions))
    ).all()
    taken_by_aid = dict(
        db.execute(
            select(models.Attempt.assessment_id, func.count())
            .where(
                models.Attempt.profile_id == profile_id,
                models.Attempt.assessment_id.in_([a.id for a in assessments]),
            )
            .group_by(models.Attempt.assessment_id)
        ).all()
    )
    for a in assessments:
        out.append(
            s.AssessmentSummary(
                id=a.id,
                subject_id=a.subject_id,
                topic_id=a.topic_id,
                title=a.title,
                description=a.description,
                difficulty=a.difficulty,
                time_limit_seconds=a.time_limit_seconds,
                question_count=len(a.questions),
                attempts_taken=taken_by_aid.get(a.id, 0),
            )
        )
    return out


def get_assessment_detail(db: Session, profile_id: str, assessment_id: str) -> s.AssessmentDetail:
    a = _get_assessment(db, assessment_id)
    questions = _questions_with_options(db, a.id)
    taken = (
        db.scalar(
            select(func.count())
            .select_from(models.Attempt)
            .where(models.Attempt.assessment_id == a.id, models.Attempt.profile_id == profile_id)
        )
        or 0
    )
    return s.AssessmentDetail(
        id=a.id,
        subject_id=a.subject_id,
        topic_id=a.topic_id,
        title=a.title,
        description=a.description,
        difficulty=a.difficulty,
        time_limit_seconds=a.time_limit_seconds,
        question_count=len(questions),
        attempts_taken=taken,
        questions=[
            s.QuestionOut(
                id=q.id,
                kind=q.kind,
                prompt=q.prompt,
                points=q.points,
                options=[s.OptionOut(id=o.id, label=o.label) for o in q.options],
            )
            for q in questions
        ],
    )


def start_attempt(db: Session, user_id: str, assessment_id: str) -> tuple[s.AttemptOut, bool]:
    """Returns (attempt, created). Resumes an in-progress attempt so crashes and
    app-backgrounding never lose a sitting — one active attempt per student."""
    profile_id = _profile_id(db, user_id)
    _get_assessment(db, assessment_id)
    existing = db.scalar(
        select(models.Attempt).where(
            models.Attempt.assessment_id == assessment_id,
            models.Attempt.profile_id == profile_id,
            models.Attempt.status == "in_progress",
        )
    )
    if existing:
        return _taking_view(db, existing), False
    number = (
        db.scalar(
            select(func.count())
            .select_from(models.Attempt)
            .where(
                models.Attempt.assessment_id == assessment_id,
                models.Attempt.profile_id == profile_id,
            )
        )
        or 0
    ) + 1
    attempt = models.Attempt(
        assessment_id=assessment_id, profile_id=profile_id, attempt_number=number, started_at=_now()
    )
    db.add(attempt)
    try:
        db.commit()
    except IntegrityError:
        # Lost a concurrent start race: the winner holds the single active
        # sitting (partial unique index) — resume it instead of forking.
        db.rollback()
        winner = db.scalar(
            select(models.Attempt).where(
                models.Attempt.assessment_id == assessment_id,
                models.Attempt.profile_id == profile_id,
                models.Attempt.status == "in_progress",
            )
        )
        if winner is None:
            raise AppError("attempt_conflict", "Attempt is being started, retry.", 409)
        return _taking_view(db, winner), False
    db.refresh(attempt)
    return _taking_view(db, attempt), True


def save_answers(db: Session, user_id: str, attempt_id: str, data: s.AnswersUpdate) -> s.AttemptOut:
    attempt = _own_attempt(db, _profile_id(db, user_id), attempt_id)
    if attempt.status != "in_progress":
        raise AppError("attempt_closed", "Attempt already submitted; answers are frozen.", 409)
    questions = {q.id: q for q in _questions_with_options(db, attempt.assessment_id)}
    for ans in data.answers:
        q = questions.get(ans.question_id)
        if not q:
            raise AppError("invalid_answer", "Question is not part of this assessment.", 422)
        if ans.selected_option_id is not None and ans.selected_option_id not in {
            o.id for o in q.options
        }:
            raise AppError("invalid_answer", "Option does not belong to the question.", 422)
        row = db.scalar(
            select(models.Answer).where(
                models.Answer.attempt_id == attempt.id, models.Answer.question_id == q.id
            )
        )
        if row:
            row.selected_option_id = ans.selected_option_id
        else:
            db.add(
                models.Answer(
                    attempt_id=attempt.id,
                    question_id=q.id,
                    selected_option_id=ans.selected_option_id,
                )
            )
    db.commit()
    db.refresh(attempt)
    return _taking_view(db, attempt)


def submit_attempt(db: Session, user_id: str, attempt_id: str) -> s.AttemptResultOut:
    """Authoritative grading. Idempotent: re-submitting returns the stored result."""
    attempt = _own_attempt(db, _profile_id(db, user_id), attempt_id)
    if attempt.status == "submitted":
        return result_view(db, attempt)
    assessment = db.get(models.Assessment, attempt.assessment_id)
    questions = _questions_with_options(db, assessment.id)
    by_q = {a.question_id: a for a in attempt.answers}
    now = _now()
    started = _as_aware(attempt.started_at) or now
    taken = max(int((now - started).total_seconds()), 0)
    score, maximum = 0, 0
    for q in questions:
        maximum += q.points
        correct = next((o for o in q.options if o.is_correct), None)
        row = by_q.get(q.id)
        hit = bool(row and correct and row.selected_option_id == correct.id)
        if row:
            row.is_correct = hit
            row.points_awarded = q.points if hit else 0
        elif q.id not in by_q:
            db.add(
                models.Answer(
                    attempt_id=attempt.id, question_id=q.id, is_correct=False, points_awarded=0
                )
            )
        if hit:
            score += q.points
    attempt.status = "submitted"
    attempt.submitted_at = now
    attempt.time_taken_seconds = taken
    attempt.overtime = bool(assessment.time_limit_seconds and taken > assessment.time_limit_seconds)
    attempt.score = score
    attempt.max_score = maximum
    attempt.accuracy = (score / maximum) if maximum else 0.0
    # Phase 3A pipeline: evidence is stored above; now extract features,
    # update twin state and append a snapshot — same transaction.
    # NOTE: SessionLocal runs with autoflush=False, so flush explicitly:
    # the evidence query must see this very submission.
    from app.services import twin_service

    db.flush()
    # V2-A3: skill evidence from the authoritative grades above — same
    # transaction, so a failed submit leaves no orphaned evidence behind.
    # V2-B2: the twin recompute below consumes these rows for the
    # evidence-derived skill track (self-report track untouched).
    from app.services import skill_graph

    skill_graph.record_submission_evidence(db, attempt)
    # SessionLocal runs with autoflush=False: flush the new evidence rows so
    # the twin recompute below observes this very submission.
    db.flush()
    snapshot, computed = twin_service.update_after_submit(db, attempt.profile_id, attempt)
    db.commit()
    db.refresh(attempt)
    result = result_view(db, attempt)
    # Phase 5A: event notifications fan out from the stored snapshot (own commit).
    from app.services import notify_service

    notify_service.notify_after_submit(db, attempt.profile_id, snapshot, computed)
    db.commit()
    return result


def read_taking_attempt(db: Session, user_id: str, attempt_id: str) -> s.AttemptOut:
    """Live attempt state for resuming. Refuses submitted attempts (see result)."""
    attempt = _own_attempt(db, _profile_id(db, user_id), attempt_id)
    if attempt.status == "submitted":
        raise AppError("attempt_closed", "Attempt submitted; see /result.", 409)
    return _taking_view(db, attempt)


def read_result(db: Session, user_id: str, attempt_id: str) -> s.AttemptResultOut:
    return result_view(db, _own_attempt(db, _profile_id(db, user_id), attempt_id))


def result_view(db: Session, attempt: models.Attempt) -> s.AttemptResultOut:
    if attempt.status != "submitted":
        raise AppError("not_submitted", "Attempt has not been submitted yet.", 409)
    assessment = db.get(models.Assessment, attempt.assessment_id)
    questions = _questions_with_options(db, assessment.id)
    by_q = {a.question_id: a for a in attempt.answers}
    answers = []
    for q in questions:
        correct = next((o for o in q.options if o.is_correct), None)
        row = by_q.get(q.id)
        answers.append(
            s.ResultAnswerOut(
                question_id=q.id,
                prompt=q.prompt,
                selected_option_id=row.selected_option_id if row else None,
                correct_option_id=correct.id if correct else None,
                is_correct=row.is_correct if row else False,
                points_awarded=row.points_awarded if row else 0,
                points=q.points,
                explanation=q.explanation,
            )
        )
    return s.AttemptResultOut(
        id=attempt.id,
        assessment_id=attempt.assessment_id,
        assessment_title=assessment.title,
        attempt_number=attempt.attempt_number,
        score=attempt.score or 0,
        max_score=attempt.max_score or 0,
        accuracy=attempt.accuracy or 0.0,
        time_taken_seconds=attempt.time_taken_seconds or 0,
        overtime=attempt.overtime,
        submitted_at=_as_aware(attempt.submitted_at),
        answers=answers,
    )


def history(db: Session, user_id: str, limit: int = 50) -> list[s.AttemptHistoryOut]:
    profile_id = _profile_id(db, user_id)
    attempts = db.scalars(
        select(models.Attempt)
        .where(models.Attempt.profile_id == profile_id)
        .order_by(models.Attempt.created_at.desc())
        .limit(limit)
        .options(
            selectinload(models.Attempt.assessment).selectinload(models.Assessment.topic),
            selectinload(models.Attempt.assessment).selectinload(models.Assessment.subject),
        )
    ).all()
    out = []
    for a in attempts:
        subject = a.assessment.subject
        out.append(
            s.AttemptHistoryOut(
                id=a.id,
                assessment_id=a.assessment_id,
                assessment_title=a.assessment.title,
                subject_code=subject.code if subject else None,
                topic_title=a.assessment.topic.title if a.assessment.topic else None,
                difficulty=a.assessment.difficulty,
                attempt_number=a.attempt_number,
                status=a.status,
                score=a.score,
                max_score=a.max_score,
                accuracy=a.accuracy,
                time_taken_seconds=a.time_taken_seconds,
                submitted_at=_as_aware(a.submitted_at),
            )
        )
    return out


def performance(db: Session, user_id: str) -> s.PerformanceOut:
    """Aggregates over SUBMITTED attempts only. Empty inputs → nulls/zeros, never fabrications."""
    from app.services.profile_service import get_my_profile

    profile = get_my_profile(db, user_id)
    submitted = db.scalars(
        select(models.Attempt)
        .where(models.Attempt.profile_id == profile.id, models.Attempt.status == "submitted")
        .options(selectinload(models.Attempt.assessment).selectinload(models.Assessment.subject))
    ).all()
    in_progress = db.scalar(
        select(func.count())
        .select_from(models.Attempt)
        .where(models.Attempt.profile_id == profile.id, models.Attempt.status == "in_progress")
    )
    completed_lessons = db.scalar(
        select(func.count())
        .select_from(models.LearningProgress)
        .where(
            models.LearningProgress.profile_id == profile.id,
            models.LearningProgress.status == "completed",
        )
    )
    by_subj: dict[str, dict] = {}
    for a in submitted:
        subj = a.assessment.subject
        key = subj.code if subj else "—"
        slot = by_subj.setdefault(
            key, {"name": subj.name if subj else "Unassigned", "acc": [], "pct": []}
        )
        if a.accuracy is not None:
            slot["acc"].append(a.accuracy)
        if a.score is not None and a.max_score:
            slot["pct"].append(a.score / a.max_score)
    acc_all = [a.accuracy for a in submitted if a.accuracy is not None]
    return s.PerformanceOut(
        submitted_attempts=len(submitted),
        in_progress_attempts=in_progress or 0,
        completed_lessons=completed_lessons or 0,
        avg_accuracy=(sum(acc_all) / len(acc_all)) if acc_all else None,
        by_subject=[
            s.SubjectPerformance(
                subject_code=code,
                subject_name=v["name"],
                attempts=len(v["acc"]),
                avg_accuracy=(sum(v["acc"]) / len(v["acc"])) if v["acc"] else None,
                avg_score_pct=(sum(v["pct"]) / len(v["pct"])) if v["pct"] else None,
            )
            for code, v in sorted(by_subj.items())
        ],
    )
