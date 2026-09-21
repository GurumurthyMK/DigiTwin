"""V2-A3 skill evidence: append-only (answer x skill) evidence events."""

revision = "0008_skill_evidence"
down_revision = "0007_skill_graph"
branch_labels = None
depends_on = None

from alembic import op  # noqa: E402
import sqlalchemy as sa  # noqa: E402


def upgrade() -> None:
    op.create_table(
        "skill_evidence",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "profile_id",
            sa.String(36),
            sa.ForeignKey("student_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "skill_id",
            sa.String(36),
            sa.ForeignKey("skills.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "question_id",
            sa.String(36),
            sa.ForeignKey("questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "attempt_id",
            sa.String(36),
            sa.ForeignKey("attempts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("is_correct", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("attempt_id", "question_id", "skill_id"),
    )
    op.create_index("ix_skill_evidence_profile_id", "skill_evidence", ["profile_id"])
    op.create_index("ix_skill_evidence_skill_id", "skill_evidence", ["skill_id"])
    op.create_index("ix_skill_evidence_question_id", "skill_evidence", ["question_id"])
    op.create_index("ix_skill_evidence_attempt_id", "skill_evidence", ["attempt_id"])


def downgrade() -> None:
    op.drop_index("ix_skill_evidence_attempt_id", table_name="skill_evidence")
    op.drop_index("ix_skill_evidence_question_id", table_name="skill_evidence")
    op.drop_index("ix_skill_evidence_skill_id", table_name="skill_evidence")
    op.drop_index("ix_skill_evidence_profile_id", table_name="skill_evidence")
    op.drop_table("skill_evidence")
