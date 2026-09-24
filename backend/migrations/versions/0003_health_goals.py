"""health goal settings"""

from alembic import op
import sqlalchemy as sa

revision = "0003_health_goals"
down_revision = "0002_product_features"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "health_goal_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("water_target", sa.Integer(), nullable=False, server_default="1800"),
        sa.Column("sleep_target", sa.Float(), nullable=False, server_default="8"),
        sa.Column("exercise_target", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("steps_target", sa.Integer(), nullable=False, server_default="8000"),
        sa.Column("protein_target", sa.Float(), nullable=False, server_default="90"),
        sa.Column(
            "calorie_target", sa.Integer(), nullable=False, server_default="2000"
        ),
        sa.Column(
            "weekly_checkin_target", sa.Integer(), nullable=False, server_default="5"
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index(
        "ix_health_goal_settings_user_id", "health_goal_settings", ["user_id"]
    )


def downgrade():
    op.drop_index("ix_health_goal_settings_user_id", table_name="health_goal_settings")
    op.drop_table("health_goal_settings")
