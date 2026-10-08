"""Track Android uploads separately from committed media assets."""

from alembic import op
import sqlalchemy as sa


revision = "0043_mobile_media_upload_sessions"
down_revision = "0042_retryable_cloud_media_deletions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mobile_media_upload_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("media_asset_id", sa.Integer(), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("staging_key", sa.String(700), nullable=True),
        sa.Column("purpose", sa.String(40), nullable=False),
        sa.Column("original_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("media_type", sa.String(20), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("cleanup_after", sa.DateTime(), nullable=False),
        sa.Column("cleanup_completed_at", sa.DateTime(), nullable=True),
        sa.Column("cleanup_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error_code", sa.String(80), nullable=True),
        sa.Column("ready_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["media_asset_id"], ["media_assets.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint(
            "user_id", "request_id", name="uq_mobile_upload_user_request"
        ),
        sa.UniqueConstraint("media_asset_id", name="uq_mobile_upload_media_asset"),
        sa.UniqueConstraint("staging_key", name="uq_mobile_upload_staging_key"),
    )
    op.create_index(
        "ix_mobile_media_upload_sessions_user_id",
        "mobile_media_upload_sessions",
        ["user_id"],
    )
    op.create_index(
        "ix_mobile_media_upload_sessions_media_asset_id",
        "mobile_media_upload_sessions",
        ["media_asset_id"],
    )
    op.create_index(
        "ix_mobile_media_upload_sessions_status",
        "mobile_media_upload_sessions",
        ["status"],
    )
    op.create_index(
        "ix_mobile_media_upload_sessions_expires_at",
        "mobile_media_upload_sessions",
        ["expires_at"],
    )
    op.create_index(
        "ix_mobile_media_upload_sessions_cleanup_after",
        "mobile_media_upload_sessions",
        ["cleanup_after"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_mobile_media_upload_sessions_cleanup_after",
        table_name="mobile_media_upload_sessions",
    )
    op.drop_index(
        "ix_mobile_media_upload_sessions_expires_at",
        table_name="mobile_media_upload_sessions",
    )
    op.drop_index(
        "ix_mobile_media_upload_sessions_status",
        table_name="mobile_media_upload_sessions",
    )
    op.drop_index(
        "ix_mobile_media_upload_sessions_media_asset_id",
        table_name="mobile_media_upload_sessions",
    )
    op.drop_index(
        "ix_mobile_media_upload_sessions_user_id",
        table_name="mobile_media_upload_sessions",
    )
    op.drop_table("mobile_media_upload_sessions")
