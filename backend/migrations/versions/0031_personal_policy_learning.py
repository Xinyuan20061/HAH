"""0031 personal policy learning engine.

Additive migration for the evidence-gated personal strategy learning framework.
It deliberately keeps the legacy action outcome tables intact; the new beliefs
are rebuilt from adjudications and never import legacy alpha/beta counts.
"""

import sqlalchemy as sa
from alembic import op
from datetime import datetime, timezone
import json


revision = "0031_personal_policy_learning"
down_revision = "0030_harness_memory_and_policy"
branch_labels = None
depends_on = None


def _index(name, table, cols):
    op.create_index(name, table, cols)


def upgrade():
    op.create_table(
        "policy_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("template_id", sa.String(80), nullable=False),
        sa.Column("template_version", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="approved"),
        sa.Column("protocol_json", sa.Text(), nullable=False),
        sa.Column("source_ids_json", sa.Text(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("template_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("template_id", "template_version", name="uq_policy_template_version"),
    )
    _index("ix_policy_templates_template_id", "policy_templates", ["template_id"])
    _index("ix_policy_templates_status", "policy_templates", ["status"])
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    templates = [
        ("session_duration", "burden", "burden-v1", "session_minutes", "delta", "decrease", -1.0, "paired_median"),
        ("record_complexity", "record_burden", "record-burden-v1", "record_item_count", "delta", "decrease", -1.0, "paired_median"),
        ("task_timing", "completion_fraction", "completion-v1", "scheduled_time", "absolute", "increase", 0.7, "fraction"),
    ]
    for template_id, metric_key, metric_version, changed_variable, mode, direction, target, aggregation in templates:
        payload = {"template_id": template_id, "template_version": "1.0.0", "metric_key": metric_key, "metric_version": metric_version, "changed_variable": changed_variable, "mode": mode, "direction": direction, "target": target, "ambiguity_band": 0.1 if mode == "delta" else 0.05, "expected_days": 7, "minimum_days": 5, "minimum_coverage": 0.7, "execution_target": 0.7, "aggregation": aggregation}
        op.execute(sa.table("policy_templates", sa.column("template_id"), sa.column("template_version"), sa.column("status"), sa.column("protocol_json"), sa.column("source_ids_json"), sa.column("reviewed_at"), sa.column("template_hash"), sa.column("created_at"), sa.column("updated_at")).insert().values(template_id=template_id, template_version="1.0.0", status="approved", protocol_json=json.dumps(payload, ensure_ascii=False, sort_keys=True), source_ids_json="[]", reviewed_at=now, template_hash="", created_at=now, updated_at=now))

    op.create_table(
        "personal_strategy_units",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("template_id", sa.String(80), nullable=False),
        sa.Column("template_version", sa.String(40), nullable=False),
        sa.Column("strategy_id", sa.String(100), nullable=False),
        sa.Column("protocol_version", sa.String(40), nullable=False),
        sa.Column("metric_version", sa.String(40), nullable=False),
        sa.Column("context_schema_version", sa.String(40), nullable=False),
        sa.Column("context_key", sa.String(64), nullable=False),
        sa.Column("protocol_json", sa.Text(), nullable=False),
        sa.Column("context_json", sa.Text(), nullable=False),
        sa.Column("state_snapshot_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("protocol_hash", sa.String(64), nullable=False),
        sa.Column("baseline_refs_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="compiled"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    _index("ix_personal_strategy_units_user_id", "personal_strategy_units", ["user_id"])
    _index("ix_personal_strategy_units_template_id", "personal_strategy_units", ["template_id"])
    _index("ix_personal_strategy_units_strategy_id", "personal_strategy_units", ["strategy_id"])
    _index("ix_personal_strategy_units_context_key", "personal_strategy_units", ["context_key"])
    _index("ix_personal_strategy_units_protocol_hash", "personal_strategy_units", ["protocol_hash"])
    _index("ix_personal_strategy_units_user_created", "personal_strategy_units", ["user_id", "created_at", "id"])

    op.create_table(
        "policy_episodes",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("unit_id", sa.String(64), sa.ForeignKey("personal_strategy_units.id"), nullable=False),
        sa.Column("legacy_experiment_id", sa.Integer(), nullable=True),
        sa.Column("decision_id", sa.String(64), nullable=True),
        sa.Column("learning_epoch", sa.String(128), nullable=False, server_default=""),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("start_at", sa.DateTime(), nullable=False),
        sa.Column("end_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("review_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("effective_adjudication_revision", sa.Integer(), nullable=True),
        sa.Column("protocol_snapshot_json", sa.Text(), nullable=False),
        sa.Column("execution_json", sa.Text(), nullable=False),
        sa.Column("context_key", sa.String(64), nullable=False),
        sa.Column("stop_reason", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("legacy_experiment_id", name="uq_policy_episode_legacy_experiment"),
    )
    _index("ix_policy_episodes_user_id", "policy_episodes", ["user_id"])
    _index("ix_policy_episodes_unit_id", "policy_episodes", ["unit_id"])
    _index("ix_policy_episodes_decision_id", "policy_episodes", ["decision_id"])
    _index("ix_policy_episodes_status", "policy_episodes", ["status"])
    _index("ix_policy_episodes_user_status", "policy_episodes", ["user_id", "status"])
    _index("ix_policy_episodes_context_key", "policy_episodes", ["context_key"])

    op.create_table(
        "policy_execution_opportunities",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("episode_id", sa.String(64), sa.ForeignKey("policy_episodes.id"), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(), nullable=False),
        sa.Column("slot", sa.Integer(), nullable=False),
        sa.Column("frozen_action_json", sa.Text(), nullable=False),
        sa.Column("current_report_id", sa.String(64), nullable=True),
        sa.UniqueConstraint("episode_id", "slot", name="uq_policy_opportunity_slot"),
    )
    _index("ix_policy_execution_opportunities_user_id", "policy_execution_opportunities", ["user_id"])
    _index("ix_policy_execution_opportunities_episode_id", "policy_execution_opportunities", ["episode_id"])
    _index("ix_policy_execution_opportunities_scheduled_at", "policy_execution_opportunities", ["scheduled_at"])

    op.create_table(
        "policy_reports",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("client_report_id", sa.String(96), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("episode_id", sa.String(64), sa.ForeignKey("policy_episodes.id"), nullable=False),
        sa.Column("opportunity_id", sa.String(64), sa.ForeignKey("policy_execution_opportunities.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("execution", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column("burden", sa.Float(), nullable=True),
        sa.Column("confounders_json", sa.Text(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("payload_hash", sa.String(64), nullable=False, server_default=""),
        sa.UniqueConstraint("user_id", "client_report_id", name="uq_policy_report_client_id"),
        sa.UniqueConstraint("opportunity_id", "revision", name="uq_policy_report_opportunity_revision"),
    )
    _index("ix_policy_reports_user_id", "policy_reports", ["user_id"])
    _index("ix_policy_reports_episode_id", "policy_reports", ["episode_id"])
    _index("ix_policy_reports_opportunity_id", "policy_reports", ["opportunity_id"])
    _index("ix_policy_reports_episode", "policy_reports", ["episode_id"])

    op.create_table(
        "policy_observation_refs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("episode_id", sa.String(64), sa.ForeignKey("policy_episodes.id"), nullable=False),
        sa.Column("endpoint", sa.String(24), nullable=False),
        sa.Column("slot", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(48), nullable=False),
        sa.Column("source_id", sa.String(96), nullable=False),
        sa.Column("source_revision", sa.Integer(), nullable=False),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("metric_version", sa.String(40), nullable=False),
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("valid", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("episode_id", "endpoint", "slot", name="uq_policy_observation_slot"),
    )
    _index("ix_policy_observation_refs_user_id", "policy_observation_refs", ["user_id"])
    _index("ix_policy_observation_refs_episode_id", "policy_observation_refs", ["episode_id"])
    _index("ix_policy_observation_refs_valid", "policy_observation_refs", ["valid"])
    _index("ix_policy_observation_source", "policy_observation_refs", ["user_id", "source_type", "source_id"])

    op.create_table(
        "policy_adjudications",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("episode_id", sa.String(64), sa.ForeignKey("policy_episodes.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("learning_epoch", sa.String(128), nullable=False, server_default=""),
        sa.Column("execution_label", sa.Integer(), nullable=True),
        sa.Column("support_label", sa.Integer(), nullable=True),
        sa.Column("availability_label", sa.Integer(), nullable=True),
        sa.Column("conclusion", sa.String(48), nullable=False),
        sa.Column("reasons_json", sa.Text(), nullable=False),
        sa.Column("evidence_refs_json", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("algorithm_version", sa.String(40), nullable=False),
        sa.Column("gate_version", sa.String(40), nullable=False),
        sa.Column("valid", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("stale", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("episode_id", "revision", name="uq_policy_adjudication_revision"),
    )
    _index("ix_policy_adjudications_user_id", "policy_adjudications", ["user_id"])
    _index("ix_policy_adjudications_episode_id", "policy_adjudications", ["episode_id"])
    _index("ix_policy_adjudications_valid", "policy_adjudications", ["valid"])
    _index("ix_policy_adjudications_stale", "policy_adjudications", ["stale"])

    op.create_table(
        "personal_policy_beliefs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("strategy_id", sa.String(100), nullable=False),
        sa.Column("protocol_version", sa.String(40), nullable=False),
        sa.Column("metric_version", sa.String(40), nullable=False),
        sa.Column("context_key", sa.String(64), nullable=False),
        sa.Column("endpoint", sa.String(24), nullable=False),
        sa.Column("learning_epoch", sa.String(128), nullable=False, server_default=""),
        sa.Column("alpha", sa.Float(), nullable=False, server_default="1"),
        sa.Column("beta", sa.Float(), nullable=False, server_default="1"),
        sa.Column("positive_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("negative_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("valid_until", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "user_id", "strategy_id", "protocol_version", "metric_version",
            "context_key", "endpoint", "learning_epoch", name="uq_personal_policy_belief_key",
        ),
    )
    _index("ix_personal_policy_beliefs_user_id", "personal_policy_beliefs", ["user_id"])
    _index("ix_personal_policy_beliefs_strategy_id", "personal_policy_beliefs", ["strategy_id"])
    _index("ix_personal_policy_beliefs_context_key", "personal_policy_beliefs", ["context_key"])

    op.create_table(
        "policy_decisions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("state_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("belief_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("candidate_json", sa.Text(), nullable=False),
        sa.Column("selected_id", sa.String(100), nullable=True),
        sa.Column("policy_mode", sa.String(40), nullable=False, server_default="deterministic_heuristic"),
        sa.Column("propensity_json", sa.Text(), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    _index("ix_policy_decisions_user_id", "policy_decisions", ["user_id"])
    _index("ix_policy_decisions_created_at", "policy_decisions", ["created_at"])

    op.create_table(
        "policy_outbox",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("ref_id", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "event_type", "ref_id", "revision", name="uq_policy_outbox_event"),
    )
    _index("ix_policy_outbox_user_id", "policy_outbox", ["user_id"])
    _index("ix_policy_outbox_status", "policy_outbox", ["status"])
    _index("ix_policy_outbox_status_retry", "policy_outbox", ["status", "next_retry_at"])

    op.create_table(
        "policy_active_slots",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("episode_kind", sa.String(40), nullable=False, server_default="policy"),
        sa.Column("episode_id", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )

    op.create_table(
        "policy_domain_generations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("domain", sa.String(64), nullable=False),
        sa.Column("source_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processed_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("user_id", "domain", name="uq_policy_domain_generation"),
    )
    _index("ix_policy_domain_generations_user_id", "policy_domain_generations", ["user_id"])

    op.create_table(
        "policy_learning_controls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("scope_key", sa.String(160), nullable=False),
        sa.Column("epoch_counter", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("learning_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("reset_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("user_id", "scope_key", name="uq_policy_learning_control"),
    )
    _index("ix_policy_learning_controls_user_id", "policy_learning_controls", ["user_id"])


def downgrade():
    for name, table in [
        ("ix_policy_learning_controls_user_id", "policy_learning_controls"),
        ("ix_policy_domain_generations_user_id", "policy_domain_generations"),
        ("ix_policy_outbox_status_retry", "policy_outbox"),
        ("ix_policy_outbox_status", "policy_outbox"),
        ("ix_policy_outbox_user_id", "policy_outbox"),
        ("ix_policy_decisions_created_at", "policy_decisions"),
        ("ix_policy_decisions_user_id", "policy_decisions"),
        ("ix_personal_policy_beliefs_context_key", "personal_policy_beliefs"),
        ("ix_personal_policy_beliefs_strategy_id", "personal_policy_beliefs"),
        ("ix_personal_policy_beliefs_user_id", "personal_policy_beliefs"),
        ("ix_policy_adjudications_stale", "policy_adjudications"),
        ("ix_policy_adjudications_valid", "policy_adjudications"),
        ("ix_policy_adjudications_episode_id", "policy_adjudications"),
        ("ix_policy_adjudications_user_id", "policy_adjudications"),
        ("ix_policy_observation_source", "policy_observation_refs"),
        ("ix_policy_observation_refs_valid", "policy_observation_refs"),
        ("ix_policy_observation_refs_episode_id", "policy_observation_refs"),
        ("ix_policy_observation_refs_user_id", "policy_observation_refs"),
        ("ix_policy_reports_episode", "policy_reports"),
        ("ix_policy_reports_opportunity_id", "policy_reports"),
        ("ix_policy_reports_episode_id", "policy_reports"),
        ("ix_policy_reports_user_id", "policy_reports"),
        ("ix_policy_execution_opportunities_scheduled_at", "policy_execution_opportunities"),
        ("ix_policy_execution_opportunities_episode_id", "policy_execution_opportunities"),
        ("ix_policy_execution_opportunities_user_id", "policy_execution_opportunities"),
        ("ix_policy_episodes_context_key", "policy_episodes"),
        ("ix_policy_episodes_user_status", "policy_episodes"),
        ("ix_policy_episodes_status", "policy_episodes"),
        ("ix_policy_episodes_decision_id", "policy_episodes"),
        ("ix_policy_episodes_unit_id", "policy_episodes"),
        ("ix_policy_episodes_user_id", "policy_episodes"),
        ("ix_personal_strategy_units_user_created", "personal_strategy_units"),
        ("ix_personal_strategy_units_protocol_hash", "personal_strategy_units"),
        ("ix_personal_strategy_units_context_key", "personal_strategy_units"),
        ("ix_personal_strategy_units_strategy_id", "personal_strategy_units"),
        ("ix_personal_strategy_units_template_id", "personal_strategy_units"),
        ("ix_personal_strategy_units_user_id", "personal_strategy_units"),
        ("ix_policy_templates_status", "policy_templates"),
        ("ix_policy_templates_template_id", "policy_templates"),
    ]:
        op.drop_index(name, table_name=table)
    for table in [
        "policy_learning_controls", "policy_domain_generations", "policy_active_slots",
        "policy_outbox", "policy_decisions", "personal_policy_beliefs",
        "policy_adjudications", "policy_observation_refs", "policy_reports",
        "policy_execution_opportunities", "policy_episodes", "personal_strategy_units",
        "policy_templates",
    ]:
        op.drop_table(table)
