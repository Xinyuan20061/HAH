"""0025 motion unified V2 evidence: stage queue, evidence frames, feedback, budget reservation.

Baseline (verified by `alembic heads` before authoring):
    head = 0024_motion_voice_harness (down = 0023_user_voice_config).

Additive-only migration per contract section 7 / spec section 10:
  * motion_analysis_runs: request_fingerprint, result_version,
    effective_pipeline_version, cloud_review_mode, error_code.
  * new table motion_evidence_frames (generic evidence pool, reference-only).
  * new table motion_stage_tasks (persistent post-processing queue, CAS by version).
  * new table motion_user_feedback (per-feedback rows, never overwritten by result).
  * provider_invocations: atomic pre-request reservation columns + unique key.

No historical revision is modified. The legacy result_json base64 bloat is NOT
rewritten here; a batch purge of existing rows is handled by the runtime purge
worker (see app/services/ai_jobs.py purge expansion) and documented in the
delivery notes, so deployment never reads a large TEXT column in one pass.
"""

import sqlalchemy as sa
from alembic import op

revision = "0025_motion_unified_v2_evidence"
down_revision = "0024_motion_voice_harness"
branch_labels = None
depends_on = None


def upgrade():
    # --- run metadata: idempotency fingerprint, result version, cloud mode -------
    op.add_column(
        "motion_analysis_runs",
        sa.Column("request_fingerprint", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "ix_motion_analysis_runs_request_fingerprint",
        "motion_analysis_runs",
        ["request_fingerprint"],
    )
    op.add_column(
        "motion_analysis_runs",
        sa.Column("result_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "motion_analysis_runs",
        sa.Column("effective_pipeline_version", sa.String(length=60), nullable=True),
    )
    op.add_column(
        "motion_analysis_runs",
        sa.Column(
            "cloud_review_mode",
            sa.String(length=30),
            nullable=False,
            server_default="off",
        ),
    )
    op.add_column(
        "motion_analysis_runs",
        sa.Column("error_code", sa.String(length=80), nullable=True),
    )

    # --- generic evidence pool (reference-only, expires) ---------------------------
    op.create_table(
        "motion_evidence_frames",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("motion_analysis_runs.id"),
            nullable=False,
        ),
        sa.Column("frame_id", sa.String(length=80), nullable=False),
        sa.Column("timestamp_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("preview_asset_id", sa.String(length=128), nullable=True),
        sa.Column("subject_id", sa.String(length=80), nullable=True),
        sa.Column("observation_json", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("run_id", "frame_id", name="uq_motion_evidence_frame"),
    )
    op.create_index(
        "ix_motion_evidence_frames_run_id",
        "motion_evidence_frames",
        ["run_id"],
    )
    op.create_index(
        "ix_motion_evidence_frames_expires_at",
        "motion_evidence_frames",
        ["expires_at"],
    )

    # --- persistent post-processing stage queue (compare-and-set by version) ------
    op.create_table(
        "motion_stage_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("motion_analysis_runs.id"),
            nullable=False,
        ),
        sa.Column("stage", sa.String(length=40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("lease_token", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "run_id", "stage", "version", name="uq_motion_stage_task"
        ),
    )
    op.create_index(
        "ix_motion_stage_tasks_run_id",
        "motion_stage_tasks",
        ["run_id"],
    )
    op.create_index(
        "ix_motion_stage_tasks_status",
        "motion_stage_tasks",
        ["status"],
    )

    # --- per-feedback rows (never overwritten by result snapshot) -------------------
    op.create_table(
        "motion_user_feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("motion_analysis_runs.id"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("frame_id", sa.String(length=80), nullable=True),
        sa.Column("corrected_label", sa.String(length=120), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_motion_user_feedback_run_id",
        "motion_user_feedback",
        ["run_id"],
    )
    op.create_index(
        "ix_motion_user_feedback_kind",
        "motion_user_feedback",
        ["kind"],
    )

    # --- atomic pre-request budget reservation ------------------------------------
    # reservation_key = sha256(user_id|evidence_hash|operation|model|prompt_version
    #                          |policy_version|consent_mode). The unique index lets the
    # database reject a double-spend race before the external call; existing rows
    # keep reservation_key NULL (NULLs do not collide in SQLite/MySQL).
    op.add_column(
        "provider_invocations",
        sa.Column("reservation_key", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "uq_provider_invocations_reservation",
        "provider_invocations",
        ["reservation_key"],
        unique=True,
    )
    op.add_column(
        "provider_invocations",
        sa.Column("evidence_hash", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "provider_invocations",
        sa.Column("model_name", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "provider_invocations",
        sa.Column("prompt_version", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "provider_invocations",
        sa.Column("policy_version", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "provider_invocations",
        sa.Column("consent_mode", sa.String(length=30), nullable=True),
    )


def downgrade():
    op.drop_column("provider_invocations", "consent_mode")
    op.drop_column("provider_invocations", "policy_version")
    op.drop_column("provider_invocations", "prompt_version")
    op.drop_column("provider_invocations", "model_name")
    op.drop_column("provider_invocations", "evidence_hash")
    op.drop_index(
        "uq_provider_invocations_reservation", table_name="provider_invocations"
    )
    op.drop_column("provider_invocations", "reservation_key")

    op.drop_index("ix_motion_user_feedback_kind", table_name="motion_user_feedback")
    op.drop_index("ix_motion_user_feedback_run_id", table_name="motion_user_feedback")
    op.drop_table("motion_user_feedback")

    op.drop_index("ix_motion_stage_tasks_status", table_name="motion_stage_tasks")
    op.drop_index("ix_motion_stage_tasks_run_id", table_name="motion_stage_tasks")
    op.drop_table("motion_stage_tasks")

    op.drop_index(
        "ix_motion_evidence_frames_expires_at", table_name="motion_evidence_frames"
    )
    op.drop_index(
        "ix_motion_evidence_frames_run_id", table_name="motion_evidence_frames"
    )
    op.drop_table("motion_evidence_frames")

    op.drop_column("motion_analysis_runs", "error_code")
    op.drop_column("motion_analysis_runs", "cloud_review_mode")
    op.drop_column("motion_analysis_runs", "effective_pipeline_version")
    op.drop_column("motion_analysis_runs", "result_version")
    op.drop_index(
        "ix_motion_analysis_runs_request_fingerprint",
        table_name="motion_analysis_runs",
    )
    op.drop_column("motion_analysis_runs", "request_fingerprint")
