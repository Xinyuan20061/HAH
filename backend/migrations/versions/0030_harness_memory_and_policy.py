"""0030 harness memory and policy.

Baseline (verified with `alembic heads` before authoring):
    head = 0029_food_references_and_priors.

Capability plan §8.5 / §9.4 / §9.5 / §12. Additive-only:

  * ``user_preference_memory`` — structured long-term preferences, replacing the
    "last 3 conversation summaries" as the primary memory;
  * ``action_policy_stats`` — explainable Beta posteriors per action family/variant;
  * ``action_outcomes`` — the observed result of each executed action/experiment.

None of these stores prompts, private reasoning or model weights.
"""

import sqlalchemy as sa
from alembic import op

revision = "0030_harness_memory_and_policy"
down_revision = "0029_food_references_and_priors"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_preference_memory",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("value", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("source", sa.String(length=30), nullable=False, server_default="explicit"),
        sa.Column("evidence_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "confidence_level", sa.String(length=20), nullable=False, server_default="high"
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("last_confirmed_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "key", name="uq_user_preference_memory"),
    )
    op.create_index(
        "ix_user_preference_memory_user_id", "user_preference_memory", ["user_id"]
    )

    op.create_table(
        "action_policy_stats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action_family", sa.String(length=80), nullable=False),
        sa.Column("variant", sa.String(length=40), nullable=False, server_default="default"),
        sa.Column("offered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("accepted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("helpful", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("inaccurate", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("alpha", sa.Float(), nullable=False, server_default="1"),
        sa.Column("beta", sa.Float(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "user_id", "action_family", "variant", name="uq_action_policy_stat"
        ),
    )
    op.create_index("ix_action_policy_stats_user_id", "action_policy_stats", ["user_id"])
    op.create_index(
        "ix_action_policy_stats_action_family", "action_policy_stats", ["action_family"]
    )

    op.create_table(
        "action_outcomes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action_key", sa.String(length=80), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False, server_default="proposal"),
        sa.Column("source_id", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("decision_id", sa.String(length=64), nullable=True),
        sa.Column("variant", sa.String(length=40), nullable=False, server_default="default"),
        sa.Column("result", sa.String(length=40), nullable=False, server_default="unknown"),
        sa.Column(
            "conclusion",
            sa.String(length=40),
            nullable=False,
            server_default="insufficient_data",
        ),
        sa.Column("user_feedback", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("observed_json", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_action_outcomes_user_id", "action_outcomes", ["user_id"])
    op.create_index("ix_action_outcomes_action_key", "action_outcomes", ["action_key"])
    op.create_index("ix_action_outcomes_source", "action_outcomes", ["source"])
    op.create_index("ix_action_outcomes_result", "action_outcomes", ["result"])
    op.create_index("ix_action_outcomes_decision_id", "action_outcomes", ["decision_id"])
    op.create_index("ix_action_outcomes_observed_at", "action_outcomes", ["observed_at"])


def downgrade():
    op.drop_index("ix_action_outcomes_observed_at", table_name="action_outcomes")
    op.drop_index("ix_action_outcomes_decision_id", table_name="action_outcomes")
    op.drop_index("ix_action_outcomes_result", table_name="action_outcomes")
    op.drop_index("ix_action_outcomes_source", table_name="action_outcomes")
    op.drop_index("ix_action_outcomes_action_key", table_name="action_outcomes")
    op.drop_index("ix_action_outcomes_user_id", table_name="action_outcomes")
    op.drop_table("action_outcomes")

    op.drop_index("ix_action_policy_stats_action_family", table_name="action_policy_stats")
    op.drop_index("ix_action_policy_stats_user_id", table_name="action_policy_stats")
    op.drop_table("action_policy_stats")

    op.drop_index(
        "ix_user_preference_memory_user_id", table_name="user_preference_memory"
    )
    op.drop_table("user_preference_memory")
