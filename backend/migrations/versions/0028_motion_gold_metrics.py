"""0028 motion gold metrics.

Baseline (verified with `alembic heads` before authoring):
    head = 0027_health_state_features.

Capability plan §5 / §12. Additive-only:

  * ``motion_gold_evaluations`` — per (run, exercise, evaluator version) the
    Gold-tier result: tier, reps/hold, per-rep findings, capability availability.

The tier is stored rather than inferred at read time so a capability that failed
its gate cannot later be presented as Gold just because the code shipped.
"""

import sqlalchemy as sa
from alembic import op

revision = "0028_motion_gold_metrics"
down_revision = "0027_health_state_features"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "motion_gold_evaluations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("motion_analysis_runs.id"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("exercise_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column(
            "evaluator_version", sa.String(length=40), nullable=False, server_default=""
        ),
        # gold | silver | unknown — never inferred from configuration at read time.
        sa.Column("tier", sa.String(length=16), nullable=False, server_default="unknown"),
        sa.Column("available", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reason_unavailable", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("reps", sa.Integer(), nullable=True),
        sa.Column("hold_seconds", sa.Float(), nullable=True),
        sa.Column("view_bucket", sa.String(length=30), nullable=False, server_default=""),
        # No server_default on TEXT: MySQL forbids DEFAULT on TEXT/BLOB/JSON.
        sa.Column("segments_json", sa.Text(), nullable=False),
        sa.Column("findings_json", sa.Text(), nullable=False),
        sa.Column("measurements_json", sa.Text(), nullable=False),
        sa.Column("gate_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "run_id", "exercise_id", "evaluator_version", name="uq_motion_gold_eval"
        ),
    )
    op.create_index(
        "ix_motion_gold_evaluations_run_id", "motion_gold_evaluations", ["run_id"]
    )
    op.create_index(
        "ix_motion_gold_evaluations_user_id", "motion_gold_evaluations", ["user_id"]
    )
    op.create_index(
        "ix_motion_gold_evaluations_exercise_id",
        "motion_gold_evaluations",
        ["exercise_id"],
    )


def downgrade():
    op.drop_index(
        "ix_motion_gold_evaluations_exercise_id", table_name="motion_gold_evaluations"
    )
    op.drop_index(
        "ix_motion_gold_evaluations_user_id", table_name="motion_gold_evaluations"
    )
    op.drop_index(
        "ix_motion_gold_evaluations_run_id", table_name="motion_gold_evaluations"
    )
    op.drop_table("motion_gold_evaluations")
