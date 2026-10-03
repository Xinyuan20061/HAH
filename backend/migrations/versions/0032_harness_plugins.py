"""user-facing Harness capability plugin installations"""

from alembic import op
import sqlalchemy as sa

revision = "0032_harness_plugins"
down_revision = "0031_personal_policy_learning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "harness_plugin_installations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("plugin_id", sa.String(length=80), nullable=False),
        sa.Column("plugin_version", sa.String(length=40), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("enabled_at", sa.DateTime(), nullable=True),
        sa.Column("disabled_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "plugin_id", name="uq_harness_plugin_installation"),
        sa.Index("ix_harness_plugin_installations_user", "user_id"),
    )


def downgrade() -> None:
    op.drop_table("harness_plugin_installations")
