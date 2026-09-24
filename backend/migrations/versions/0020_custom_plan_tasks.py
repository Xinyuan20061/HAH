"""Add custom plan tasks support (title/description/task_type).

Revision ID: 0020_custom_plan_tasks
Revises: 0019_agent_micro_experiments
"""

from alembic import op
import sqlalchemy as sa


revision = "0020_custom_plan_tasks"
down_revision = "0019_agent_micro_experiments"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "plan_task_states",
        sa.Column("title", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "plan_task_states",
        sa.Column("description", sa.String(length=300), nullable=True),
    )
    op.add_column(
        "plan_task_states",
        sa.Column(
            "task_type",
            sa.String(length=20),
            nullable=False,
            server_default="system",
        ),
    )


def downgrade():
    op.drop_column("plan_task_states", "task_type")
    op.drop_column("plan_task_states", "description")
    op.drop_column("plan_task_states", "title")
