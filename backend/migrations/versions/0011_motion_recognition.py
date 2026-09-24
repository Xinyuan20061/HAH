"""Persist automatic motion-recognition provenance."""

from alembic import op
import sqlalchemy as sa


revision = "0011_motion_recognition"
down_revision = "0010_knowledge_rag"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "motion_scores",
        sa.Column("requested_exercise_type", sa.String(40), nullable=False, server_default=""),
    )
    op.add_column(
        "motion_scores",
        sa.Column("recognition_method", sa.String(80), nullable=False, server_default=""),
    )
    op.add_column(
        "motion_scores",
        sa.Column("recognition_confidence", sa.Float(), nullable=False, server_default="1"),
    )
    op.execute(
        "UPDATE motion_scores SET requested_exercise_type = exercise_type "
        "WHERE requested_exercise_type = ''"
    )
    op.create_index(
        "ix_motion_scores_requested_exercise_type",
        "motion_scores",
        ["requested_exercise_type"],
    )


def downgrade():
    op.drop_index(
        "ix_motion_scores_requested_exercise_type", table_name="motion_scores"
    )
    op.drop_column("motion_scores", "recognition_confidence")
    op.drop_column("motion_scores", "recognition_method")
    op.drop_column("motion_scores", "requested_exercise_type")
