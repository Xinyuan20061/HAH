"""Retain encrypted media references for bounded deletion retries."""

from alembic import op
import sqlalchemy as sa


revision = "0042_retryable_cloud_media_deletions"
down_revision = "0041_mobile_identity_and_link_codes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "media_deletion_tasks",
        sa.Column("encrypted_storage_reference", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("media_deletion_tasks", "encrypted_storage_reference")
