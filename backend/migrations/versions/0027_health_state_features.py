"""0027 health state engine.

Baseline (verified with `alembic heads` before authoring):
    head = 0026_product_closure_and_agent_actions.

Capability plan §4 / §12. Additive-only:

  * ``health_state_features`` — one versioned, reproducible derived value per
    (user, feature, window, definition version), with the inputs' hash and the
    concrete evidence references;
  * ``health_state_snapshots`` — a frozen view whose hash identifies the state.

No historical revision is modified. No backfill: a state value is computed from
records on demand, never invented during a migration.
"""

import sqlalchemy as sa
from alembic import op

revision = "0027_health_state_features"
down_revision = "0026_product_closure_and_agent_actions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "health_state_features",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("feature_key", sa.String(length=80), nullable=False),
        sa.Column(
            "feature_version", sa.String(length=20), nullable=False, server_default="1.0.0"
        ),
        sa.Column("window_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("value_numeric", sa.Float(), nullable=True),
        sa.Column("value_text", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("unit", sa.String(length=20), nullable=False, server_default=""),
        sa.Column(
            "evidence_type", sa.String(length=20), nullable=False, server_default="observed"
        ),
        sa.Column(
            "confidence_level",
            sa.String(length=20),
            nullable=False,
            server_default="unavailable",
        ),
        sa.Column("observed_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_hash", sa.String(length=64), nullable=False, server_default=""),
        # No server_default on TEXT: MySQL forbids DEFAULT on TEXT/BLOB/JSON.
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("limitations_json", sa.Text(), nullable=False),
        sa.Column("valid_from", sa.DateTime(), nullable=False),
        sa.Column("valid_until", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "user_id",
            "feature_key",
            "window_days",
            "feature_version",
            name="uq_health_state_feature",
        ),
    )
    op.create_index(
        "ix_health_state_features_user_id", "health_state_features", ["user_id"]
    )
    op.create_index(
        "ix_health_state_features_feature_key",
        "health_state_features",
        ["feature_key"],
    )
    op.create_index(
        "ix_health_state_features_input_hash", "health_state_features", ["input_hash"]
    )

    op.create_table(
        "health_state_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "state_version", sa.String(length=20), nullable=False, server_default="1.0.0"
        ),
        sa.Column("as_of", sa.DateTime(), nullable=False),
        sa.Column("window_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("values_json", sa.Text(), nullable=False),
        sa.Column("constraints_json", sa.Text(), nullable=False),
        sa.Column("missingness_json", sa.Text(), nullable=False),
        sa.Column("active_actions_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_health_state_snapshots_user_id", "health_state_snapshots", ["user_id"]
    )
    op.create_index("ix_health_state_snapshots_as_of", "health_state_snapshots", ["as_of"])
    op.create_index(
        "ix_health_state_snapshots_snapshot_hash",
        "health_state_snapshots",
        ["snapshot_hash"],
    )


def downgrade():
    op.drop_index(
        "ix_health_state_snapshots_snapshot_hash", table_name="health_state_snapshots"
    )
    op.drop_index("ix_health_state_snapshots_as_of", table_name="health_state_snapshots")
    op.drop_index(
        "ix_health_state_snapshots_user_id", table_name="health_state_snapshots"
    )
    op.drop_table("health_state_snapshots")

    op.drop_index(
        "ix_health_state_features_input_hash", table_name="health_state_features"
    )
    op.drop_index(
        "ix_health_state_features_feature_key", table_name="health_state_features"
    )
    op.drop_index(
        "ix_health_state_features_user_id", table_name="health_state_features"
    )
    op.drop_table("health_state_features")
