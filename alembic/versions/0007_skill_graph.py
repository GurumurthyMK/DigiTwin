"""V2-A1 Skill Graph foundation: Skill -> Topic link + Question <-> Skill edges."""

revision = "0007_skill_graph"
down_revision = "0006_account"
branch_labels = None
depends_on = None

from alembic import op  # noqa: E402
import sqlalchemy as sa  # noqa: E402


def upgrade() -> None:
    # Batch mode: SQLite cannot ALTER constraints; batch copy-and-move
    # handles it, and renders as plain ALTER on PostgreSQL.
    with op.batch_alter_table("skills") as batch_op:
        batch_op.add_column(sa.Column("topic_id", sa.String(36), nullable=True))
        batch_op.create_foreign_key(
            "fk_skills_topic_id_topics", "topics", ["topic_id"], ["id"], ondelete="SET NULL"
        )
        batch_op.create_index("ix_skills_topic_id", ["topic_id"])
    op.create_table(
        "question_skills",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "question_id",
            sa.String(36),
            sa.ForeignKey("questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "skill_id",
            sa.String(36),
            sa.ForeignKey("skills.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint("question_id", "skill_id"),
    )
    op.create_index("ix_question_skills_question_id", "question_skills", ["question_id"])
    op.create_index("ix_question_skills_skill_id", "question_skills", ["skill_id"])


def downgrade() -> None:
    op.drop_index("ix_question_skills_skill_id", table_name="question_skills")
    op.drop_index("ix_question_skills_question_id", table_name="question_skills")
    op.drop_table("question_skills")
    with op.batch_alter_table("skills") as batch_op:
        batch_op.drop_index("ix_skills_topic_id")
        batch_op.drop_constraint("fk_skills_topic_id_topics", type_="foreignkey")
        batch_op.drop_column("topic_id")
