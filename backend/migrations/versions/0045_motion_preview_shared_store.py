"""Persist short-lived motion previews across Cloud Run replicas."""

import sqlalchemy as sa
from sqlalchemy.dialects import mysql
from alembic import op

revision = "0045_motion_preview_shared_store"
down_revision = "0044_motion_feedback_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "motion_preview_objects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("motion_analysis_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("asset_id", sa.String(length=128), nullable=False),
        sa.Column("image_bytes", sa.LargeBinary().with_variant(mysql.MEDIUMBLOB(), "mysql"), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("run_id", "asset_id", name="uq_motion_preview_run_asset"),
    )
    op.create_index("ix_motion_preview_objects_run_id", "motion_preview_objects", ["run_id"])
    op.create_index("ix_motion_preview_objects_expires_at", "motion_preview_objects", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_motion_preview_objects_expires_at", table_name="motion_preview_objects")
    op.drop_index("ix_motion_preview_objects_run_id", table_name="motion_preview_objects")
    op.drop_table("motion_preview_objects")
