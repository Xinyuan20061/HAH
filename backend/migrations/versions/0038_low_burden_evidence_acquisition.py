"""low burden evidence acquisition sessions and versioned certificates"""

from alembic import op
import sqlalchemy as sa


revision = "0038_low_burden_evidence_acquisition"
down_revision = "0037_policy_decision_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "policy_observation_refs",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_table(
        "policy_acquisition_sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("episode_id", sa.String(64), sa.ForeignKey("policy_episodes.id"), nullable=False),
        sa.Column("contract_json", sa.Text(), nullable=False),
        sa.Column("contract_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("decision_state", sa.String(32), nullable=False, server_default="needs_evidence"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("consented_at", sa.DateTime(), nullable=False),
        sa.Column("budget_json", sa.Text(), nullable=False),
        sa.Column("episode_prompt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active_question_id", sa.String(64), nullable=True),
        sa.Column("latest_certificate_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "episode_id", name="uq_acq_session_user_episode"),
        sa.CheckConstraint("status IN ('active','paused','closed')", name="ck_acq_session_status"),
        sa.CheckConstraint("decision_state IN ('needs_evidence','sufficient','waiting_window','deferred','needs_repair','blocked','stopped')", name="ck_acq_session_decision_state"),
        sa.CheckConstraint("version > 0 AND episode_prompt_count >= 0", name="ck_acq_session_counters"),
    )
    op.create_index("ix_policy_acquisition_sessions_user_id", "policy_acquisition_sessions", ["user_id"])
    op.create_index("ix_policy_acquisition_sessions_episode_id", "policy_acquisition_sessions", ["episode_id"])
    op.create_index("ix_acq_sessions_user_status", "policy_acquisition_sessions", ["user_id", "status"])

    op.create_table(
        "policy_acquisition_questions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("session_id", sa.String(64), sa.ForeignKey("policy_acquisition_sessions.id"), nullable=False),
        sa.Column("target_key", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("endpoint", sa.String(24), nullable=False),
        sa.Column("slot", sa.Integer(), nullable=False),
        sa.Column("target_opportunity_id", sa.String(64), sa.ForeignKey("policy_execution_opportunities.id"), nullable=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("expected_episode_version", sa.Integer(), nullable=False),
        sa.Column("expected_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("expected_session_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="issued"),
        sa.Column("prompt_json", sa.Text(), nullable=False),
        sa.Column("estimated_cost_ms", sa.Integer(), nullable=False, server_default="3000"),
        sa.Column("issued_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("answered_at", sa.DateTime(), nullable=True),
        sa.Column("answer_json", sa.Text(), nullable=False),
        sa.Column("resulting_report_id", sa.String(64), nullable=True),
        sa.Column("resulting_observation_ref_id", sa.Integer(), nullable=True),
        sa.CheckConstraint("status IN ('issued','answered','unknown','declined','unavailable','timed_out','obsolete','cancelled')", name="ck_acq_question_status"),
        sa.CheckConstraint("slot >= 0 AND estimated_cost_ms >= 0", name="ck_acq_question_bounds"),
        sa.CheckConstraint("expected_episode_version > 0 AND expected_session_version > 0", name="ck_acq_question_versions"),
    )
    op.create_index("ix_policy_acquisition_questions_user_id", "policy_acquisition_questions", ["user_id"])
    op.create_index("ix_policy_acquisition_questions_session_id", "policy_acquisition_questions", ["session_id"])
    op.create_index("ix_acq_question_session_status", "policy_acquisition_questions", ["session_id", "status"])
    op.create_index("ix_acq_question_user_target", "policy_acquisition_questions", ["user_id", "target_key"])

    op.create_table(
        "policy_acquisition_commands",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.Column("method", sa.String(8), nullable=False),
        sa.Column("route_key", sa.String(200), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("resource_kind", sa.String(40), nullable=False),
        sa.Column("resource_id", sa.String(64), nullable=False),
        sa.Column("response_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_acq_command_user_key"),
        sa.CheckConstraint("method IN ('POST','PATCH','PUT','DELETE')", name="ck_acq_command_method"),
    )
    op.create_index("ix_policy_acquisition_commands_user_id", "policy_acquisition_commands", ["user_id"])

    op.create_table(
        "policy_acquisition_daily_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("prompt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("measured_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("user_id", "business_date", name="uq_acq_usage_user_date"),
        sa.CheckConstraint("prompt_count >= 0 AND estimated_ms >= 0 AND measured_ms >= 0 AND version > 0", name="ck_acq_usage_bounds"),
    )
    op.create_index("ix_policy_acquisition_daily_usage_user_id", "policy_acquisition_daily_usage", ["user_id"])

    op.create_table(
        "policy_acquisition_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("session_id", sa.String(64), sa.ForeignKey("policy_acquisition_sessions.id"), nullable=False),
        sa.Column("question_id", sa.String(64), sa.ForeignKey("policy_acquisition_questions.id"), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("reason_code", sa.String(80), nullable=False, server_default=""),
        sa.Column("elapsed_ms", sa.Integer(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("session_id", "sequence", name="uq_acq_event_sequence"),
        sa.CheckConstraint("sequence > 0 AND (elapsed_ms IS NULL OR elapsed_ms >= 0)", name="ck_acq_event_bounds"),
    )
    op.create_index("ix_policy_acquisition_events_user_id", "policy_acquisition_events", ["user_id"])
    op.create_index("ix_policy_acquisition_events_session_id", "policy_acquisition_events", ["session_id"])
    op.create_index("ix_acq_event_user_created", "policy_acquisition_events", ["user_id", "created_at"])

    op.create_table(
        "policy_decision_certificates",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("episode_id", sa.String(64), sa.ForeignKey("policy_episodes.id"), nullable=False),
        sa.Column("session_id", sa.String(64), sa.ForeignKey("policy_acquisition_sessions.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("endpoint", sa.String(24), nullable=False),
        sa.Column("label", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="valid"),
        sa.Column("contract_hash", sa.String(64), nullable=False),
        sa.Column("evidence_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("binding_json", sa.Text(), nullable=False),
        sa.Column("proof_json", sa.Text(), nullable=False),
        sa.Column("body_hash", sa.String(64), nullable=False),
        sa.Column("predecessor_id", sa.String(64), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("session_id", "revision", name="uq_acq_certificate_revision"),
        sa.CheckConstraint("revision > 0", name="ck_acq_certificate_revision"),
        sa.CheckConstraint("purpose IN ('execution_progress','execution_endpoint')", name="ck_acq_certificate_purpose"),
        sa.CheckConstraint("endpoint = 'execution'", name="ck_acq_certificate_endpoint"),
        sa.CheckConstraint("status IN ('valid','stale','revoked')", name="ck_acq_certificate_status"),
        sa.CheckConstraint("label IS NULL OR label IN (0,1)", name="ck_acq_certificate_label"),
    )
    op.create_index("ix_policy_decision_certificates_user_id", "policy_decision_certificates", ["user_id"])
    op.create_index("ix_policy_decision_certificates_episode_id", "policy_decision_certificates", ["episode_id"])
    op.create_index("ix_policy_decision_certificates_session_id", "policy_decision_certificates", ["session_id"])
    op.create_index("ix_acq_certificate_user_episode", "policy_decision_certificates", ["user_id", "episode_id", "status"])

    op.create_table(
        "policy_certificate_dependencies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("certificate_id", sa.String(64), sa.ForeignKey("policy_decision_certificates.id"), nullable=False),
        sa.Column("dependency_kind", sa.String(32), nullable=False),
        sa.Column("source_type", sa.String(48), nullable=False, server_default=""),
        sa.Column("source_id", sa.String(96), nullable=False, server_default=""),
        sa.Column("source_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metric_version", sa.String(40), nullable=False, server_default=""),
        sa.Column("value_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("observation_ref_id", sa.Integer(), nullable=True),
        sa.Column("observation_revision", sa.Integer(), nullable=True),
        sa.Column("opportunity_id", sa.String(64), nullable=True),
        sa.Column("current_report_id", sa.String(64), nullable=True),
        sa.UniqueConstraint("certificate_id", "dependency_kind", "source_type", "source_id", "metric_version", name="uq_acq_dependency_identity"),
        sa.CheckConstraint("source_revision >= 0", name="ck_acq_dependency_source_revision"),
        sa.CheckConstraint("observation_revision IS NULL OR observation_revision > 0", name="ck_acq_dependency_observation_revision"),
    )
    op.create_index("ix_policy_certificate_dependencies_user_id", "policy_certificate_dependencies", ["user_id"])
    op.create_index("ix_policy_certificate_dependencies_certificate_id", "policy_certificate_dependencies", ["certificate_id"])
    op.create_index("ix_acq_dependency_source", "policy_certificate_dependencies", ["user_id", "source_type", "source_id"])

    op.create_table(
        "policy_evidence_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("observation_ref_id", sa.Integer(), sa.ForeignKey("policy_observation_refs.id"), nullable=False),
        sa.Column("observation_revision", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(48), nullable=False),
        sa.Column("source_id", sa.String(96), nullable=False),
        sa.Column("source_revision", sa.Integer(), nullable=False),
        sa.Column("metric_version", sa.String(40), nullable=False),
        sa.Column("endpoint", sa.String(24), nullable=False),
        sa.Column("slot", sa.Integer(), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("value_hash", sa.String(64), nullable=False),
        sa.Column("confirmed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "observation_ref_id", "observation_revision", name="uq_acq_evidence_revision"),
        sa.CheckConstraint("observation_revision > 0 AND source_revision > 0 AND slot >= 0", name="ck_acq_evidence_revision_bounds"),
    )
    op.create_index("ix_policy_evidence_revisions_user_id", "policy_evidence_revisions", ["user_id"])
    op.create_index("ix_policy_evidence_revisions_observation_ref_id", "policy_evidence_revisions", ["observation_ref_id"])
    op.create_index("ix_acq_evidence_source", "policy_evidence_revisions", ["user_id", "source_type", "source_id"])

    op.create_table(
        "policy_acquisition_fences",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("generation >= 0", name="ck_acq_fence_generation"),
    )


def downgrade() -> None:
    op.drop_table("policy_acquisition_fences")
    op.drop_table("policy_evidence_revisions")
    op.drop_table("policy_certificate_dependencies")
    op.drop_table("policy_decision_certificates")
    op.drop_table("policy_acquisition_events")
    op.drop_table("policy_acquisition_daily_usage")
    op.drop_table("policy_acquisition_commands")
    op.drop_table("policy_acquisition_questions")
    op.drop_table("policy_acquisition_sessions")
    op.drop_column("policy_observation_refs", "revision")
