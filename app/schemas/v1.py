"""Request/response schemas. Versioned implicitly via /api/v1 prefix."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator


# ---- common ----
class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    detail: ErrorDetail


# ---- auth ----
class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class RefreshIn(BaseModel):
    # Optional since Amendment A1: cookie-transport clients (web) refresh via
    # the httpOnly cookie and send an empty body; header clients send the token.
    refresh_token: str | None = None


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"


class UserOut(BaseModel):
    id: str
    email: str
    is_active: bool

    model_config = {"from_attributes": True}


# ---- profile ----
class ProfileOut(BaseModel):
    id: str
    user_id: str
    full_name: str = ""
    date_of_birth: date | None = None
    bio: str | None = None
    timezone: str = "UTC"
    hours_per_week: int = 0
    onboarding_completed: bool = False

    model_config = {"from_attributes": True}


class ProfileUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    date_of_birth: date | None = None
    bio: str | None = None
    timezone: str | None = Field(default=None, max_length=64)
    hours_per_week: int | None = Field(default=None, ge=0, le=168)
    onboarding_completed: bool | None = None


# ---- education ----
class EducationIn(BaseModel):
    level: str = Field(max_length=100)
    institution: str = Field(default="", max_length=200)
    field_of_study: str = Field(default="", max_length=200)
    start_year: int | None = Field(default=None, ge=1900, le=2100)
    end_year: int | None = Field(default=None, ge=1900, le=2100)


class EducationOut(EducationIn):
    id: str
    profile_id: str

    model_config = {"from_attributes": True}


# ---- skills ----
class SkillOut(BaseModel):
    id: str
    name: str

    model_config = {"from_attributes": True}


class ProfileSkillIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    level: Literal["beginner", "intermediate", "advanced"] = "beginner"


class ProfileSkillOut(BaseModel):
    skill: SkillOut
    level: str

    model_config = {"from_attributes": True}


# ---- goals ----
class GoalIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    status: Literal["active", "paused", "completed"] = "active"


class GoalOut(GoalIn):
    id: str
    profile_id: str

    model_config = {"from_attributes": True}


# ---- availability ----
class AvailabilityIn(BaseModel):
    weekday: int = Field(ge=0, le=6)
    start_time: str = Field(pattern=r"^\d{2}:\d{2}$")
    end_time: str = Field(pattern=r"^\d{2}:\d{2}$")

    @field_validator("start_time", "end_time")
    @classmethod
    def _real_clock_time(cls, v: str) -> str:
        hh, mm = int(v[:2]), int(v[3:])
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError("must be a real clock time (00:00-23:59)")
        return v


class AvailabilityOut(AvailabilityIn):
    id: str
    profile_id: str

    model_config = {"from_attributes": True}


# ---- subjects ----
class SubjectOut(BaseModel):
    id: str
    code: str
    name: str
    description: str | None = None

    model_config = {"from_attributes": True}


class ProfileSubjectsUpdate(BaseModel):
    subject_ids: list[str]


# ---- learning (Phase 2A) ----
class TopicOut(BaseModel):
    id: str
    subject_id: str
    title: str
    description: str | None = None
    order_index: int = 0
    content_count: int = 0
    completed_count: int = 0  # for the requesting student (0 when anonymous)

    model_config = {"from_attributes": True}


class ContentOut(BaseModel):
    id: str
    topic_id: str
    kind: str
    title: str
    body: str | None = None
    url: str | None = None
    duration_minutes: int = 0
    order_index: int = 0

    model_config = {"from_attributes": True}


class ProgressUpdate(BaseModel):
    status: Literal["not_started", "in_progress", "completed"]


class ProgressOut(BaseModel):
    content_item_id: str
    status: str

    model_config = {"from_attributes": True}


# ---- assessments (Phase 2A) ----
class OptionOut(BaseModel):
    id: str
    label: str

    model_config = {"from_attributes": True}


class QuestionOut(BaseModel):
    """Taking view: options WITHOUT correctness. Never leak is_correct pre-submit."""

    id: str
    kind: str
    prompt: str
    points: int
    options: list[OptionOut]

    model_config = {"from_attributes": True}


class AssessmentSummary(BaseModel):
    id: str
    subject_id: str | None
    topic_id: str | None
    title: str
    description: str | None = None
    difficulty: str
    time_limit_seconds: int | None = None
    question_count: int = 0
    attempts_taken: int = 0  # by the requesting student

    model_config = {"from_attributes": True}


class AssessmentDetail(AssessmentSummary):
    questions: list[QuestionOut]


class AnswerIn(BaseModel):
    question_id: str
    selected_option_id: str | None = None


class AnswersUpdate(BaseModel):
    answers: list[AnswerIn]


class AttemptOut(BaseModel):
    id: str
    assessment_id: str
    attempt_number: int
    status: str
    time_limit_seconds: int | None = None
    started_at: object
    submitted_at: object | None = None
    time_taken_seconds: int | None = None
    overtime: bool = False
    score: int | None = None
    max_score: int | None = None
    accuracy: float | None = None
    questions: list[QuestionOut] = []
    selected: dict[str, str | None] = {}  # question_id -> selected_option_id
    server_now: object | None = None  # lets clients compute remaining time off server clock

    model_config = {"from_attributes": True}


class ResultAnswerOut(BaseModel):
    question_id: str
    prompt: str
    selected_option_id: str | None
    correct_option_id: str | None
    is_correct: bool | None
    points_awarded: int | None
    points: int
    explanation: str | None = None


class AttemptResultOut(BaseModel):
    id: str
    assessment_id: str
    assessment_title: str
    attempt_number: int
    score: int
    max_score: int
    accuracy: float
    time_taken_seconds: int
    overtime: bool
    submitted_at: object
    answers: list[ResultAnswerOut]


class AttemptHistoryOut(BaseModel):
    id: str
    assessment_id: str
    assessment_title: str
    subject_code: str | None = None
    topic_title: str | None = None
    difficulty: str | None = None
    attempt_number: int
    status: str
    score: int | None = None
    max_score: int | None = None
    accuracy: float | None = None
    time_taken_seconds: int | None = None
    submitted_at: object | None = None


# ---- performance (Phase 2A: aggregates over REAL submitted attempts only) ----
class SubjectPerformance(BaseModel):
    subject_code: str
    subject_name: str
    attempts: int
    avg_accuracy: float | None
    avg_score_pct: float | None


class PerformanceOut(BaseModel):
    submitted_attempts: int
    in_progress_attempts: int
    completed_lessons: int
    avg_accuracy: float | None
    by_subject: list[SubjectPerformance]


# ---- digital twin (Phase 3A: derived state, documented in docs/twin-metrics.md) ----
class TwinSubjectOut(BaseModel):
    subject_id: str
    code: str
    name: str
    mastery: float
    evidence_count: int


class TwinTopicOut(BaseModel):
    topic_id: str
    title: str
    subject_id: str | None = None
    mastery: float
    evidence_count: int


class TwinSkillOut(BaseModel):
    skill_id: str
    name: str
    # Self-report track (V1 meaning, unchanged): None when never self-reported.
    level: str | None = None
    proficiency: float | None = None
    evidence_count: int
    # Provenance: self_report | assessed | self_report+assessed.
    source: str = "self_report"
    # Evidence-derived track (V2-B2): None/0 when no SkillEvidence exists.
    mastery: float | None = None
    confidence: float | None = None
    skill_evidence_count: int = 0
    correct_count: int = 0
    incorrect_count: int = 0
    trend: str | None = None
    trend_slope: float | None = None
    last_updated: str | None = None


class TwinOut(BaseModel):
    has_evidence: bool
    version: int
    updated_at: object | None = None
    overall_mastery: float | None = None
    overall_accuracy: float | None = None
    consistency: float | None = None
    trend_direction: str | None = None
    trend_slope: float | None = None
    total_answers: int = 0
    correct_answers: int = 0
    attempts_count: int = 0
    lessons_completed: int = 0
    subjects: list[TwinSubjectOut] = []
    topics: list[TwinTopicOut] = []
    skills: list[TwinSkillOut] = []
    latest_change: str | None = None


class TwinChangeOut(BaseModel):
    dimension: str
    ref: str
    label: str
    old: float
    new: float
    delta: float


class TwinSnapshotOut(BaseModel):
    id: str
    created_at: object | None = None
    trigger_type: str
    trigger_id: str | None = None
    overall_mastery: float | None = None
    overall_accuracy: float | None = None
    changes: list[TwinChangeOut] = []
    summary: str = ""


class TwinEvolutionEventOut(BaseModel):
    id: str
    created_at: object | None = None
    trigger_type: str
    trigger_id: str | None = None
    attempt_id: str | None = None
    snapshot_id: str
    dimension: str
    ref: str
    label: str
    metric: str
    old_value: float | None = None
    new_value: float | None = None
    old_label: str | None = None
    new_label: str | None = None


# ---- AI intelligence (Phase 4A: structured objects, never prose-to-parse) ----
class EvidenceRefOut(BaseModel):
    kind: str  # topic|subject|skill|attempt|attempts|prediction
    id: str = ""
    label: str = ""
    detail: str = ""


class InsightOut(BaseModel):
    type: str  # strength|weakness|trend|behavior|risk
    title: str
    explanation: str
    evidence: list[EvidenceRefOut] = []
    confidence: str  # low|medium|high
    priority: int  # 1 highest
    generated_at: object | None = None


class PredictionOut(BaseModel):
    status: str  # ready|insufficient_data
    requires: str | None = None
    expected: float | None = None
    low: float | None = None
    high: float | None = None
    confidence: str | None = None
    evidence_n: int = 0
    backtest_mae: float | None = None
    backtest_n: int = 0
    method: str = ""
    engine: str = ""
    disclaimer: str = ""
    generated_at: object | None = None


class RecommendationOut(BaseModel):
    kind: str  # study_next|revise|skill
    title: str
    reason: str
    evidence: list[EvidenceRefOut] = []
    priority: int
    est_minutes: int = 10
    refs: dict = {}
    generated_at: object | None = None


class StudyPlanOut(BaseModel):
    today: list[RecommendationOut] = []
    queue: list[RecommendationOut] = []
    engine: str = ""
    generated_at: object | None = None


class CareerFactorOut(BaseModel):
    dimension: str
    label: str
    value: float | None = None
    weight: float = 0.0
    evidence: int | None = None
    hint: str | None = None


class CareerMatchOut(BaseModel):
    career_id: str
    title: str
    blurb: str = ""
    score: float
    coverage: float
    confidence: str
    factors: list[CareerFactorOut] = []
    missing: list[CareerFactorOut] = []
    aligned_goals: list[str] = []
    generated_at: object | None = None


class CareerOut(BaseModel):
    matches: list[CareerMatchOut] = []
    taxonomy_version: str = ""
    engine: str = ""
    disclaimer: str = ""
    generated_at: object | None = None


# ---- product layer (Phase 5A: dashboards, mentor, notifications) ----
class OverviewAttemptOut(BaseModel):
    id: str
    assessment_title: str
    attempt_number: int


class OverviewOut(BaseModel):
    display_name: str
    onboarding_completed: bool = False
    overall_mastery: float | None = None
    overall_accuracy: float | None = None
    trend_direction: str | None = None
    latest_change: str | None = None
    attempts_count: int = 0
    lessons_completed: int = 0
    today: list[RecommendationOut] = []
    urgent: list[InsightOut] = []
    in_progress: list[OverviewAttemptOut] = []
    unread_notifications: int = 0


class MentorFollowUp(BaseModel):
    key: str
    question: str


class MentorAnswerOut(BaseModel):
    key: str
    question: str
    answer: str
    evidence: list[EvidenceRefOut] = []
    follow_ups: list[MentorFollowUp] = []


class MentorQuestionsOut(BaseModel):
    questions: list[MentorFollowUp] = []


class NotificationOut(BaseModel):
    id: str
    kind: str
    title: str
    body: str = ""
    ref_type: str | None = None
    ref_id: str | None = None
    read: bool = False
    created_at: object | None = None
    live: bool = False


class NotificationPrefsOut(BaseModel):
    twin_change_enabled: bool = True
    risk_enabled: bool = True
    plan_enabled: bool = True

    model_config = {"from_attributes": True}


class NotificationPrefsIn(BaseModel):
    twin_change_enabled: bool | None = None
    risk_enabled: bool | None = None
    plan_enabled: bool | None = None


# ---- account self-service ----
class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


class AccountDelete(BaseModel):
    password: str
