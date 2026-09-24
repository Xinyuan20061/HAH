"""platform foundation
Revision ID: 0004_platform_foundation
Revises: 0003_health_goals
"""

from alembic import op
import sqlalchemy as sa

revision = "0004_platform_foundation"
down_revision = "0003_health_goals"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "health_timeline_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("ref_type", sa.String(40), nullable=False),
        sa.Column("ref_id", sa.Integer(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_health_timeline_events_user_id", "health_timeline_events", ["user_id"]
    )
    op.create_index(
        "ix_health_timeline_events_event_type", "health_timeline_events", ["event_type"]
    )
    op.create_index(
        "ix_health_timeline_events_occurred_at",
        "health_timeline_events",
        ["occurred_at"],
    )
    op.create_table(
        "media_assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("original_name", sa.String(255), nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=False, unique=True),
        sa.Column("media_type", sa.String(20), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_media_assets_user_id", "media_assets", ["user_id"])
    op.create_index("ix_media_assets_storage_key", "media_assets", ["storage_key"])
    op.create_index("ix_media_assets_media_type", "media_assets", ["media_type"])
    op.create_table(
        "motion_analysis_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "media_asset_id",
            sa.Integer(),
            sa.ForeignKey("media_assets.id"),
            nullable=False,
        ),
        sa.Column("exercise_type", sa.String(40), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_motion_analysis_jobs_user_id", "motion_analysis_jobs", ["user_id"]
    )
    op.create_index(
        "ix_motion_analysis_jobs_media_asset_id",
        "motion_analysis_jobs",
        ["media_asset_id"],
    )
    op.create_index(
        "ix_motion_analysis_jobs_status", "motion_analysis_jobs", ["status"]
    )


def downgrade():
    op.drop_table("motion_analysis_jobs")
    op.drop_table("media_assets")
    op.drop_table("health_timeline_events")
