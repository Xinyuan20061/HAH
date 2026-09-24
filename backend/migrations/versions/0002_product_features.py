"""product features: user ai config, health checkin, plan state"""

from alembic import op
import sqlalchemy as sa

revision = "0002_product_features"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_ai_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("provider", sa.String(30), nullable=False, server_default="deepseek"),
        sa.Column(
            "base_url",
            sa.String(500),
            nullable=False,
            server_default="https://api.deepseek.com",
        ),
        sa.Column(
            "model", sa.String(120), nullable=False, server_default="deepseek-chat"
        ),
        sa.Column("api_key_encrypted", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_user_ai_configs_user_id", "user_ai_configs", ["user_id"])
    op.create_table(
        "health_checkins",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("record_date", sa.String(10), nullable=False),
        sa.Column("water_ml", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sleep_hours", sa.Float(), nullable=False, server_default="0"),
        sa.Column("weight_kg", sa.Float(), nullable=False, server_default="0"),
        sa.Column("steps", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mood", sa.String(20), nullable=False, server_default="normal"),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
        sa.UniqueConstraint(
            "user_id", "record_date", name="uq_health_checkin_user_date"
        ),
    )
    op.create_index("ix_health_checkins_user_id", "health_checkins", ["user_id"])
    op.create_index(
        "ix_health_checkins_record_date", "health_checkins", ["record_date"]
    )
    op.create_table(
        "plan_task_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("record_date", sa.String(10), nullable=False),
        sa.Column("task_key", sa.String(80), nullable=False),
        sa.Column("done", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
        sa.UniqueConstraint(
            "user_id", "record_date", "task_key", name="uq_plan_state_user_date_task"
        ),
    )
    op.create_index("ix_plan_task_states_user_id", "plan_task_states", ["user_id"])
    op.create_index(
        "ix_plan_task_states_record_date", "plan_task_states", ["record_date"]
    )


def downgrade():
    op.drop_table("plan_task_states")
    op.drop_table("health_checkins")
    op.drop_table("user_ai_configs")
