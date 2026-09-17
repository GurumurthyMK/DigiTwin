"""Phase 1A entities: users, refresh tokens, student profiles,
education, skills, career goals, study availability, subjects.

Deliberately scoped: no assessments, twin state, AI tables yet
(those arrive in Phases 2-4).
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin


class User(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    profile: Mapped["StudentProfile | None"] = relationship(
        back_populates="user", cascade="all,delete"
    )
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        back_populates="user", cascade="all,delete"
    )


class RefreshToken(Base, UUIDMixin):
    __tablename__ = "refresh_tokens"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="refresh_tokens")


class StudentProfile(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "student_profiles"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    full_name: Mapped[str] = mapped_column(String(200), default="")
    date_of_birth: Mapped[object | None] = mapped_column(Date, nullable=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    hours_per_week: Mapped[int] = mapped_column(Integer, default=0)
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped[User] = relationship(back_populates="profile")
    education: Mapped[list["EducationInfo"]] = relationship(
        back_populates="profile", cascade="all,delete"
    )
    profile_skills: Mapped[list["ProfileSkill"]] = relationship(
        back_populates="profile", cascade="all,delete"
    )
    goals: Mapped[list["CareerGoal"]] = relationship(back_populates="profile", cascade="all,delete")
    availability: Mapped[list["StudyAvailability"]] = relationship(
        back_populates="profile", cascade="all,delete"
    )
    profile_subjects: Mapped[list["ProfileSubject"]] = relationship(
        back_populates="profile", cascade="all,delete"
    )


class EducationInfo(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "education_infos"

    profile_id: Mapped[str] = mapped_column(
        ForeignKey("student_profiles.id", ondelete="CASCADE"), index=True
    )
    level: Mapped[str] = mapped_column(String(100))  # e.g. high_school, bachelor, master
    institution: Mapped[str] = mapped_column(String(200), default="")
    field_of_study: Mapped[str] = mapped_column(String(200), default="")
    start_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_year: Mapped[int | None] = mapped_column(Integer, nullable=True)

    profile: Mapped[StudentProfile] = relationship(back_populates="education")


class Skill(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "skills"

    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)

    profile_links: Mapped[list["ProfileSkill"]] = relationship(back_populates="skill")


class ProfileSkill(Base, UUIDMixin):
    __tablename__ = "profile_skills"
    __table_args__ = (UniqueConstraint("profile_id", "skill_id"),)

    profile_id: Mapped[str] = mapped_column(
        ForeignKey("student_profiles.id", ondelete="CASCADE"), index=True
    )
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"), index=True)
    level: Mapped[str] = mapped_column(
        String(32), default="beginner"
    )  # beginner|intermediate|advanced

    profile: Mapped[StudentProfile] = relationship(back_populates="profile_skills")
    skill: Mapped[Skill] = relationship(back_populates="profile_links")


class CareerGoal(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "career_goals"

    profile_id: Mapped[str] = mapped_column(
        ForeignKey("student_profiles.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active")  # active|paused|completed

    profile: Mapped[StudentProfile] = relationship(back_populates="goals")


class StudyAvailability(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "study_availability"
    __table_args__ = (UniqueConstraint("profile_id", "weekday", "start_time", "end_time"),)

    profile_id: Mapped[str] = mapped_column(
        ForeignKey("student_profiles.id", ondelete="CASCADE"), index=True
    )
    weekday: Mapped[int] = mapped_column(Integer)  # 0=Mon..6=Sun
    start_time: Mapped[str] = mapped_column(String(5))  # HH:MM
    end_time: Mapped[str] = mapped_column(String(5))

    profile: Mapped[StudentProfile] = relationship(back_populates="availability")


class Subject(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "subjects"

    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    profile_links: Mapped[list["ProfileSubject"]] = relationship(back_populates="subject")
    topics: Mapped[list["Topic"]] = relationship(back_populates="subject", cascade="all,delete")


class ProfileSubject(Base, UUIDMixin):
    __tablename__ = "profile_subjects"
    __table_args__ = (UniqueConstraint("profile_id", "subject_id"),)

    profile_id: Mapped[str] = mapped_column(
        ForeignKey("student_profiles.id", ondelete="CASCADE"), index=True
    )
    subject_id: Mapped[str] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), index=True
    )

    profile: Mapped[StudentProfile] = relationship(back_populates="profile_subjects")
    subject: Mapped[Subject] = relationship(back_populates="profile_links")


# ---------------- Phase 2A: learning & assessment ----------------


class Topic(Base, UUIDMixin, TimestampMixin):
    """A subject is divided into ordered topics. Content + assessments hang off these."""

    __tablename__ = "topics"

    subject_id: Mapped[str] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, default=0)

    subject: Mapped[Subject] = relationship(back_populates="topics")
    content_items: Mapped[list["ContentItem"]] = relationship(
        back_populates="topic", cascade="all,delete", order_by="ContentItem.order_index"
    )
    assessments: Mapped[list["Assessment"]] = relationship(back_populates="topic")


class ContentItem(Base, UUIDMixin, TimestampMixin):
    """One learnable unit. `kind` is an open string (lesson/article/video/…)
    so future content types need no schema change; type-specific data lives in `url`/`body`."""

    __tablename__ = "content_items"

    topic_id: Mapped[str] = mapped_column(ForeignKey("topics.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="lesson")
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str | None] = mapped_column(Text, nullable=True)  # markdown
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=0)
    order_index: Mapped[int] = mapped_column(Integer, default=0)

    topic: Mapped[Topic] = relationship(back_populates="content_items")
    progress: Mapped[list["LearningProgress"]] = relationship(
        back_populates="content_item", cascade="all,delete"
    )


class LearningProgress(Base, UUIDMixin, TimestampMixin):
    """Student state per content item. Upserted; deeper history is out of scope for Phase 2A."""

    __tablename__ = "learning_progress"
    __table_args__ = (UniqueConstraint("profile_id", "content_item_id"),)

    profile_id: Mapped[str] = mapped_column(
        ForeignKey("student_profiles.id", ondelete="CASCADE"), index=True
    )
    content_item_id: Mapped[str] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="not_started")

    profile: Mapped[StudentProfile] = relationship()
    content_item: Mapped[ContentItem] = relationship(back_populates="progress")


class Assessment(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "assessments"

    subject_id: Mapped[str | None] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    topic_id: Mapped[str | None] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    difficulty: Mapped[str] = mapped_column(String(16), default="medium")
    time_limit_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_published: Mapped[bool] = mapped_column(Boolean, default=True)

    subject: Mapped[Subject | None] = relationship()
    topic: Mapped[Topic | None] = relationship(back_populates="assessments")
    questions: Mapped[list["Question"]] = relationship(
        back_populates="assessment", cascade="all,delete", order_by="Question.order_index"
    )
    attempts: Mapped[list["Attempt"]] = relationship(
        back_populates="assessment", cascade="all,delete"
    )


class Question(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "questions"

    assessment_id: Mapped[str] = mapped_column(
        ForeignKey("assessments.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32), default="single_choice")
    prompt: Mapped[str] = mapped_column(Text)
    points: Mapped[int] = mapped_column(Integer, default=1)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, default=0)

    assessment: Mapped[Assessment] = relationship(back_populates="questions")
    options: Mapped[list["QuestionOption"]] = relationship(
        back_populates="question", cascade="all,delete", order_by="QuestionOption.order_index"
    )


class QuestionOption(Base, UUIDMixin):
    __tablename__ = "question_options"

    question_id: Mapped[str] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(Text)
    is_correct: Mapped[bool] = mapped_column(Boolean, default=False)
    order_index: Mapped[int] = mapped_column(Integer, default=0)

    question: Mapped[Question] = relationship(back_populates="options")


class Attempt(Base, UUIDMixin, TimestampMixin):
    """One assessment sitting. Rows are append-only: submitted attempts are never
    mutated; history is the raw record Phase 3 will consume."""

    __tablename__ = "attempts"
    __table_args__ = (
        # Double-tap Start / concurrent resume must never fork two sittings:
        # the DB backstops the check-then-insert in start_attempt.
        Index(
            "uq_attempts_single_active",
            "assessment_id",
            "profile_id",
            unique=True,
            sqlite_where=text("status = 'in_progress'"),
            postgresql_where=text("status = 'in_progress'"),
        ),
    )

    assessment_id: Mapped[str] = mapped_column(
        ForeignKey("assessments.id", ondelete="CASCADE"), index=True
    )
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("student_profiles.id", ondelete="CASCADE"), index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="in_progress")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    time_taken_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    overtime: Mapped[bool] = mapped_column(Boolean, default=False)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    accuracy: Mapped[float | None] = mapped_column(nullable=True)

    assessment: Mapped[Assessment] = relationship(back_populates="attempts")
    answers: Mapped[list["Answer"]] = relationship(back_populates="attempt", cascade="all,delete")


class Answer(Base, UUIDMixin, TimestampMixin):
    """Draft answers carry no correctness; grading stamps is_correct/points on submit."""

    __tablename__ = "answers"
    __table_args__ = (UniqueConstraint("attempt_id", "question_id"),)

    attempt_id: Mapped[str] = mapped_column(
        ForeignKey("attempts.id", ondelete="CASCADE"), index=True
    )
    question_id: Mapped[str] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), index=True
    )
    selected_option_id: Mapped[str | None] = mapped_column(
        ForeignKey("question_options.id", ondelete="SET NULL"), nullable=True
    )
    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    points_awarded: Mapped[int | None] = mapped_column(Integer, nullable=True)

    attempt: Mapped[Attempt] = relationship(back_populates="answers")


# ---------------- Phase 3A: digital twin ----------------
# The twin is DERIVED state: every row here is recomputed from submitted
# attempts + learning progress (see docs/twin-metrics.md + twin_service).
# Raw evidence (attempts/answers) is never modified by the twin pipeline.


class TwinState(Base, UUIDMixin, TimestampMixin):
    """One row per student: roll-up aggregates. Detail lives in the per-dimension tables."""

    __tablename__ = "twin_states"

    profile_id: Mapped[str] = mapped_column(
        ForeignKey("student_profiles.id", ondelete="CASCADE"), unique=True, index=True
    )
    overall_mastery: Mapped[float | None] = mapped_column(nullable=True)  # 0..1
    overall_accuracy: Mapped[float | None] = mapped_column(nullable=True)  # 0..1
    consistency: Mapped[float | None] = mapped_column(nullable=True)  # 0..1, needs >=2 attempts
    trend_direction: Mapped[str | None] = mapped_column(String(16), nullable=True)
    trend_slope: Mapped[float | None] = mapped_column(nullable=True)
    total_answers: Mapped[int] = mapped_column(Integer, default=0)
    correct_answers: Mapped[int] = mapped_column(Integer, default=0)
    attempts_count: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=0)  # increments per update


class TwinSubjectMastery(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "twin_subject_mastery"
    __table_args__ = (UniqueConstraint("profile_id", "subject_id"),)

    profile_id: Mapped[str] = mapped_column(
        ForeignKey("student_profiles.id", ondelete="CASCADE"), index=True
    )
    subject_id: Mapped[str] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), index=True
    )
    mastery: Mapped[float] = mapped_column()  # 0..1
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)  # answers behind it


class TwinTopicMastery(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "twin_topic_mastery"
    __table_args__ = (UniqueConstraint("profile_id", "topic_id"),)

    profile_id: Mapped[str] = mapped_column(
        ForeignKey("student_profiles.id", ondelete="CASCADE"), index=True
    )
    topic_id: Mapped[str] = mapped_column(ForeignKey("topics.id", ondelete="CASCADE"), index=True)
    mastery: Mapped[float] = mapped_column()  # 0..1
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)


class TwinSkillProficiency(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "twin_skill_proficiency"
    __table_args__ = (UniqueConstraint("profile_id", "skill_id"),)

    profile_id: Mapped[str] = mapped_column(
        ForeignKey("student_profiles.id", ondelete="CASCADE"), index=True
    )
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"), index=True)
    proficiency: Mapped[float] = mapped_column()  # 0..1
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)


class TwinSnapshot(Base, UUIDMixin):
    """Append-only history: what the twin looked like after each triggering
    event, what changed vs the previous snapshot, and which evidence caused it."""

    __tablename__ = "twin_snapshots"

    profile_id: Mapped[str] = mapped_column(
        ForeignKey("student_profiles.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    trigger_type: Mapped[str] = mapped_column(String(32))  # assessment_submitted|progress_updated
    trigger_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # attempt/content id
    overall_mastery: Mapped[float | None] = mapped_column(nullable=True)
    overall_accuracy: Mapped[float | None] = mapped_column(nullable=True)
    changes_json: Mapped[str] = mapped_column(Text, default="[]")
    summary: Mapped[str] = mapped_column(Text, default="")


# ---------------- Phase 5A: product layer ----------------


class Notification(Base, UUIDMixin, TimestampMixin):
    """Stored, event-generated notices. Read-state per row; capped per profile.
    Live reminders (unfinished sittings) are computed at read time, not stored."""

    __tablename__ = "notifications"

    profile_id: Mapped[str] = mapped_column(
        ForeignKey("student_profiles.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32))  # twin_change|risk|plan
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    ref_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ref_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NotificationPrefs(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "notification_prefs"

    profile_id: Mapped[str] = mapped_column(
        ForeignKey("student_profiles.id", ondelete="CASCADE"), unique=True, index=True
    )
    twin_change_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    risk_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    plan_enabled: Mapped[bool] = mapped_column(Boolean, default=True)


# ---------------- Account self-service ----------------


class PasswordResetToken(Base, UUIDMixin):
    """Single-use, short-lived reset token. Only the SHA-256 hash is stored."""

    __tablename__ = "password_reset_tokens"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
