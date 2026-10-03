"""Versioned Harness capability configuration and user-visible audit."""

from alembic import op
import sqlalchemy as sa


revision = "0036_harness_capability_contract"
down_revision = "0035_policy_context_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("harness_plugin_installations", sa.Column("config_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("harness_plugin_installations", sa.Column("reviewed_manifest_hash", sa.String(length=64), nullable=False, server_default=""))
    op.add_column("harness_plugin_installations", sa.Column("last_run_at", sa.DateTime(), nullable=True))
    op.add_column("harness_plugin_installations", sa.Column("last_error", sa.String(length=255), nullable=False, server_default=""))
    op.create_table(
        "harness_capability_audits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("plugin_id", sa.String(length=80), nullable=False),
        # Deliberately not an FK: deleting an installation must not erase the
        # consent/revocation audit trail.
        sa.Column("installation_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=True),
        sa.Column("request_hash", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("config_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("detail_json", sa.Text(), nullable=False),
        sa.Column("response_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_harness_audit_idem"),
        sa.Index("ix_harness_capability_audits_user_id", "user_id"),
        sa.Index("ix_harness_capability_audits_plugin_id", "plugin_id"),
        sa.Index("ix_harness_capability_audits_installation_id", "installation_id"),
        sa.Index("ix_harness_capability_audits_event_type", "event_type"),
        sa.Index("ix_harness_capability_audit_owner_plugin", "user_id", "plugin_id", "created_at"),
    )


def downgrade() -> None:
    op.drop_table("harness_capability_audits")
    for name in ("last_error", "last_run_at", "reviewed_manifest_hash", "config_version"):
        op.drop_column("harness_plugin_installations", name)
