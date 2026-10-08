"""Make motion label confirmations safe to retry."""

import sqlalchemy as sa
from alembic import op

revision = "0044_motion_feedback_idempotency"
down_revision = "0043_mobile_media_upload_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "motion_user_feedback",
        sa.Column("idempotency_key", sa.String(length=120), nullable=True),
    )
    op.create_index(
        "uq_motion_user_feedback_request",
        "motion_user_feedback",
        ["run_id", "user_id", "idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_motion_user_feedback_request", table_name="motion_user_feedback"
    )
    op.drop_column("motion_user_feedback", "idempotency_key")
