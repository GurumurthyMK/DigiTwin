"""Phase 2B: single-active-attempt backstop (double-tap Start race)."""
revision = "0003_single_active_attempt"
down_revision = "0002_phase2a"
branch_labels = None
depends_on = None

from alembic import op  # noqa: E402
import sqlalchemy as sa  # noqa: E402


def upgrade() -> None:
    op.create_index(
        "uq_attempts_single_active",
        "attempts",
        ["assessment_id", "profile_id"],
        unique=True,
        sqlite_where=sa.text("status = 'in_progress'"),
        postgresql_where=sa.text("status = 'in_progress'"),
    )


def downgrade() -> None:
    op.drop_index("uq_attempts_single_active", table_name="attempts")
