"""health timeline intelligence and agent foundation
Revision ID: 0005_health_intelligence
Revises: 0004_platform_foundation
"""

from alembic import op
import sqlalchemy as sa

revision = "0005_health_intelligence"
down_revision = "0004_platform_foundation"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "health_goal_adjustments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("metric", sa.String(40), nullable=False),
        sa.Column("window_days", sa.Integer(), nullable=False),
        sa.Column("observed_days", sa.Integer(), nullable=False),
        sa.Column("completion_rate", sa.Float(), nullable=False),
        sa.Column("previous_target", sa.Float(), nullable=False),
        sa.Column("recommended_target", sa.Float(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("facts_json", sa.Text(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("applied_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    for col in ["user_id", "metric", "status"]:
        op.create_index(
            f"ix_health_goal_adjustments_{col}", "health_goal_adjustments", [col]
        )
    op.create_table(
        "health_agent_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("intent", sa.String(60), nullable=False),
        sa.Column("user_message", sa.Text(), nullable=False),
        sa.Column("context_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    for col in ["user_id", "intent", "status"]:
        op.create_index(f"ix_health_agent_runs_{col}", "health_agent_runs", [col])
    op.create_table(
        "health_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("period_start", sa.String(10), nullable=False),
        sa.Column("period_end", sa.String(10), nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column(
            "source_run_id",
            sa.Integer(),
            sa.ForeignKey("health_agent_runs.id"),
            nullable=True,
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    for col in ["user_id", "period_start", "period_end", "source_run_id", "status"]:
        op.create_index(f"ix_health_plans_{col}", "health_plans", [col])
    op.create_table(
        "health_plan_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "plan_id", sa.Integer(), sa.ForeignKey("health_plans.id"), nullable=False
        ),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("planned_date", sa.String(10), nullable=False),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("target_json", sa.Text(), nullable=False),
        sa.Column("done", sa.Boolean(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    for col in ["plan_id", "user_id", "planned_date", "done"]:
        op.create_index(f"ix_health_plan_items_{col}", "health_plan_items", [col])
    op.create_table(
        "weekly_report_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("period_start", sa.String(10), nullable=False),
        sa.Column("period_end", sa.String(10), nullable=False),
        sa.Column("facts_version", sa.String(20), nullable=False),
        sa.Column("facts_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "user_id", "period_start", "period_end", name="uq_weekly_report_user_period"
        ),
    )
    for col in ["user_id", "period_start", "period_end"]:
        op.create_index(
            f"ix_weekly_report_snapshots_{col}", "weekly_report_snapshots", [col]
        )


def downgrade():
    op.drop_table("weekly_report_snapshots")
    op.drop_table("health_plan_items")
    op.drop_table("health_plans")
    op.drop_table("health_agent_runs")
    op.drop_table("health_goal_adjustments")
