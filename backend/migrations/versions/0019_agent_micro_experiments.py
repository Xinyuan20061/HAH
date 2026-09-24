"""Add user-confirmed N-of-1 micro experiments for Agent v4.

Revision ID: 0019_agent_micro_experiments
Revises: 0018_seed_knowledge_documents
"""

from alembic import op
import sqlalchemy as sa


revision = "0019_agent_micro_experiments"
down_revision = "0018_seed_knowledge_documents"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_micro_experiments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("insight_code", sa.String(length=60), nullable=False),
        sa.Column("variant", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=180), nullable=False),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        sa.Column("primary_metric", sa.String(length=60), nullable=False),
        sa.Column("start_date", sa.String(length=10), nullable=False),
        sa.Column("end_date", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("baseline_json", sa.Text(), nullable=False),
        sa.Column("target_json", sa.Text(), nullable=False),
        sa.Column("protocol_json", sa.Text(), nullable=False),
        sa.Column("outcome_json", sa.Text(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_agent_micro_experiments_user_id", "agent_micro_experiments", ["user_id"])
    op.create_index("ix_agent_micro_experiments_insight_code", "agent_micro_experiments", ["insight_code"])
    op.create_index("ix_agent_micro_experiments_primary_metric", "agent_micro_experiments", ["primary_metric"])
    op.create_index("ix_agent_micro_experiments_start_date", "agent_micro_experiments", ["start_date"])
    op.create_index("ix_agent_micro_experiments_end_date", "agent_micro_experiments", ["end_date"])
    op.create_index("ix_agent_micro_experiments_status", "agent_micro_experiments", ["status"])


def downgrade():
    op.drop_table("agent_micro_experiments")
