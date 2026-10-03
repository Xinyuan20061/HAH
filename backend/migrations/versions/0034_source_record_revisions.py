"""add source revisions for policy evidence resolvers"""

from alembic import op
import sqlalchemy as sa


revision = "0034_source_record_revisions"
down_revision = "0033_policy_compile_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("exercise_records", "health_checkins", "plan_task_states"):
        op.add_column(table, sa.Column("version", sa.Integer(), nullable=False, server_default="1"))


def downgrade() -> None:
    for table in ("plan_task_states", "health_checkins", "exercise_records"):
        op.drop_column(table, "version")
