"""competition architecture: CloudBase media refs + database AI job queue + local worker registry
Revision ID: 0007_competition_cloud_worker
Revises: 0006_product_safety
"""

from alembic import op
import sqlalchemy as sa

revision = "0007_competition_cloud_worker"
down_revision = "0006_product_safety"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("media_assets") as b:
        b.add_column(
            sa.Column(
                "storage_backend", sa.String(30), nullable=False, server_default="local"
            )
        )
        b.add_column(
            sa.Column(
                "cloud_file_id", sa.String(700), nullable=False, server_default=""
            )
        )
        b.add_column(sa.Column("source_url", sa.Text(), nullable=True))
        b.add_column(sa.Column("source_url_expires_at", sa.DateTime(), nullable=True))
        b.create_index("ix_media_assets_storage_backend", ["storage_backend"])
        b.create_index("ix_media_assets_cloud_file_id", ["cloud_file_id"])

    op.execute(
        sa.text("UPDATE media_assets SET source_url = '' WHERE source_url IS NULL")
    )
    with op.batch_alter_table("media_assets") as b:
        b.alter_column("source_url", existing_type=sa.Text(), nullable=False)

    op.create_table(
        "ai_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "media_asset_id",
            sa.Integer(),
            sa.ForeignKey("media_assets.id"),
            nullable=True,
        ),
        sa.Column("job_type", sa.String(40), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("error_code", sa.String(80), nullable=False, server_default=""),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("worker_id", sa.String(120), nullable=False, server_default=""),
        sa.Column("lease_token", sa.String(80), nullable=False, server_default=""),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    for c in [
        "user_id",
        "media_asset_id",
        "job_type",
        "status",
        "priority",
        "worker_id",
        "lease_token",
        "lease_expires_at",
    ]:
        op.create_index(f"ix_ai_jobs_{c}", "ai_jobs", [c])

    op.create_table(
        "ai_worker_nodes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("worker_id", sa.String(120), nullable=False),
        sa.Column(
            "name",
            sa.String(120),
            nullable=False,
            server_default="HealthMate Local Worker",
        ),
        sa.Column("version", sa.String(40), nullable=False, server_default="1.0.0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="online"),
        sa.Column("gpu_name", sa.String(160), nullable=False, server_default=""),
        sa.Column("capabilities_json", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("worker_id", name="uq_ai_worker_nodes_worker_id"),
    )
    op.create_index(
        "ix_ai_worker_nodes_worker_id", "ai_worker_nodes", ["worker_id"], unique=True
    )
    op.create_index("ix_ai_worker_nodes_status", "ai_worker_nodes", ["status"])
    op.create_index(
        "ix_ai_worker_nodes_last_seen_at", "ai_worker_nodes", ["last_seen_at"]
    )


def downgrade():
    op.drop_table("ai_worker_nodes")
    op.drop_table("ai_jobs")
    with op.batch_alter_table("media_assets") as b:
        b.drop_index("ix_media_assets_cloud_file_id")
        b.drop_index("ix_media_assets_storage_backend")
        b.drop_column("source_url_expires_at")
        b.drop_column("source_url")
        b.drop_column("cloud_file_id")
        b.drop_column("storage_backend")
