"""food correction loop, agent action registry, evaluation and safety
Revision ID: 0006_product_safety
Revises: 0005_health_intelligence
"""

from alembic import op
import sqlalchemy as sa

revision = "0006_product_safety"
down_revision = "0005_health_intelligence"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("diet_records") as b:
        b.add_column(
            sa.Column("portion", sa.String(120), nullable=False, server_default="")
        )
        b.add_column(
            sa.Column(
                "cooking_method", sa.String(120), nullable=False, server_default=""
            )
        )
        b.add_column(
            sa.Column("weight_g", sa.Float(), nullable=False, server_default="0")
        )
        b.add_column(sa.Column("fiber", sa.Float(), nullable=False, server_default="0"))
        b.add_column(sa.Column("vision_analysis_id", sa.Integer(), nullable=True))
        b.create_index("ix_diet_records_vision_analysis_id", ["vision_analysis_id"])
    op.create_table(
        "food_analysis_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("image_sha256", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("initial_json", sa.Text(), nullable=False),
        sa.Column("corrected_json", sa.Text(), nullable=False),
        sa.Column("correction_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column(
            "finalized_record_id",
            sa.Integer(),
            sa.ForeignKey("diet_records.id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    for c in ["user_id", "image_sha256", "status", "finalized_record_id"]:
        op.create_index(f"ix_food_analysis_sessions_{c}", "food_analysis_sessions", [c])
    op.create_table(
        "food_analysis_corrections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "analysis_id",
            sa.Integer(),
            sa.ForeignKey("food_analysis_sessions.id"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("corrected_json", sa.Text(), nullable=False),
        sa.Column("changed_fields_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    for c in ["analysis_id", "user_id"]:
        op.create_index(
            f"ix_food_analysis_corrections_{c}", "food_analysis_corrections", [c]
        )
    op.create_table(
        "agent_action_audits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "run_id", sa.Integer(), sa.ForeignKey("health_agent_runs.id"), nullable=True
        ),
        sa.Column("action_key", sa.String(80), nullable=False),
        sa.Column("risk_level", sa.String(20), nullable=False),
        sa.Column("requires_confirmation", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("input_json", sa.Text(), nullable=False),
        sa.Column("output_json", sa.Text(), nullable=False),
        sa.Column("block_reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    for c in ["user_id", "run_id", "action_key", "risk_level", "status"]:
        op.create_index(f"ix_agent_action_audits_{c}", "agent_action_audits", [c])
    op.create_table(
        "evaluation_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("metric_name", sa.String(100), nullable=False),
        sa.Column("metric_value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(30), nullable=False),
        sa.Column("source", sa.String(60), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("meta_json", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    for c in ["user_id", "metric_name", "source", "success", "occurred_at"]:
        op.create_index(f"ix_evaluation_events_{c}", "evaluation_events", [c])
    op.create_table(
        "evaluation_benchmarks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("task_type", sa.String(60), nullable=False),
        sa.Column("metric_name", sa.String(100), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(30), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    for c in ["user_id", "task_type", "metric_name"]:
        op.create_index(f"ix_evaluation_benchmarks_{c}", "evaluation_benchmarks", [c])
    op.create_table(
        "safety_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("category", sa.String(60), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("matched_rule", sa.String(100), nullable=False),
        sa.Column("message_hash", sa.String(64), nullable=False),
        sa.Column("excerpt_redacted", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    for c in ["user_id", "category", "severity", "action"]:
        op.create_index(f"ix_safety_events_{c}", "safety_events", [c])
    op.create_table(
        "privacy_audits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("action", sa.String(60), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("detail_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    for c in ["user_id", "action", "status"]:
        op.create_index(f"ix_privacy_audits_{c}", "privacy_audits", [c])


def downgrade():
    for t in [
        "privacy_audits",
        "safety_events",
        "evaluation_benchmarks",
        "evaluation_events",
        "agent_action_audits",
        "food_analysis_corrections",
        "food_analysis_sessions",
    ]:
        op.drop_table(t)
    with op.batch_alter_table("diet_records") as b:
        b.drop_index("ix_diet_records_vision_analysis_id")
        for c in [
            "vision_analysis_id",
            "fiber",
            "weight_g",
            "cooking_method",
            "portion",
        ]:
            b.drop_column(c)
