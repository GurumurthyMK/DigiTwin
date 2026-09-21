"""V2-B2 twin skill state: provenance + evidence-derived mastery on TwinSkillProficiency."""
revision = "0009_twin_skill_evidence_state"
down_revision = "0008_skill_evidence"
branch_labels = None
depends_on = None

from alembic import op  # noqa: E402
import sqlalchemy as sa  # noqa: E402


def upgrade() -> None:
    # Batch mode: SQLite needs copy-and-move for the nullability change;
    # renders as plain ALTERs on PostgreSQL. Existing rows are preserved.
    with op.batch_alter_table("twin_skill_proficiency") as batch_op:
        batch_op.alter_column("proficiency", existing_type=sa.Float(), nullable=True)
        batch_op.add_column(sa.Column("source", sa.String(32), nullable=True))
        batch_op.add_column(sa.Column("mastery", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("confidence", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("skill_evidence_count", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("correct_count", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("incorrect_count", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("trend", sa.String(16), nullable=True))
        batch_op.add_column(sa.Column("trend_slope", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("last_updated", sa.DateTime(timezone=True), nullable=True))
    # Every pre-existing row was written by the self-report path: mark the
    # source and give the new counters their coherent zero-state (no evidence
    # existed for these rows by construction — evidence recording began with
    # the same release line, and any real evidence recomputes on next submit).
    op.execute("UPDATE twin_skill_proficiency SET source = 'self_report' WHERE source IS NULL")
    op.execute(
        "UPDATE twin_skill_proficiency SET skill_evidence_count = 0 "
        "WHERE skill_evidence_count IS NULL"
    )
    op.execute("UPDATE twin_skill_proficiency SET correct_count = 0 WHERE correct_count IS NULL")
    op.execute(
        "UPDATE twin_skill_proficiency SET incorrect_count = 0 WHERE incorrect_count IS NULL"
    )


def downgrade() -> None:
    # Assessed-only rows cannot survive the NOT NULL restore below; they are
    # purely derived and rebuild from SkillEvidence on the next submit, so
    # drop them first (downgrades in this repo are destructive by convention).
    op.execute("DELETE FROM twin_skill_proficiency WHERE proficiency IS NULL")
    with op.batch_alter_table("twin_skill_proficiency") as batch_op:
        batch_op.drop_column("last_updated")
        batch_op.drop_column("trend_slope")
        batch_op.drop_column("trend")
        batch_op.drop_column("incorrect_count")
        batch_op.drop_column("correct_count")
        batch_op.drop_column("skill_evidence_count")
        batch_op.drop_column("confidence")
        batch_op.drop_column("mastery")
        batch_op.drop_column("source")
        batch_op.alter_column("proficiency", existing_type=sa.Float(), nullable=False)
