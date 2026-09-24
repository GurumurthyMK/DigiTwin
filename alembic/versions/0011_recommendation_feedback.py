"""Set 2 adaptive feedback: append-only recommendation interaction history."""
revision = "0011_recommendation_feedback"
down_revision = "0010_twin_evolution_events"
branch_labels = None
depends_on = None

from alembic import op  # noqa: E402
import sqlalchemy as sa  # noqa: E402


def upgrade() -> None:
    op.create_table(
        "recommendation_feedback",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "profile_id",
            sa.String(36),
            sa.ForeignKey("student_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rec_key", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("refs_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_recommendation_feedback_profile_id", "recommendation_feedback", ["profile_id"]
    )
    op.create_index("ix_recommendation_feedback_rec_key", "recommendation_feedback", ["rec_key"])


def downgrade() -> None:
    op.drop_index("ix_recommendation_feedback_rec_key", table_name="recommendation_feedback")
    op.drop_index("ix_recommendation_feedback_profile_id", table_name="recommendation_feedback")
    op.drop_table("recommendation_feedback")
