"""0024 motion+voice harness: unified motion runs, feedback, provider ledger and voice budget.

Baseline: 0023_user_voice_config. One rollback-able migration covering the five
tables/fields required by HEALTHMATE_HARNESS_MOTION_VOICE_ENGINEERING_SPEC section 8.1,
plus the per-user voice provider switch required by section 5.
"""

import sqlalchemy as sa
from alembic import op

revision = "0024_motion_voice_harness"
down_revision = "0023_user_voice_config"
branch_labels = None
depends_on = None


def upgrade():
    # --- unified motion analysis runs -------------------------------------------------
    op.create_table(
        "motion_analysis_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "media_asset_id",
            sa.Integer(),
            sa.ForeignKey("media_assets.id"),
            nullable=False,
        ),
        sa.Column(
            "ai_job_id", sa.Integer(), sa.ForeignKey("ai_jobs.id"), nullable=True
        ),
        sa.Column(
            "parent_run_id",
            sa.Integer(),
            sa.ForeignKey("motion_analysis_runs.id"),
            nullable=True,
        ),
        # Same-request unique key: same idempotency key + same asset returns the same task.
        sa.Column("dedupe_key", sa.String(128), nullable=True),
        sa.Column("requested_type", sa.String(30), nullable=False),
        sa.Column("pipeline_version", sa.String(60), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("consent_version", sa.String(40), nullable=False),
        sa.Column("model_versions_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_motion_analysis_runs_dedupe_key",
        "motion_analysis_runs",
        ["dedupe_key"],
        unique=True,
    )
    op.create_index(
        "ix_motion_analysis_runs_user_media",
        "motion_analysis_runs",
        ["user_id", "media_asset_id"],
    )
    op.create_index(
        "ix_motion_analysis_runs_status", "motion_analysis_runs", ["status"]
    )

    # --- structured result feedback (one per run; images live in short-term storage) --
    op.create_table(
        "motion_analysis_feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("motion_analysis_runs.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("schema_version", sa.String(30), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    # --- provider invocation ledger (desensitized only) ------------------------------
    op.create_table(
        "provider_invocations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("motion_analysis_runs.id"),
            nullable=True,
        ),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("operation", sa.String(60), nullable=False),
        sa.Column("request_fingerprint", sa.String(128), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("token_or_char_count", sa.Integer(), nullable=True),
        sa.Column("provider_request_id", sa.String(128), nullable=True),
        sa.Column("cost_estimate", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_provider_invocations_user_id", "provider_invocations", ["user_id"]
    )
    op.create_index(
        "ix_provider_invocations_fingerprint",
        "provider_invocations",
        ["request_fingerprint"],
    )
    op.create_index(
        "ix_provider_invocations_created_at",
        "provider_invocations",
        ["created_at"],
    )

    # --- one-time real connectivity checks ("verify once, then stop") ----------------
    op.create_table(
        "provider_connection_checks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("config_fingerprint", sa.String(128), nullable=False),
        sa.Column("direction", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("provider_request_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        # One row per (fingerprint, direction, status): a successful check blocks
        # further real calls for the same config fingerprint (connected -> stop testing).
        sa.UniqueConstraint(
            "config_fingerprint",
            "direction",
            "status",
            name="uq_provider_conn_check",
        ),
    )

    # --- local daily voice budget (never masquerades as Tencent console quota) -------
    op.create_table(
        "voice_usage_daily",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("asr_count", sa.Integer(), nullable=False),
        sa.Column("tts_chars", sa.Integer(), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "user_id", "usage_date", "provider", name="uq_voice_usage_daily"
        ),
    )

    # --- per-user voice provider switch (section 5 of the spec) ----------------------
    op.add_column(
        "user_ai_configs",
        sa.Column(
            "voice_provider",
            sa.String(30),
            nullable=False,
            server_default="off",
        ),
    )
    op.add_column(
        "user_ai_configs",
        sa.Column("voice_preferences_json", sa.Text(), nullable=True),
    )


def downgrade():
    op.drop_column("user_ai_configs", "voice_preferences_json")
    op.drop_column("user_ai_configs", "voice_provider")
    op.drop_table("voice_usage_daily")
    op.drop_table("provider_connection_checks")
    op.drop_table("provider_invocations")
    op.drop_table("motion_analysis_feedback")
    op.drop_index("ix_motion_analysis_runs_status", table_name="motion_analysis_runs")
    op.drop_index(
        "ix_motion_analysis_runs_user_media", table_name="motion_analysis_runs"
    )
    op.drop_index(
        "ix_motion_analysis_runs_dedupe_key", table_name="motion_analysis_runs"
    )
    op.drop_table("motion_analysis_runs")
