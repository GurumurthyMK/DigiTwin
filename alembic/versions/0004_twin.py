"""Phase 3A: digital twin tables (derived state + append-only snapshots)."""
revision = "0004_twin"
down_revision = "0003_single_active_attempt"
branch_labels = None
depends_on = None

from alembic import op  # noqa: E402
import sqlalchemy as sa  # noqa: E402


def upgrade() -> None:
    op.create_table(
        "twin_states",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("profile_id", sa.String(36), sa.ForeignKey("student_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("overall_mastery", sa.Float(), nullable=True),
        sa.Column("overall_accuracy", sa.Float(), nullable=True),
        sa.Column("consistency", sa.Float(), nullable=True),
        sa.Column("trend_direction", sa.String(16), nullable=True),
        sa.Column("trend_slope", sa.Float(), nullable=True),
        sa.Column("total_answers", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("correct_answers", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempts_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_twin_states_profile_id", "twin_states", ["profile_id"], unique=True)
    for table, ref, ref_table in [
        ("twin_subject_mastery", "subject_id", "subjects"),
        ("twin_topic_mastery", "topic_id", "topics"),
        ("twin_skill_proficiency", "skill_id", "skills"),
    ]:
        op.create_table(
            table,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("profile_id", sa.String(36), sa.ForeignKey("student_profiles.id", ondelete="CASCADE"), nullable=False),
            sa.Column(ref, sa.String(36), sa.ForeignKey(f"{ref_table}.id", ondelete="CASCADE"), nullable=False),
            sa.Column("mastery" if "skill" not in table else "proficiency", sa.Float(), nullable=False),
            sa.Column("evidence_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("profile_id", ref),
        )
        op.create_index(f"ix_{table}_profile_id", table, ["profile_id"])
    op.create_table(
        "twin_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("profile_id", sa.String(36), sa.ForeignKey("student_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trigger_type", sa.String(32), nullable=False),
        sa.Column("trigger_id", sa.String(36), nullable=True),
        sa.Column("overall_mastery", sa.Float(), nullable=True),
        sa.Column("overall_accuracy", sa.Float(), nullable=True),
        sa.Column("changes_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index("ix_twin_snapshots_profile_id", "twin_snapshots", ["profile_id"])


def downgrade() -> None:
    for t in ["twin_snapshots", "twin_skill_proficiency", "twin_topic_mastery", "twin_subject_mastery", "twin_states"]:
        op.drop_table(t)
