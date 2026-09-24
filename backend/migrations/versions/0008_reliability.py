"""Queue idempotency/backoff; preserve old rows and UTC DATETIME schemas."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import MEDIUMTEXT

revision = "0008_reliability"
down_revision = "0007_competition_cloud_worker"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("media_assets") as b:
        b.alter_column(
            "storage_key",
            existing_type=sa.String(500),
            type_=sa.String(700),
            existing_nullable=False,
        )
    with op.batch_alter_table("ai_jobs") as b:
        b.add_column(sa.Column("dedupe_key", sa.String(64), nullable=True))
        b.add_column(sa.Column("claim_request_id", sa.String(80), nullable=True))
        b.add_column(sa.Column("next_attempt_at", sa.DateTime(), nullable=True))
        b.create_unique_constraint("uq_ai_jobs_dedupe_key", ["dedupe_key"])
        b.create_unique_constraint("uq_ai_jobs_claim_request_id", ["claim_request_id"])
        b.create_index(
            "ix_ai_jobs_dispatch", ["status", "job_type", "priority", "next_attempt_at"]
        )
        if op.get_bind().dialect.name == "mysql":
            b.alter_column(
                "result_json",
                existing_type=sa.Text(),
                type_=MEDIUMTEXT(),
                existing_nullable=False,
                server_default=None,
            )
    # Upgrade queues that were permanently failed solely because their signed URL expired.
    op.execute(
        sa.text(
            "UPDATE ai_jobs SET status='waiting_source_refresh', finished_at=NULL, "
            "lease_token='', worker_id='', lease_expires_at=NULL "
            "WHERE status='failed' AND error_code IN ('media_url_expired', 'media_url_missing')"
        )
    )


def downgrade():
    with op.batch_alter_table("ai_jobs") as b:
        b.drop_index("ix_ai_jobs_dispatch")
        b.drop_constraint("uq_ai_jobs_claim_request_id", type_="unique")
        b.drop_constraint("uq_ai_jobs_dedupe_key", type_="unique")
        b.drop_column("next_attempt_at")
        b.drop_column("claim_request_id")
        b.drop_column("dedupe_key")
    # Keep expanded media identity/result types: shrinking them can lose user data.
