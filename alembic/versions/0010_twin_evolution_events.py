"""V2-C1 twin evolution events: structured per-metric change history."""
revision = "0010_twin_evolution_events"
down_revision = "0009_twin_skill_evidence_state"
branch_labels = None
depends_on = None

from alembic import op  # noqa: E402
import sqlalchemy as sa  # noqa: E402


def upgrade() -> None:
    op.create_table(
        "twin_evolution_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "profile_id",
            sa.String(36),
            sa.ForeignKey("student_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "snapshot_id",
            sa.String(36),
            sa.ForeignKey("twin_snapshots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trigger_type", sa.String(32), nullable=False),
        sa.Column("trigger_id", sa.String(36), nullable=True),
        sa.Column(
            "attempt_id",
            sa.String(36),
            sa.ForeignKey("attempts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("dimension", sa.String(16), nullable=False),
        sa.Column("ref", sa.String(36), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("metric", sa.String(16), nullable=False),
        sa.Column("old_value", sa.Float(), nullable=True),
        sa.Column("new_value", sa.Float(), nullable=True),
        sa.Column("old_label", sa.String(16), nullable=True),
        sa.Column("new_label", sa.String(16), nullable=True),
    )
    op.create_index("ix_twin_evolution_events_profile_id", "twin_evolution_events", ["profile_id"])
    op.create_index(
        "ix_twin_evolution_events_snapshot_id", "twin_evolution_events", ["snapshot_id"]
    )
    op.create_index("ix_twin_evolution_events_attempt_id", "twin_evolution_events", ["attempt_id"])


def downgrade() -> None:
    op.drop_index("ix_twin_evolution_events_attempt_id", table_name="twin_evolution_events")
    op.drop_index("ix_twin_evolution_events_snapshot_id", table_name="twin_evolution_events")
    op.drop_index("ix_twin_evolution_events_profile_id", table_name="twin_evolution_events")
    op.drop_table("twin_evolution_events")
